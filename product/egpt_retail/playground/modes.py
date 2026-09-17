"""R11's four-mode router -- the deterministic core "skills loaded on demand" means.

No model decides which data a mode may touch, in this repo's house style (R6's SQL guard,
K11's feasibility): :func:`resolve_mode_scope` is a pure-ish function from ``(mode,
scope_id)`` to a bounded tuple of tools, built once per request by the ``AgentFactory``
(``skills/playground.py``, ``app/components.py``). A mode whose inputs cannot be resolved --
an unknown use case, an area outside the ontology -- is refused before an agent is ever
built (:attr:`ModeScope.refused`), so a declined question never reaches a model call at all.

Within a *resolved* mode, the tools themselves enforce the boundary structurally rather than
by instruction: the business-area concept lookup below returns a decline for a concept
outside its area instead of the concept's description, so "declined or scoped, not answered
from everything" holds even if the agent's own prose does not say so. This is the same
posture R6's guard takes -- refuse the read, do not ask the model to behave.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

from egpt.answer_provenance import alist_provenance_by_use_case, aread_provenance
from egpt.memory.types import MemoryQuery, Scope
from egpt.utils.log import log_warning
from egpt.workflow.types import StepInput

from egpt_retail.app.db import build_control_plane_engine
from egpt_retail.app.semantic import open_semantic_reader, semantic_driver
from egpt_retail.app.settings import RetailSettings
from egpt_retail.app.stores import abuild_knowledge_stores
from egpt_retail.executors.gold import read_gold
from egpt_retail.ontology.model import ARTIFACT_PATH, load_ontology
from egpt_retail.playground.grounding import agrounded_read_instructions
from egpt_retail.retrieval.legs import aretail_unified_read
from egpt_retail.warehouse.schema import SILVER_SCHEMA

__all__ = ["PlaygroundMode", "ModeScope", "resolve_mode_scope", "aresolve_mode_scope"]


class PlaygroundMode(str, Enum):
    """The four grounding scopes R11's delivery-plan entry names. Distinct from
    ``contracts.playground.ChatMode`` (``full``/``brief``), the answer-format axis the
    OpenAPI contract itself specifies -- this one is internal to the ``AgentFactory``'s
    ``factory_input``, not a wire-contract enum."""

    DATA = "data"
    KNOWLEDGE = "knowledge"
    USE_CASE = "use_case"
    BUSINESS_AREA = "business_area"


@dataclass(frozen=True)
class ModeScope:
    """What one resolved mode may do. ``refused`` set means the agent factory must not build
    an agent at all -- the tools tuple is empty and irrelevant in that case."""

    mode: PlaygroundMode
    scope_id: Optional[str]
    tools: Tuple[Callable[..., Any], ...] = ()
    description: str = ""
    refused: Optional[str] = None


def resolve_mode_scope(
    mode: PlaygroundMode,
    scope_id: Optional[str],
    *,
    settings: Optional[RetailSettings] = None,
    user_id: Optional[str] = None,
    model: Any = None,
) -> ModeScope:
    """The bounded tool set for one mode.

    Synchronous, and two things now happen at resolution time rather than per call, both
    deliberately: ``business_area``'s ontology file read (local disk), and ``knowledge``'s
    memory provider, which opens the pgvector engine and provisions its schema. Both are paid
    here because ``aresolve_mode_scope`` runs this on a worker thread, so the cost lands off
    the event loop; doing either inside a tool closure would block the loop on the first
    question instead. Everything else still opens its own connection per call.

    ``model`` is the resolved model for this request's tier. It reaches ``data`` mode's SQL
    sub-agent, which would otherwise be built with no model at all -- see ``query_warehouse``.
    """
    settings = settings or RetailSettings()
    if mode is PlaygroundMode.DATA:
        return _data_scope(settings, model)
    if mode is PlaygroundMode.KNOWLEDGE:
        return _knowledge_scope(settings, user_id)
    if mode is PlaygroundMode.USE_CASE:
        return _use_case_scope(scope_id, settings)
    if mode is PlaygroundMode.BUSINESS_AREA:
        return _business_area_scope(scope_id, settings)
    raise ValueError(
        f"unknown playground mode {mode!r}"
    )  # pragma: no cover -- enum-closed


async def aresolve_mode_scope(
    mode: PlaygroundMode,
    scope_id: Optional[str],
    *,
    settings: Optional[RetailSettings] = None,
    user_id: Optional[str] = None,
    model: Any = None,
) -> ModeScope:
    """:func:`resolve_mode_scope` off the event loop."""
    return await asyncio.to_thread(
        resolve_mode_scope,
        mode,
        scope_id,
        settings=settings,
        user_id=user_id,
        model=model,
    )


def _data_scope(settings: RetailSettings, model: Any = None) -> ModeScope:
    """Chat with data: gold for what a KPI *is*, guarded SQL for everything else (G21).

    Two tools, and the split is the point. ``read_kpi_data`` answers "what is KPI X" from
    materialised gold -- stamped with source and computation time, which is the alignment
    rule's whole reason for gold existing. ``query_warehouse`` answers the open-ended "why did
    it move" questions no registered KPI covers, by putting a model in front of silver.

    Not one or the other: a model writing SQL to recompute a number that already exists as a
    governed gold row would produce an answer nobody could trace, which is exactly what gold's
    stamps exist to prevent. Gold stays authoritative; SQL is for exploration.

    **G21 is the instructions this provider runs under.** ``DEFAULT_READ_INSTRUCTIONS``
    substitutes ``{schema}`` and nothing else, so without grounding the sub-agent sees
    ``crm_deal`` and two columns called ``amount`` and cannot learn which is a quote, that this
    customer says "RFQ", or that every quote query carries a scoping filter -- while the
    semantic layer holds all of it, reviewed and accepted through K7.

    Both paths run inside R6's guard. ``build_warehouse_read_guard`` is the same call R5's
    materialiser and ``read_gold`` make, so "no SQL runs outside the guard" holds for the SQL
    tool by construction rather than by the sub-agent behaving.
    """

    async def read_kpi_data(kpi_identifiers: List[str]) -> Dict[str, Any]:
        """Materialised values for the named KPIs, through the guard. A KPI with nothing
        materialised is listed under ``missing`` with why, never invented."""
        step_input = StepInput(additional_data={"kpi_identifiers": kpi_identifiers})
        output = await read_gold(step_input)
        # Narrowing cast, not an assumption: `read_gold` always returns a dict content.
        return cast(Dict[str, Any], output.content)

    async def query_warehouse(question: str) -> Dict[str, Any]:
        """Answer a question from silver by reading it, grounded in the semantic layer.

        **The sub-agent is given an explicit model.** ``DatabaseContextProvider`` passes its
        ``model`` straight to the agent it builds, and an agent with no model and no
        ``model_provider`` falls through to ``get_model("openai-responses:gpt-5.4")`` --
        ``agent/_init.py``'s own docstring says that path "would ship tenant prompts to
        api.openai.com with nothing in the log to say why". Everything this tool handles is
        exactly what must not go there: the grounding block (personal-data field names and
        compliance tags included), the schema of every silver table, and up to ``row_cap``
        rows of real customer data. Routing it through R8's small tier is also what keeps it
        inside the routing table rather than invisible to it.

        **Both engines are the read-only one.** ``sql_engine`` is what the provider would bind
        to the *write* agent, and although ``write=False`` means that agent is never built,
        handing a writable warehouse engine to an interactive surface leaves one boolean
        between the playground and it. ``app/db.py`` argues for three layers, none of them the
        whole story alone; this is the cheapest of the three. It also means the ``api`` role no
        longer needs ``EGPT_RETAIL_WAREHOUSE_DB_URL``, which its own contract says it should
        never have.

        Engines come from the pooled accessor rather than ``create_engine`` per call: two
        fresh pools per question is how a burst of chat exhausts ``max_connections``.
        """
        from egpt.integrations.context.database.provider import DatabaseContextProvider

        from egpt_retail.app.db import pooled_warehouse_readonly_engine
        from egpt_retail.app.sql_guard import build_warehouse_read_guard

        engine = pooled_warehouse_readonly_engine(settings)
        with open_semantic_reader(settings) as reader:
            instructions = await agrounded_read_instructions(reader)
            guard = build_warehouse_read_guard(reader)
            provider = DatabaseContextProvider(
                id="warehouse",
                sql_engine=engine,
                readonly_engine=engine,
                schema=SILVER_SCHEMA,
                read_instructions=instructions,
                read_guard=guard,
                model=model,
                # Read-only by construction, not by instruction: the write agent is never
                # built, so no prompt has to be trusted to keep the playground off the
                # write path.
                write=False,
            )
            answer = await provider.aquery(question)
        content = getattr(answer, "content", answer)
        # NO PROVENANCE ROW, and it is not an oversight. "Every answer writes a provenance
        # record" is an alignment rule, and this path breaks it -- but the record is keyed by
        # `run_id`, which only the run route mints, and `RequestContext` carries `user_id`,
        # `session_id` and `tenant_id` and no run id. A tool closure therefore cannot write a
        # correctly-keyed row, and writing a wrongly-keyed one is worse than none: the citation
        # panel would show sources that belong to no run anyone can find. Threading the run id
        # into the factory context is a framework change and is deliberately not smuggled in
        # here. `read_kpi_data` has the same gap; this tool does not widen it, it inherits it.
        return {
            "answer": content,
            "schema": SILVER_SCHEMA,
            "grounded": "## The semantic layer" in instructions,
        }

    return ModeScope(
        mode=PlaygroundMode.DATA,
        scope_id=None,
        tools=(read_kpi_data, query_warehouse),
        description="Gold for KPI values, guarded and grounded SQL over silver for the rest.",
    )


@lru_cache(maxsize=4)
def _memory_provider_for(slug: str) -> Optional[Any]:
    """One memory provider per tenant slug, per process.

    Cached because construction is not cheap: ``build_memory_provider_for`` opens the pgvector
    engine and provisions its schema (``app/factory.py`` says so, which is why K1's own wiring
    is a lifespan rather than part of ``build_agent_os``). Building one per tool call would
    open an engine per question asked.

    Built at *resolution* time, not inside the tool closure, so the cost lands on the thread
    ``aresolve_mode_scope`` already runs the resolver in rather than on the event loop.

    The import is function-local: ``app/components.py`` imports this module at module scope,
    so naming it at the top of this one is a cycle.

    **Not closed.** It lives for the process, which is what a shared provider is for -- but it
    is a second provider alongside the one K1's lifespan builds and closes, so this process
    holds two engines where it could hold one. Threading the lifespan's provider into the
    agent factory instead is the better shape and is flagged for review rather than guessed at
    here: it needs the factory to receive something the lifespan populates after startup, and
    that is a change to how the factory is constructed, not to this module.
    """
    from egpt_retail.app.components import build_memory_provider

    settings = RetailSettings()
    if (settings.retail_customer_slug or "").strip() != slug:
        # The cache key must be the thing the provider is built for. A caller whose settings
        # disagree with the key would otherwise get another tenant's provider.
        return None
    return build_memory_provider(settings)


def _shared_memory_provider(settings: RetailSettings) -> Optional[Any]:
    """The cached provider for these settings, or ``None`` when no slug is configured."""
    slug = (settings.retail_customer_slug or "").strip()
    if not slug:
        return None
    try:
        return _memory_provider_for(slug)
    except Exception as exc:  # noqa: BLE001 -- no memory is a degraded answer, not a failed one
        log_warning(
            f"playground: memory leg unavailable ({exc.__class__.__name__}: {exc}); "
            "answering from the prose, ontology and semantic legs only."
        )
        return None


def _knowledge_scope(
    settings: RetailSettings, user_id: Optional[str] = None
) -> ModeScope:
    """The knowledge base through K10's full four-leg fan-out.

    All four legs, which is the point: domain prose, the shipped ontology, this deployment's
    semantic layer and memory, fused by reciprocal rank fusion so a question needing a domain
    rule *and* a table name gets both. The first cut wired the semantic leg alone, so "chat
    with the knowledge base" searched table and column names and never reached the knowledge
    base; the prose and ontology stores it was missing are ``app/stores.py``'s, the same ones
    ``entrypoints/suggest.py`` builds.

    **The memory leg reads as tenant + user, never tenant alone.** ``MEMORY_SCOPE.md``'s read
    table is explicit that a tenant-wide read returns every row, including every *other*
    user's -- so fanning memory in under the tenant-only scope this mode used to build would
    have made one person's playground answer cite another person's memories. ``user_id`` is
    threaded from the verified ``RequestContext``, never from anything the caller sends, and
    an absent one drops the leg rather than widening it: no memory is the honest answer for an
    unidentified caller, and "all of them" is the one answer that must not be.

    A store that cannot be built (no embedder installed, graph unreachable) drops its leg and
    logs; the remaining legs still answer. K10's own contract distinguishes a leg that ran and
    matched nothing from one that did not run, and degrading to fewer legs is visible in the
    per-hit ``origin`` the tool returns.
    """

    memory = _shared_memory_provider(settings) if user_id else None

    async def search_knowledge(query: str, limit: int = 5) -> Dict[str, Any]:
        slug = settings.retail_customer_slug or "unscoped"
        scope = (
            Scope(tenant_id=slug, user_id=user_id) if user_id else Scope(tenant_id=slug)
        )
        mem_query = MemoryQuery(text=query, limit=limit)
        # One driver for both graph readers: OntologyStore and SemanticGraphStore read the
        # same Neo4j, and opening two pools for one fan-out is two pools for one question.
        driver = semantic_driver(settings)
        try:
            with open_semantic_reader(settings, driver=driver) as reader:
                # Store construction and the fan-out are separate, and only construction is
                # tolerated. The first version wrapped both, so a memory or semantic *leg*
                # failure was logged as "the prose and ontology legs could not be built" --
                # the wrong cause -- and then re-ran the same two failing legs a second time.
                # `aunified_read` re-raises the first leg exception, so that was reachable.
                # A leg failure now propagates; only a store that will not build degrades.
                stores = None
                try:
                    stores = await abuild_knowledge_stores(settings, driver)
                except Exception as exc:  # noqa: BLE001 -- a missing leg is not a failed answer
                    log_warning(
                        "playground knowledge mode: the prose and ontology stores could not "
                        f"be built ({exc.__class__.__name__}: {exc}); answering from the "
                        "semantic and memory legs only."
                    )
                result = await aretail_unified_read(
                    scope,
                    mem_query,
                    memory=memory,
                    knowledge=stores.knowledge if stores is not None else None,
                    ontology=stores.ontology if stores is not None else None,
                    semantic=reader,
                )
        finally:
            driver.close()
        return {
            "results": [
                {
                    "content": hit.content,
                    "score": hit.score,
                    "origin": hit.origin,
                    "id": hit.id,
                }
                for hit in result.fused()
            ]
        }

    return ModeScope(
        mode=PlaygroundMode.KNOWLEDGE,
        scope_id=None,
        tools=(search_knowledge,),
        description="Unified retrieval over prose, ontology, the semantic layer and memory (K10).",
    )


def _use_case_scope(scope_id: Optional[str], settings: RetailSettings) -> ModeScope:
    """Scoped to one use case's runs. Found by reference, not search: both tools resolve
    ``egpt.answer_provenance`` directly by ``use_case_id``/``run_id``, never through a
    retrieval call over anything."""
    if not scope_id:
        return ModeScope(
            mode=PlaygroundMode.USE_CASE,
            scope_id=scope_id,
            refused="use_case mode requires a use_case_id.",
        )

    async def list_runs(limit: int = 20) -> Dict[str, Any]:
        engine = build_control_plane_engine(settings)
        try:
            records = await alist_provenance_by_use_case(engine, scope_id, limit=limit)
        finally:
            engine.dispose()
        return {
            "use_case_id": scope_id,
            "runs": [record.to_dict() for record in records],
        }

    async def get_run(run_id: str) -> Dict[str, Any]:
        engine = build_control_plane_engine(settings)
        try:
            record = await aread_provenance(engine, run_id)
        finally:
            engine.dispose()
        if record is None or record.use_case_id != scope_id:
            return {"declined": f"run {run_id!r} is not part of use case {scope_id!r}."}
        return record.to_dict()

    return ModeScope(
        mode=PlaygroundMode.USE_CASE,
        scope_id=scope_id,
        tools=(list_runs, get_run),
        description=f"Runs of use case {scope_id!r}, by reference.",
    )


def _business_area_scope(
    scope_id: Optional[str], settings: RetailSettings
) -> ModeScope:
    """Scoped to one business area's concepts. The area's concept names are read once from
    the shipped ontology plus overlay (the same source ``executors/areas.py``'s
    ``detect_business_areas`` reads, in memory, no graph round trip) and the lookup tool
    below refuses anything outside that set."""
    if not scope_id:
        return ModeScope(
            mode=PlaygroundMode.BUSINESS_AREA,
            scope_id=scope_id,
            refused="business_area mode requires a business_area.",
        )

    overlay = settings.retail_ontology_overlay
    ontology = load_ontology(
        ARTIFACT_PATH, overlay_path=Path(overlay) if overlay else None
    )
    concepts_by_name = {
        concept.name: concept
        for concept in ontology.concepts
        if concept.business_area == scope_id
    }
    if not concepts_by_name:
        return ModeScope(
            mode=PlaygroundMode.BUSINESS_AREA,
            scope_id=scope_id,
            refused=(
                f"{scope_id!r} is not a business area this deployment's ontology covers."
            ),
        )

    def lookup_concept(name: str) -> Dict[str, Any]:
        """A structural refusal, not a prompt: a concept outside this area gets a decline,
        never its description -- the model cannot leak scope by asking nicely."""
        concept = concepts_by_name.get(name)
        if concept is None:
            return {
                "declined": (
                    f"{name!r} is outside the {scope_id!r} business area this chat is "
                    "scoped to."
                )
            }
        return {
            "name": concept.name,
            "description": concept.description,
            "synonyms": list(concept.synonyms),
        }

    return ModeScope(
        mode=PlaygroundMode.BUSINESS_AREA,
        scope_id=scope_id,
        tools=(lookup_concept,),
        description=f"{len(concepts_by_name)} concept(s) in {scope_id!r}.",
    )
