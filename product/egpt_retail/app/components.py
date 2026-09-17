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

import asyncio
from pathlib import Path
from typing import Any, List, Optional, Union, cast

from egpt.agent import Agent, AgentFactory, RemoteAgent
from egpt.agent.protocol import AgentProtocol
from egpt.factory import RequestContext
from egpt.integrations.db.postgres import PostgresDb
from egpt.memory.config import build_memory_provider_for
from egpt.memory.provider import MemoryProvider
from egpt.providers.model_provider import ModelResolveRequest
from egpt.registry.registry import Registry
from egpt.utils.log import log_warning
from egpt.workflow import RemoteWorkflow, Workflow, WorkflowFactory
from egpt.workflow.step import Step
from egpt.workflow.types import OnError
from pydantic import BaseModel

from egpt_retail.app.settings import RetailSettings
from egpt_retail.executors.alerts import ALERT_EXECUTORS, evaluate_alerts
from egpt_retail.executors.areas import detect_business_areas
from egpt_retail.executors.delivery import deliver_in_app
from egpt_retail.executors.gaps import check_spec_gaps
from egpt_retail.executors.gold import GOLD_EXECUTORS, materialise_gold, read_gold
from egpt_retail.executors.golden_gate import check_golden_gate
from egpt_retail.executors.kpi import assess_kpi_feasibility
from egpt_retail.executors.monitoring import (
    assess_execution_health,
    assess_quality,
    list_silent_schedules,
)
from egpt_retail.executors.plan import derive_plan
from egpt_retail.executors.preflight import check_preflight
from egpt_retail.executors.provenance import record_provenance
from egpt_retail.executors.registry import assert_registry_valid
from egpt_retail.executors.rfq import check_explanation_grounding, sample_rows
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
from egpt_retail.skills.funnel_explanation import FunnelExplanation
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
    "FUNNEL_EXPLANATION_ID",
    "LANDING_TO_SILVER_WORKFLOW_ID",
    "NIGHTLY_REFRESH_WORKFLOW_ID",
    "RFQ_WORKFLOW_ID",
    "ALERT_EVALUATION_WORKFLOW_ID",
    "MEMORY_WRITING_AGENT_IDS",
    "abuild_memory_provider",
    "build_agents",
    "build_memory_provider",
    "build_playground_agent_factory",
    "build_registry",
    "build_workflows",
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

#: Lane R's second agent from the K14 template (R10). The RFQ workflow's one model step --
#: registered here, not only embedded in ``build_workflows``'s step, so it gets the same
#: memory wiring and startup check every other agent gets (``memory_lifespan_for``,
#: ``assert_memory_wired``) rather than a second, unwired object nobody drains.
FUNNEL_EXPLANATION_ID = "funnel_explanation"

#: R4's pipeline, kept registered for hand runs. Stable: stored workflows reference it by id.
LANDING_TO_SILVER_WORKFLOW_ID = "landing-to-silver"

#: R5's nightly chain: R4's six steps then ``materialise_gold``. The schedule
#: (``egpt-retail schedule-nightly``) targets this id, so it is the one that must not move.
NIGHTLY_REFRESH_WORKFLOW_ID = "nightly-refresh"

#: R10's RFQ chain (D12): explain-only, one model step. Named for the schedule that will
#: target it once R9's activation route exists; stable for the same reason NIGHTLY's is.
RFQ_WORKFLOW_ID = "rfq-funnel"

#: R12's alert pass (G7): every enabled ``alert_definitions`` row evaluated against gold,
#: then ``record_provenance``. Named for the schedule R9's activation route will eventually
#: target -- "an evaluation executor running after materialisation" (delivery-plan.md) means
#: after ``nightly-refresh``, not inside it: a second registered workflow rather than a
#: seventh step appended to NIGHTLY_REFRESH_WORKFLOW_ID, so an alert-only re-run never
#: re-triggers a gold rebuild and a gold-only re-run is never blocked on alert delivery.
ALERT_EVALUATION_WORKFLOW_ID = "alert-evaluation"

#: The agents whose memory partitions the consolidator drains. The three static agents: the
#: lifespan hands each one the memory provider, so each one writes a partition.
#: ``PLAYGROUND_AGENT_ID`` is deliberately absent -- ``memory_lifespan_for`` iterates the
#: objects ``build_agents`` returns, and for a factory that is the ``AgentFactory`` itself,
#: not the fresh ``Agent`` it builds per request inside ``build_playground_agent``. Wiring
#: memory into a per-request factory agent is real work R11 does not do in this package;
#: listing this id here without doing it would tell the consolidator to drain a partition
#: nothing ever writes.
MEMORY_WRITING_AGENT_IDS: List[str] = [
    SYNONYM_PROPOSER_ID,
    JOIN_PATH_EXPLAINER_ID,
    FUNNEL_EXPLANATION_ID,
]

