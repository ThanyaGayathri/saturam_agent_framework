"""What the OS registers: agents, workflows, and the executor registry.

**Every agent gets an explicit, stable ``id``.** ``MEMORY_SCOPE.md`` records that an agent with
neither ``id`` nor ``name`` gets ``generate_id_from_name`` over a uuid4 -- a fresh memory partition
on every process start. The consolidator's scopes are built from the ids listed here, so an
unstable id would silently orphan every partition the previous process wrote.

**The agents are built here from the skills' assets; no ``Agent`` exists to import.**
``skills/synonym_proposer.py`` and ``skills/join_path_explainer.py`` export the output schema and
the prompt/code drift checkers; the agents themselves were only ever constructed inside the K14
cookbooks. Two differences from those cookbooks: nothing here reads a bare ``os.environ[...]``
(which raises at import and would take ``/config`` down with it), and **no agent pins a model**.
Each is built with ``model=None`` and handed a provider by ``routing.route_agents`` (R8, G6):
the routing table says which tier serves it, the provider resolves the Azure deployment for
that tier at startup and again per run, and a small-tier request the deployment cannot take
escalates to the frontier before the call. ``AzureOpenAI`` reads ``AZURE_OPENAI_*`` in
``_get_client_params``, not in ``__init__``, so ``/config`` still lists every agent with no key
present. An agent with no row in the table refuses assembly, naming itself.

The agent **is** handed a memory provider (K1). No OS-level path does that -- ``memory_provider``
has no reference under ``egpt/os/`` -- so it is ``Agent(memory_provider=...)`` explicitly, built
through ``build_memory_provider_for``, the per-tenant entry point the gap report flagged as
orphaned. The consolidator builds its own provider from the same resolver, which is correct: it
is a separate process and shares no object with the API.

A provider alone is not enough. Without a tenant on the run context the memory hooks still no-op
(``agent/_managers.py:575``), which is why K1 also installs ``FixedTenantMiddleware`` in the
factory. Provider and tenant are two halves of one fix; either alone leaves memory silent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Union, cast

from egpt.agent import Agent, AgentFactory, RemoteAgent
from egpt.agent.protocol import AgentProtocol
from egpt.factory import RequestContext
from egpt.integrations.db.postgres import PostgresDb
from egpt.memory.config import build_memory_provider_for
from egpt.memory.provider import MemoryProvider
from egpt.registry.registry import Registry
from egpt.workflow import RemoteWorkflow, Workflow, WorkflowFactory
from egpt.workflow.step import Step
from egpt.workflow.types import OnError
from pydantic import BaseModel

from egpt_retail.app.settings import RetailSettings
from egpt_retail.executors.areas import detect_business_areas
from egpt_retail.executors.delivery import deliver_in_app
from egpt_retail.executors.gaps import check_spec_gaps
from egpt_retail.executors.gold import GOLD_EXECUTORS, materialise_gold
from egpt_retail.executors.kpi import assess_kpi_feasibility
from egpt_retail.executors.plan import derive_plan
from egpt_retail.executors.preflight import check_preflight
from egpt_retail.executors.provenance import record_provenance
from egpt_retail.executors.registry import assert_registry_valid
from egpt_retail.executors.warehouse import WAREHOUSE_EXECUTORS
from egpt_retail.memory.config import build_memory_resolver
from egpt_retail.playground.modes import PlaygroundMode, aresolve_mode_scope
from egpt_retail.routing.providers import (
    TierProviders,
    build_tier_providers,
    provider_for,
    route_agents,
)
from egpt_retail.routing.table import assert_every_agent_routed, tier_for
from egpt_retail.skills.join_path_explainer import JoinExplanation
from egpt_retail.skills.playground import PlaygroundAnswer
from egpt_retail.skills.synonym_proposer import SynonymProposalSet

__all__ = [
    "RegisteredAgent",
    "RegisteredWorkflow",
    "SYNONYM_PROPOSER_ID",
    "JOIN_PATH_EXPLAINER_ID",
    "PLAYGROUND_AGENT_ID",
    "PlaygroundModeInput",
    "LANDING_TO_SILVER_WORKFLOW_ID",
    "NIGHTLY_REFRESH_WORKFLOW_ID",
    "MEMORY_WRITING_AGENT_IDS",
    "build_agents",
    "build_playground_agent_factory",
    "build_workflows",
    "build_registry",
]

#: Exactly what ``EgptOS(agents=..., workflows=...)`` accepts. ``list`` is invariant, so the
#: builders declare the union rather than the concrete class.
RegisteredAgent = Union[Agent, RemoteAgent, AgentProtocol, AgentFactory]
RegisteredWorkflow = Union[Workflow, RemoteWorkflow, WorkflowFactory]

SYNONYM_PROPOSER_ID = "synonym_proposer"
#: Lane R's agent from K14's second half. Registered here (R8) rather than living only in its
#: cookbook, because the routing table needs one small-tier agent that really exists.
JOIN_PATH_EXPLAINER_ID = "join_path_explainer"
#: R11's playground agent. Built by an ``AgentFactory``, not a static ``Agent`` -- see
#: ``build_playground_agent_factory``.
PLAYGROUND_AGENT_ID = "playground"

#: R4's pipeline, kept registered for hand runs. Stable: stored workflows reference it by id.
LANDING_TO_SILVER_WORKFLOW_ID = "landing-to-silver"

#: R5's nightly chain: R4's six steps then ``materialise_gold``. The schedule
#: (``egpt-retail schedule-nightly``) targets this id, so it is the one that must not move.
NIGHTLY_REFRESH_WORKFLOW_ID = "nightly-refresh"

#: The agents whose memory partitions the consolidator drains. The two static agents: the
#: lifespan hands each one the memory provider, so each one writes a partition.
#: ``PLAYGROUND_AGENT_ID`` is deliberately absent -- ``memory_lifespan_for`` iterates the
#: objects ``build_agents`` returns, and for a factory that is the ``AgentFactory`` itself,
#: not the fresh ``Agent`` it builds per request inside ``build_playground_agent``. Wiring
#: memory into a per-request factory agent is real work R11 does not do in this package;
#: listing this id here without doing it would tell the consolidator to drain a partition
#: nothing ever writes.
MEMORY_WRITING_AGENT_IDS: List[str] = [SYNONYM_PROPOSER_ID, JOIN_PATH_EXPLAINER_ID]

#: R7's three (G4). Unlike R4's six, these are not wired into a standalone registered
#: workflow -- they exist to be composed into other packages' workflows (R10's RFQ chain,
#: eventually), and R7's own verification is a throwaway workflow in the cookbook.
R7_EXECUTORS = (check_preflight, record_provenance, deliver_in_app)

#: Lane K's two (K9, K11). Both are deterministic steps a workflow composes -- feasibility is
#: a set difference and area detection is a graph query -- and both shipped unreachable:
#: registered in no registry, called from nowhere, with docstrings claiming registration.
#: A name in EXECUTOR_REGISTRY.md that no registry holds is a contract nobody can resolve.
K_EXECUTORS = (assess_kpi_feasibility, detect_business_areas)
#: R9's two: the deterministic gap check and the plan view. Composed into the builder's
#: routes in P5; neither is a step of a registered workflow.
R9_EXECUTORS = (check_spec_gaps, derive_plan)

_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
_SYNONYM_PROPOSER_PROMPT = _SKILLS_DIR / "EXAMPLE.md"
_JOIN_PATH_EXPLAINER_PROMPT = _SKILLS_DIR / "join_path_explainer.md"
_PLAYGROUND_PROMPT = _SKILLS_DIR / "playground.md"


class PlaygroundModeInput(BaseModel):
    """``factory_input`` for the playground agent -- validated by ``EgptOS`` before
    ``build_playground_agent`` runs (``cookbook/05_agent_os/factories/agent/
    02_input_schema_factory.py`` is the pattern)."""

    mode: PlaygroundMode
    scope_id: Optional[str] = None


def build_memory_provider(settings: RetailSettings) -> Optional[MemoryProvider]:
    """This deployment's memory provider, or ``None`` when no customer slug is configured.

    Returns ``None`` rather than raising so the factory can report *every* startup problem at
    once through ``assert_memory_wired``; raising here would surface the first fault and hide
    the rest. An unset slug is still fatal -- just fatal one layer up, with company.
    """
    slug = (settings.retail_customer_slug or "").strip()
    if not slug:
        return None
    return build_memory_provider_for(build_memory_resolver(settings), tenant_id=slug)


def build_agents(settings: RetailSettings, db: PostgresDb) -> List[RegisteredAgent]:
    """The registered agents. Pure construction; nothing here calls a model or a database.

    No memory provider is attached here -- see ``factory.memory_lifespan_for``. Building one
    opens a database connection, and this runs at import.
    """
    synonym_proposer = Agent(
        id=SYNONYM_PROPOSER_ID,
        name=SYNONYM_PROPOSER_ID,
        description="Proposes synonyms and question patterns for one business concept (Lane K, K14).",
        instructions=_SYNONYM_PROPOSER_PROMPT.read_text(),
        output_schema=SynonymProposalSet,
        db=db,
    )
    join_path_explainer = Agent(
        id=JOIN_PATH_EXPLAINER_ID,
        name=JOIN_PATH_EXPLAINER_ID,
        description=(
            "Explains a join path the semantic layer found, or did not, without ever "
            "disagreeing with it (Lane R, K14)."
        ),
        instructions=_JOIN_PATH_EXPLAINER_PROMPT.read_text(),
        output_schema=JoinExplanation,
        db=db,
    )
    agents: List[RegisteredAgent] = [synonym_proposer, join_path_explainer]
    providers = build_tier_providers(settings)
    # R8: tier per agent, from the table; refuses an unrouted or model-pinning agent.
    route_agents(agents, providers)

    # The playground agent is built per request by an AgentFactory (R11), not statically
    # like the two above -- `route_agents` only ever sees the two static agents, so its own
    # tier is asserted here instead, at the same startup point, rather than only surfacing
    # the first time a request resolves an unrouted agent id.
    factory = build_playground_agent_factory(settings, db, providers)
    assert_every_agent_routed([factory.id])
    agents.append(factory)
    return agents


def build_playground_agent_factory(
    settings: RetailSettings, db: PostgresDb, providers: TierProviders
) -> AgentFactory:
    """R11's playground: a fresh, narrowly-tooled ``Agent`` per request rather than four
    static ones. ``ctx.input.mode``/``scope_id`` resolve through
    ``playground.modes.aresolve_mode_scope`` -- the deterministic router that decides what
    the returned agent is even allowed to try -- before the agent is constructed at all.

    ``build_playground_agent`` is itself ``async def``: ``AgentFactory.factory`` accepts
    either a sync or an async callable (``BaseFactory.__init__``'s
    ``Union[Callable[[RequestContext], T], Callable[[RequestContext], Awaitable[T]]]``), and
    the async twin is what actually gets exercised this way -- ``business_area``'s ontology
    file read is genuine (if small) blocking I/O, and a sync factory would run it inline on
    the event loop thread rather than off it.

    Not covered by ``route_agents``: a factory has no ``model`` attribute of its own to pin
    or route, since the model belongs to the ``Agent`` it builds per call. The per-request
    ``model_provider`` assignment below is what ``route_agents`` does for the two static
    agents, done here at the one place it applies to a factory-built agent.
    """
    prompt = _PLAYGROUND_PROMPT.read_text()

    async def build_playground_agent(ctx: RequestContext) -> Agent:
        # `RequestContext.input` is typed as `BaseModel | dict | None` because a factory
        # can declare any schema; `input_schema=PlaygroundModeInput` below is what EgptOS
        # actually validates it against before this function ever runs.
        cfg = cast(PlaygroundModeInput, ctx.input)
        scope = await aresolve_mode_scope(cfg.mode, cfg.scope_id, settings=settings)
        if scope.refused:
            instructions = (
                f"{prompt}\n\n"
                f"This request is refused before any tool call: {scope.refused}\n"
                "Return PlaygroundAnswer with an empty blocks list and notes set to exactly "
                "that refusal text -- do not attempt to answer from general knowledge."
            )
            tools: List[Any] = []
        else:
            instructions = prompt
            tools = list(scope.tools)
        agent = Agent(
            id=PLAYGROUND_AGENT_ID,
            name=PLAYGROUND_AGENT_ID,
            description=(
                "Answers one retail question, grounded only by its resolved mode's tools (R11)."
            ),
            instructions=instructions,
            tools=tools,
            output_schema=PlaygroundAnswer,
            db=db,
        )
        # `model_provider` is not an `Agent` constructor argument -- the framework reads it
        # as a plain attribute (`routing/providers.py`'s `route_agents` does the same thing
        # for the two static agents, via `setattr` for the identical reason: mypy has no
        # stub for an attribute the framework only ever assigns dynamically).
        setattr(
            agent,
            "model_provider",
            provider_for(tier_for(PLAYGROUND_AGENT_ID), providers),
        )  # noqa: B010
        return agent

    return AgentFactory(
        db=db,
        id=PLAYGROUND_AGENT_ID,
        name="Playground",
        description="Four-mode grounded chat (R11): data, knowledge, use_case, business_area.",
        factory=build_playground_agent,
        input_schema=PlaygroundModeInput,
    )


def _chain(executors: tuple) -> List[Any]:
    """Steps from executors, every one ``on_error=OnError.fail`` -- see ``build_workflows``.

    ``List[Any]`` because ``Workflow(steps=...)`` takes an invariant ``list`` of a union that
    a ``List[Step]`` does not satisfy for mypy, though every element is a ``Step``.
    """
    return [
        Step(
            name=executor.__name__.replace("_", "-"),
            executor=executor,
            on_error=OnError.fail,
        )
        for executor in executors
    ]


def build_workflows(
    settings: RetailSettings, db: PostgresDb
) -> List[RegisteredWorkflow]:
    """The registered workflows: R4's landing-to-silver, and R5's nightly-refresh on top of it.

    Two workflows, one executor list. ``nightly-refresh`` is R4's six steps followed by
    ``materialise_gold``; ``landing-to-silver`` stays for hand runs that must not touch gold.
    Both are built from the same tuples, so the chain cannot drift between them, and
    ``assert_registry_valid`` checks ``registry.functions`` -- a name shared by two workflows
    is not a collision, a name shared by two *functions* is.

    A blocked night is a *completed* run: ``decide_load_verdict`` returns ``stop=True``, the
    engine breaks the step loop, ``materialise-gold`` never starts and is absent from
    ``step_results``, and the schedule records ``success``. The verdict is in
    ``quality.load_run``; nothing about the run record says "gold was rebuilt" unless the
    gold step is present. R05's cookbook asserts that shape.

    A workflow rather than a bespoke runner, because that is what buys per-step retries, run
    history in the ``WorkflowSession``, and a step that can stop the chain -- the three things
    R5's nightly gold materialisation will need and would otherwise have to invent.

    **This workflow must be driven with ``arun``.** Its executors are ``async def`` (the
    executor registry's exemption, since every one of them does I/O), and a synchronous
    ``Workflow.run()`` raises inside ``Step``'s retry loop, which ``on_error`` then handles --
    and ``on_error`` defaults to ``OnError.skip``. A sync run would therefore skip every step
    and report a completed run that loaded nothing. ``on_error=OnError.fail`` below is the
    second half of that guard: the gates never raise, so it only bites on an unexpected
    failure, which must stop the chain rather than carry on toward gold.
    """
    return [
        Workflow(
            id=LANDING_TO_SILVER_WORKFLOW_ID,
            name="Landing to silver",
            description=(
                "Loads the raw drop into landing, conforms the five silver entities, and "
                "gates gold on row-count delta, key null rates and referential integrity "
                "(R4, G8)."
            ),
            db=db,
            steps=_chain(WAREHOUSE_EXECUTORS),
        ),
        Workflow(
            id=NIGHTLY_REFRESH_WORKFLOW_ID,
            name="Nightly refresh",
            description=(
                "The scheduled night (D7): landing to silver with its quality gates, then "
                "every ready KPI materialised into gold from the registry's formula, "
                "executed in the warehouse (R5, D6). A blocked load stops the chain before "
                "gold."
            ),
            db=db,
            steps=_chain(WAREHOUSE_EXECUTORS + (materialise_gold,)),
        ),
    ]


def build_registry() -> Registry:
    """The executor registry, validated before it reaches the OS.

    R4's six, R7's three (``check_preflight``, ``record_provenance``, ``deliver_in_app``,
    G4), R5's two (``materialise_gold``, ``read_gold``), R9's two (``check_spec_gaps``,
    ``derive_plan``) and Lane K's two (``assess_kpi_feasibility``, ``detect_business_areas``).
    The check has run here since phase zero, when the list was empty; it now has
    something to check, which is what makes the duplicate-name refusal real rather than
    theoretical. Every name here is also a row in ``contracts/EXECUTOR_REGISTRY.md``, and
    ``test_r4_executors.py``/``test_r7_executors.py`` assert the two agree so the document
    cannot rot.
    """
    registry = Registry()
    for executor in (
        WAREHOUSE_EXECUTORS + R7_EXECUTORS + GOLD_EXECUTORS + R9_EXECUTORS + K_EXECUTORS
    ):
        registry.functions.append(executor)
    assert_registry_valid(registry.functions)
    return registry
