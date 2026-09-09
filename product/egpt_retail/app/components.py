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
from typing import List, Optional, Union

from egpt.agent import Agent, AgentFactory, RemoteAgent
from egpt.agent.protocol import AgentProtocol
from egpt.integrations.db.postgres import PostgresDb
from egpt.integrations.models.azure import AzureOpenAI
from egpt.memory.config import build_memory_provider_for
from egpt.memory.provider import MemoryProvider
from egpt.registry.registry import Registry
from egpt.workflow import RemoteWorkflow, Workflow, WorkflowFactory

from egpt_retail.app.settings import RetailSettings
from egpt_retail.executors.registry import assert_registry_valid
from egpt_retail.memory.config import build_memory_resolver
from egpt_retail.skills.synonym_proposer import SynonymProposalSet

__all__ = [
    "RegisteredAgent",
    "RegisteredWorkflow",
    "SYNONYM_PROPOSER_ID",
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

#: The agents whose memory partitions the consolidator drains. Phase zero: one.
MEMORY_WRITING_AGENT_IDS: List[str] = [SYNONYM_PROPOSER_ID]

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


def build_workflows(
    settings: RetailSettings, db: PostgresDb
) -> List[RegisteredWorkflow]:
    """The registered workflows. None in phase zero: the RFQ chain is R10 and the executor
    library it composes is R7. Kept as a function so ``main.py`` does not change when they land."""
    return []


def build_registry() -> Registry:
    """The executor registry, validated before it reaches the OS.

    Empty in phase zero -- ``EXECUTOR_REGISTRY.md`` fixes the shape, not the list -- but the check
    runs now so the seam is where the convention says it is: "wire it into the application factory
    next to K1's memory startup check".
    """
    registry = Registry()
    assert_registry_valid(registry.functions)
    return registry