#: R7's three (G4). Unlike R4's six, these are not wired into a standalone registered
#: workflow -- they exist to be composed into other packages' workflows (R10's RFQ chain,
#: eventually), and R7's own verification is a throwaway workflow in the cookbook.
R7_EXECUTORS = (check_preflight, record_provenance, deliver_in_app)

#: R12's three (G7): ``evaluate_alert_condition`` and ``write_alert`` are the names K15
#: already reserved for the Alert shape's own stages before R12 existed to build them;
#: ``evaluate_alerts`` is the nightly batch pass that calls both. See
#: ``executors/alerts.py``.
R12_EXECUTORS = ALERT_EXECUTORS

#: R10's own (D12). ``sample_rows`` is the API contract's own example plan's third step,
#: reserved there since R9 landed and refused as unregistered until now.
#: ``check_explanation_grounding`` was added after review found the plan's own checkers
#: (``citations_within_input``, ``movers_match_input``) were cookbook/test-only, reachable by
#: nothing at run time -- the contract's example plan (``paths/use-cases.yaml``) was updated
#: to add it as step 5 in the same change.
R10_EXECUTORS = (sample_rows, check_explanation_grounding)

#: Lane K's two (K9, K11). Both are deterministic steps a workflow composes -- feasibility is
#: a set difference and area detection is a graph query -- and both shipped unreachable:
#: registered in no registry, called from nowhere, with docstrings claiming registration.
#: A name in EXECUTOR_REGISTRY.md that no registry holds is a contract nobody can resolve.
K_EXECUTORS = (assess_kpi_feasibility, detect_business_areas)
#: R9's two: the deterministic gap check and the plan view. Composed into the builder's
#: routes in P5; neither is a step of a registered workflow.
R9_EXECUTORS = (check_spec_gaps, derive_plan)

#: R14's three (D14): execution health from run history, silent-schedule detection, and the
#: input-drift assessment pass. Like Lane K's two above, none is a step of a registered
#: workflow -- they read run history and schedule state directly, composed into the builder's
#: `/monitoring/*` routes in P5. None permits or blocks: a monitoring pass reports.
R14_EXECUTORS = (assess_execution_health, list_silent_schedules, assess_quality)

#: R15's one (D14, G13): the publish gate. Documented in EXECUTOR_REGISTRY.md since it landed,
#: but omitted from this sum until a review caught it -- an unreachable executor is exactly the
#: "documented but unregistered" drift the registry doc exists to prevent, and now
#: ``test_executor_registry_doc_coverage.py`` walks every row of that document and asserts it
#: resolves via ``build_registry()``, so this direction of drift fails CI for every package.
GOLDEN_EXECUTORS = (check_golden_gate,)

_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
_SYNONYM_PROPOSER_PROMPT = _SKILLS_DIR / "EXAMPLE.md"
_JOIN_PATH_EXPLAINER_PROMPT = _SKILLS_DIR / "join_path_explainer.md"
_PLAYGROUND_PROMPT = _SKILLS_DIR / "playground.md"
_FUNNEL_EXPLANATION_PROMPT = _SKILLS_DIR / "funnel_explanation.md"


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


