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
from egpt_retail.app.stores import open_knowledge_stores
from egpt_retail.executors.gold import read_gold
from egpt_retail.ontology.model import ARTIFACT_PATH, load_ontology
from egpt_retail.retrieval.legs import aretail_unified_read

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
) -> ModeScope:
    """The bounded tool set for one mode. Synchronous: the only I/O at resolution time is
    ``business_area``'s ontology file read (local disk, not the network); everything else
    below constructs closures that open their own connection per call, not at resolution
    time, so a chat session never holds a driver or engine open across turns."""
    settings = settings or RetailSettings()
    if mode is PlaygroundMode.DATA:
        return _data_scope()
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
) -> ModeScope:
    """:func:`resolve_mode_scope` off the event loop."""
    return await asyncio.to_thread(
        resolve_mode_scope, mode, scope_id, settings=settings, user_id=user_id
    )


def _data_scope() -> ModeScope:
    """Semantic-layer-grounded: gold-only reads through R6's guard, the same seam R13's
    dashboards read through (``executors/gold.py``'s own "the seam an interactive playground
    reads through")."""

    async def read_kpi_data(kpi_identifiers: List[str]) -> Dict[str, Any]:
        """Materialised values for the named KPIs, through the guard. A KPI with nothing
        materialised is listed under ``missing`` with why, never invented."""
        step_input = StepInput(additional_data={"kpi_identifiers": kpi_identifiers})
        output = await read_gold(step_input)
        # Narrowing cast, not an assumption: `read_gold` always returns a dict content.
        return cast(Dict[str, Any], output.content)

    return ModeScope(
        mode=PlaygroundMode.DATA,
        scope_id=None,
        tools=(read_kpi_data,),
        description="Gold-only reads over every ready KPI, through R6's guard.",
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
                try:
                    with open_knowledge_stores(settings, driver=driver) as stores:
                        result = await aretail_unified_read(
                            scope,
                            mem_query,
                            memory=memory,
                            knowledge=stores.knowledge,
                            ontology=stores.ontology,
                            semantic=reader,
                        )
                except Exception as exc:  # noqa: BLE001 -- a missing leg is not a failed answer
                    log_warning(
                        "playground knowledge mode: the prose and ontology legs could not be "
                        f"built ({exc.__class__.__name__}: {exc}); answering from the "
                        "semantic and memory legs only."
                    )
                    result = await aretail_unified_read(
                        scope, mem_query, memory=memory, semantic=reader
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
