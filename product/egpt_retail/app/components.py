"""What the OS registers: agents, workflows, and the executor registry.

**Every agent gets an explicit, stable ``id``.** ``MEMORY_SCOPE.md`` records that an agent with
neither ``id`` nor ``name`` gets ``generate_id_from_name`` over a uuid4 -- a fresh memory partition
on every process start. The consolidator's scopes are built from the ids listed here, so an
unstable id would silently orphan every partition the previous process wrote.

**The synonym proposer is built here from Lane K's assets; no ``Agent`` exists to import.**
``skills/synonym_proposer.py`` exports the output schema and the prompt/code drift checkers; the
agent itself was only ever constructed inside ``cookbook/94_retail/K14_skill_template.py``. Two
differences from that cookbook: the deployment name comes from settings with a default, never a
bare ``os.environ[...]`` (which raises at import and would take ``/config`` down with it), and the
model is constructed lazily -- ``AzureOpenAI`` reads ``AZURE_OPENAI_*`` in ``_get_client_params``,
not in ``__init__``, so ``/config`` lists the agent with no key present.

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
from typing import Any, List, Optional, Union

from egpt.agent import Agent, AgentFactory, RemoteAgent
from egpt.agent.protocol import AgentProtocol
from egpt.integrations.db.postgres import PostgresDb
from egpt.integrations.models.azure import AzureOpenAI
from egpt.memory.config import build_memory_provider_for
from egpt.memory.provider import MemoryProvider
from egpt.registry.registry import Registry
from egpt.workflow import RemoteWorkflow, Workflow, WorkflowFactory
from egpt.workflow.step import Step
from egpt.workflow.types import OnError

from egpt_retail.app.settings import RetailSettings
from egpt_retail.executors.delivery import deliver_in_app
from egpt_retail.executors.gold import GOLD_EXECUTORS, materialise_gold
from egpt_retail.executors.preflight import check_preflight
from egpt_retail.executors.provenance import record_provenance
from egpt_retail.executors.registry import assert_registry_valid
from egpt_retail.executors.warehouse import WAREHOUSE_EXECUTORS
from egpt_retail.memory.config import build_memory_resolver
from egpt_retail.skills.synonym_proposer import SynonymProposalSet

__all__ = [
    "RegisteredAgent",
    "RegisteredWorkflow",
    "SYNONYM_PROPOSER_ID",
    "LANDING_TO_SILVER_WORKFLOW_ID",
    "NIGHTLY_REFRESH_WORKFLOW_ID",
    "MEMORY_WRITING_AGENT_IDS",
    "build_agents",
    "build_workflows",
    "build_registry",
]

#: Exactly what ``EgptOS(agents=..., workflows=...)`` accepts. ``list`` is invariant, so the
#: builders declare the union rather than the concrete class.
RegisteredAgent = Union[Agent, RemoteAgent, AgentProtocol, AgentFactory]
RegisteredWorkflow = Union[Workflow, RemoteWorkflow, WorkflowFactory]

SYNONYM_PROPOSER_ID = "synonym_proposer"

#: R4's pipeline, kept registered for hand runs. Stable: stored workflows reference it by id.
LANDING_TO_SILVER_WORKFLOW_ID = "landing-to-silver"

#: R5's nightly chain: R4's six steps then ``materialise_gold``. The schedule
#: (``egpt-retail schedule-nightly``) targets this id, so it is the one that must not move.
NIGHTLY_REFRESH_WORKFLOW_ID = "nightly-refresh"

#: The agents whose memory partitions the consolidator drains. Phase zero: one.
MEMORY_WRITING_AGENT_IDS: List[str] = [SYNONYM_PROPOSER_ID]

#: R7's three (G4). Unlike R4's six, these are not wired into a standalone registered
#: workflow -- they exist to be composed into other packages' workflows (R10's RFQ chain,
#: eventually), and R7's own verification is a throwaway workflow in the cookbook.
R7_EXECUTORS = (check_preflight, record_provenance, deliver_in_app)

_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
_SYNONYM_PROPOSER_PROMPT = _SKILLS_DIR / "EXAMPLE.md"


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
        model=AzureOpenAI(id=settings.retail_azure_deployment),
        instructions=_SYNONYM_PROPOSER_PROMPT.read_text(),
        output_schema=SynonymProposalSet,
        db=db,
    )
    return [synonym_proposer]


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
    G4) and R5's two (``materialise_gold``, ``read_gold``). The check has run here since phase zero, when the list was empty; it now has
    something to check, which is what makes the duplicate-name refusal real rather than
    theoretical. Every name here is also a row in ``contracts/EXECUTOR_REGISTRY.md``, and
    ``test_r4_executors.py``/``test_r7_executors.py`` assert the two agree so the document
    cannot rot.
    """
    registry = Registry()
    for executor in WAREHOUSE_EXECUTORS + R7_EXECUTORS + GOLD_EXECUTORS:
        registry.functions.append(executor)
    assert_registry_valid(registry.functions)
    return registry