async def abuild_memory_provider(settings: RetailSettings) -> Optional[MemoryProvider]:
    """Async variant, and the one ``factory.memory_lifespan_for`` actually calls.

    ``build_memory_provider_for`` opens a pgvector connection and provisions its schema, and
    has no async sibling of its own (``egpt/memory/config.py``), so the blocking construction
    runs off the event loop with ``asyncio.to_thread``. The single caller is an ASGI lifespan
    (``async def _lifespan``): calling the sync function there blocked the event loop for the
    length of a database round trip at every boot, which is the whole reason this variant
    exists rather than the sync/async convention on its own.
    """
    return await asyncio.to_thread(build_memory_provider, settings)


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
    funnel_explanation = Agent(
        id=FUNNEL_EXPLANATION_ID,
        name=FUNNEL_EXPLANATION_ID,
        description=(
            "Explains what moved in the RFQ quote funnel from numbers `read_gold` and "
            "`sample_rows` already computed; never computes or ranks one itself (Lane R, R10)."
        ),
        instructions=_FUNNEL_EXPLANATION_PROMPT.read_text(),
        output_schema=FunnelExplanation,
        db=db,
    )
    agents: List[RegisteredAgent] = [
        synonym_proposer,
        join_path_explainer,
        funnel_explanation,
    ]
    providers = build_tier_providers(settings)
    # R8: tier per agent, from the table; refuses an unrouted or model-pinning agent.
    route_agents(agents, providers)
    # K12 and K13 are deliberately NOT registered here. A registered agent sits on the
    # framework's generic run route, and a caller hitting it would get the raw draft set with
    # none of assemble()'s discipline -- no dedup against the registry, no vocabulary check,
    # no definition self-consistency -- from a prompt whose closed sets were rendered at
    # startup. They run only through `egpt-retail suggest`, which builds them with the live
    # registry via `build_suggestion_agents` and always assembles. Routing rows still exist
    # and the builder still routes; `test_k12_k13_suggestion_pass` pins both.

    # The playground agent is built per request by an AgentFactory (R11), not statically
    # like the two above -- `route_agents` only ever sees the two static agents, so its own
    # tier is asserted here instead, at the same startup point, rather than only surfacing
    # the first time a request resolves an unrouted agent id.
    factory = build_playground_agent_factory(settings, db, providers)
    assert_every_agent_routed([factory.id])
    agents.append(factory)
    return agents


def _verified_user_id(ctx: Any) -> Optional[str]:
    """The caller's identity, but only when something verified it.

    ``RequestContext.user_id`` is **not** a verified value. Its own docstring says "From form
    field or request.state": ``build_request_context`` takes the multipart ``user_id`` form
    field verbatim, and the run route overrides it only when middleware stamped an identity.
    Two reachable states leave the raw form field standing -- authorization off (retail's
    documented local posture, ``app/scopes.py``), and a token that decodes but carries no
    ``sub`` (``os/middleware/jwt.py`` sets ``request.state.user_id = None`` *explicitly*).

    That matters because this value becomes the memory read scope. Trusting it would let
    ``curl -F 'user_id=someone-else'`` read another person's memories -- cross-user, not
    cross-tenant, and exactly what MEMORY_SCOPE.md's read table exists to prevent.

    So: take the identity only from ``ctx.trusted``, which ``factory/utils.py`` documents as
    "Populated by verified middleware only", and fall back to ``None``. ``None`` drops the
    memory leg rather than widening it -- no memory is the honest answer for an unidentified
    caller, and "everyone's" is the one answer that must never be returned.

    This is the same posture ``api/routers/playground.py``'s ``_owner`` takes for saved
    prompts and reports, which refuses rather than collapsing callers onto a shared identity.
    The difference is the consequence: ``_owner`` raises 401 because an unowned prompt cannot
    be stored, while here a degraded answer is better than a refused one -- the other three
    legs still work without memory.
    """
    trusted = getattr(ctx, "trusted", None)
    verified = getattr(trusted, "user_id", None) if trusted is not None else None
    if verified:
        return str(verified)
    log_warning(
        "playground: no verified caller identity on this request, so the memory leg is "
        "dropped. An unverified user_id must never scope a memory read (MEMORY_SCOPE.md)."
    )
    return None


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
        scope = await aresolve_mode_scope(
            cfg.mode,
            cfg.scope_id,
            settings=settings,
            user_id=_verified_user_id(ctx),
            model=provider_for(tier_for(PLAYGROUND_AGENT_ID), providers).resolve(
                ModelResolveRequest(agent_id=PLAYGROUND_AGENT_ID)
            ),
        )
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


def _rfq_steps(funnel_explanation: Agent) -> List[Any]:
    """The API contract's own example plan order (``paths/use-cases.yaml``,
    ``runUseCasePreflight``) -- ``check_preflight``, ``read_gold``, ``sample_rows``, the one
    model step, then R7's ``record_provenance`` and ``deliver_in_app`` -- plus one step added
    after the plan was first written: ``check_explanation_grounding``, inserted between the
    model step and delivery after review found the schema's own checkers
    (``citations_within_input``, ``movers_match_input``) were reachable from nowhere at run
    time. See that executor's docstring; ``paths/use-cases.yaml`` was updated in the same
    change to name it as step 5.

    Not built by :func:`_chain`, which only knows executors -- ``funnel_explanation`` is the
    live, already-routed ``Agent`` object :func:`build_agents` returned, not a name the engine
    resolves later, so this workflow must be assembled after ``build_agents`` has run.
    """
    executor_steps = _chain((check_preflight, read_gold, sample_rows))
    return [
        *executor_steps[:3],
        Step(
            name="funnel-explanation",
            agent=funnel_explanation,
            description="One model step. Explains the numbers; never decides anything.",
            on_error=OnError.fail,
        ),
        *_chain((check_explanation_grounding, record_provenance, deliver_in_app)),
    ]


def build_workflows(
    settings: RetailSettings,
    db: PostgresDb,
    agents: Optional[List[RegisteredAgent]] = None,
) -> List[RegisteredWorkflow]:
    """The registered workflows: R4's landing-to-silver, R5's nightly-refresh on top of it,
    and R10's rfq-funnel.

    Two of the three, one executor list. ``nightly-refresh`` is R4's six steps followed by
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

    ``rfq-funnel`` (R10, D12) is the third: ``check_preflight``, ``read_gold``,
    ``sample_rows``, the ``funnel_explanation`` agent step, ``record_provenance``,
    ``deliver_in_app`` -- the API contract's own example compiled plan. Its agent step needs
    a real, already-routed ``Agent`` object, not a name, so ``agents`` defaults to a fresh
    ``build_agents(settings, db)`` call when a caller (a cookbook, a test) does not already
    have one; ``build_agent_os`` passes its own list so the workflow's step and the OS's
    registered agent are the same object, which is what lets the memory lifespan's provider
    assignment reach the one this workflow actually runs.
    """
    agents = agents if agents is not None else build_agents(settings, db)
    try:
        funnel_explanation = next(
            agent
            for agent in agents
            if getattr(agent, "id", None) == FUNNEL_EXPLANATION_ID
        )
    except StopIteration:
        raise ValueError(
            f"{FUNNEL_EXPLANATION_ID!r} must be in `agents` to build the rfq-funnel workflow"
        ) from None
    if not isinstance(funnel_explanation, Agent):
        raise TypeError(
            f"{FUNNEL_EXPLANATION_ID!r} must be a concrete Agent to embed in a workflow "
            f"step, got {type(funnel_explanation).__name__}"
        )
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
        Workflow(
            id=RFQ_WORKFLOW_ID,
            name="RFQ funnel",
            description=(
                "The RFQ and Quote Pipeline (D12): explain-only, one model step. Reads the "
                "funnel KPIs from gold, samples the movers and on-site friction supporting "
                "them, explains what moved, records provenance and delivers in-app (R10)."
            ),
            db=db,
            steps=_rfq_steps(funnel_explanation),
        ),
        Workflow(
            id=ALERT_EVALUATION_WORKFLOW_ID,
            name="Alert evaluation",
            description=(
                "Every enabled alert definition, checked against gold (G7): fires and "
                "delivers through R7's own path when its threshold is crossed, and records "
                "that it ran either way so a quiet week reads as quiet rather than broken "
                "(R12)."
            ),
            db=db,
            steps=_chain((evaluate_alerts, record_provenance)),
        ),
    ]


def build_registry() -> Registry:
    """The executor registry, validated before it reaches the OS.

    R4's six, R7's three (``check_preflight``, ``record_provenance``, ``deliver_in_app``,
    G4), R5's two (``materialise_gold``, ``read_gold``), R9's two (``check_spec_gaps``,
    ``derive_plan``), R10's two (``sample_rows``, ``check_explanation_grounding``), R12's
    three (``evaluate_alert_condition``, ``write_alert``, ``evaluate_alerts``), R14's three
    (``assess_execution_health``, ``list_silent_schedules``, ``assess_quality``), Lane K's two
    (``assess_kpi_feasibility``, ``detect_business_areas``) and R15's one
    (``check_golden_gate``). The check has run here since phase zero, when the list was empty;
    it now has something to check, which is what makes the duplicate-name refusal real rather
    than theoretical. Every name here is also a row in ``contracts/EXECUTOR_REGISTRY.md``, and
    ``test_r4_executors.py``/``test_r7_executors.py`` assert the two agree for their own
    executors so the document cannot rot in that direction; the other direction (documented but
    never added to this sum -- exactly how ``check_golden_gate`` shipped unreachable)
    is caught for every package by ``test_executor_registry_doc_coverage.py``, which walks
    every row of the document and asserts it resolves via this function.
    """
    registry = Registry()
    for executor in (
        WAREHOUSE_EXECUTORS
        + R7_EXECUTORS
        + GOLD_EXECUTORS
        + R9_EXECUTORS
        + R10_EXECUTORS
        + R12_EXECUTORS
        + R14_EXECUTORS
        + K_EXECUTORS
        + GOLDEN_EXECUTORS
    ):
        registry.functions.append(executor)
    assert_registry_valid(registry.functions)
    return registry
