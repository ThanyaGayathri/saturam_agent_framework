"""One retrieval interface spanning the KB and memory, source-labeled (SM-S4, #121).

KB (``knowledge/``, curated/static/read-only) and memory (dynamic/write-heavy/temporal) differ on
mutability, time, and ownership (docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md) and stay
separate stores -- decisions D7/D8, ADR 0003. This module does not touch either store; it composes
:class:`~egpt.memory.provider.MemoryProvider` and :class:`KnowledgeRetrievable` -- the retrieval
slice of :class:`~egpt.knowledge.protocol.KnowledgeProtocol` that a real ``Knowledge`` instance
already satisfies -- behind one call that labels each result by the store it came from.

Ranking the two together is out of scope: a KB document's score is a vector-DB similarity (or
``None`` pre-rerank) and a memory record's may already be fused across substrates by the seam
(SM-H2, #132), so the two are not comparable yet. :class:`UnifiedSlice` keeps them apart rather
than interleaving by a comparison this ticket does not make.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Union, runtime_checkable

from egpt.filters import FilterExpr
from egpt.knowledge.document import Document
from egpt.memory.provider import MemoryProvider
from egpt.memory.types import MemoryQuery, MemoryRecord, Scope

#: What ``knowledge.retrieve``/``aretrieve`` actually accept as ``filters`` (matches
#: ``Knowledge.retrieve``, ``knowledge/knowledge.py``) -- narrower than ``Any`` so a caller
#: passing e.g. a bare string is a type error here rather than a runtime surprise inside the leg.
KbFilters = Optional[Union[Dict[str, Any], List[FilterExpr]]]

__all__ = [
    "KbFilters",
    "KnowledgeRetrievable",
    "RetrievalSource",
    "UnifiedResult",
    "UnifiedSlice",
    "aunified_read",
    "unified_read",
]


@runtime_checkable
class KnowledgeRetrievable(Protocol):
    """The KB leg's actual contract -- narrower than :class:`~egpt.knowledge.protocol.KnowledgeProtocol`.

    The full protocol also requires ``build_context``/``get_tools``/``aget_tools``, none of which
    this module calls; annotating against it would reject a KB stand-in that implements only
    retrieval. And ``KnowledgeProtocol.retrieve``/``aretrieve`` declare ``**kwargs`` only, so a
    conformant minimal implementation can silently drop ``max_results`` -- this protocol declares
    the parameters ``unified_read``/``aunified_read`` actually pass, so mypy catches a leg that
    does not honour them.
    """

    def retrieve(
        self, query: str, max_results: Optional[int] = None, filters: KbFilters = None, **kwargs: Any
    ) -> List[Document]: ...

    async def aretrieve(
        self, query: str, max_results: Optional[int] = None, filters: KbFilters = None, **kwargs: Any
    ) -> List[Document]: ...


class RetrievalSource(str, Enum):
    """Which store a :class:`UnifiedResult` came from."""

    KB = "kb"
    MEMORY = "memory"


@dataclass(frozen=True)
class UnifiedResult:
    """One result, labeled by the store it came from."""

    content: str
    source: RetrievalSource
    score: Optional[float] = None
    id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UnifiedSlice:
    """The fan-out result: KB and memory results held apart, plus one labeled view of both.

    A leg with no store wired is an empty list, not an absent one -- the same "did not run"
    convention the memory seam's own substrate legs use (``RetrievalCandidates``, returned by
    ``VectorSubstrate.search_candidates``, ``memory/types.py``), so a caller can distinguish "no
    KB configured" from "KB configured but no match" by whether it passed ``knowledge`` in the
    first place, not by inspecting this result.
    """

    kb: List[UnifiedResult] = field(default_factory=list)
    memory: List[UnifiedResult] = field(default_factory=list)

    @property
    def results(self) -> List[UnifiedResult]:
        """KB results followed by memory results, each still carrying its own ``source``."""
        return [*self.kb, *self.memory]


def _kb_result(document: Document) -> UnifiedResult:
    return UnifiedResult(
        content=document.content,
        source=RetrievalSource.KB,
        score=document.reranking_score,
        id=document.id if document.id is not None else document.content_id,
        metadata=dict(document.meta_data or {}),
    )


def _memory_result(record: MemoryRecord) -> UnifiedResult:
    return UnifiedResult(
        content=record.content,
        source=RetrievalSource.MEMORY,
        score=record.score,
        id=record.id,
        metadata={**record.metadata, "tier": record.tier.value},
    )


def _kb_leg(
    knowledge: Optional[KnowledgeRetrievable], query: MemoryQuery, kb_filters: KbFilters
) -> List[UnifiedResult]:
    # ``limit=0`` must mean "zero results", but ``Knowledge.search`` computes
    # ``max_results or self.max_results`` -- 0 is falsy, so a caller asking for nothing would
    # silently get the KB's default page instead. Handled here, at the boundary, rather than in
    # ``Knowledge`` (a shared file every other consumer of it also depends on).
    if knowledge is None or query.limit == 0:
        return []
    documents = knowledge.retrieve(query.text, max_results=query.limit, filters=kb_filters)
    return [_kb_result(document) for document in documents]


def _memory_leg(memory: Optional[MemoryProvider], scope: Scope, query: MemoryQuery) -> List[UnifiedResult]:
    if memory is None:
        return []
    memory_slice = memory.read(scope, query)
    return [_memory_result(record) for record in memory_slice.records]


def unified_read(
    scope: Scope,
    query: MemoryQuery,
    *,
    memory: Optional[MemoryProvider] = None,
    knowledge: Optional[KnowledgeRetrievable] = None,
    kb_filters: KbFilters = None,
) -> UnifiedSlice:
    """Fan ``query`` out across the KB and memory, each result labeled by its source.

    ``query.text`` and ``query.limit`` serve both legs -- one request envelope, not a KB-specific
    and a memory-specific query type, since the two already carry what each leg's own ``retrieve``/
    ``read`` needs.

    Both legs are always attempted, even if one raises: the two stores are independent, so one
    failing must not silently discard the other's, already-fetched, successful results. If a leg
    raises, its exception is re-raised after both legs have run -- KB's before memory's, if both
    raised. See :func:`aunified_read` for why the async variant needs the same "run both, then
    raise" shape for an additional reason (an un-awaited task, not just a discarded result).

    Args:
        kb_filters: Forwarded to ``knowledge.retrieve`` only -- there is no memory-side
            equivalent, so the memory leg always reads with the unfiltered ``query``. Pass
            ``None`` (the default) to retrieve from the KB without filtering.
    """
    kb_results: List[UnifiedResult] = []
    kb_error: Optional[Exception] = None
    try:
        kb_results = _kb_leg(knowledge, query, kb_filters)
    except Exception as exc:
        kb_error = exc

    memory_results: List[UnifiedResult] = []
    memory_error: Optional[Exception] = None
    try:
        memory_results = _memory_leg(memory, scope, query)
    except Exception as exc:
        memory_error = exc

    if kb_error is not None:
        raise kb_error
    if memory_error is not None:
        raise memory_error

    return UnifiedSlice(kb=kb_results, memory=memory_results)


async def _akb_leg(
    knowledge: Optional[KnowledgeRetrievable], query: MemoryQuery, kb_filters: KbFilters
) -> List[UnifiedResult]:
    if knowledge is None or query.limit == 0:
        return []
    documents = await knowledge.aretrieve(query.text, max_results=query.limit, filters=kb_filters)
    return [_kb_result(document) for document in documents]


async def _amemory_leg(memory: Optional[MemoryProvider], scope: Scope, query: MemoryQuery) -> List[UnifiedResult]:
    if memory is None:
        return []
    memory_slice = await memory.aread(scope, query)
    return [_memory_result(record) for record in memory_slice.records]


async def aunified_read(
    scope: Scope,
    query: MemoryQuery,
    *,
    memory: Optional[MemoryProvider] = None,
    knowledge: Optional[KnowledgeRetrievable] = None,
    kb_filters: KbFilters = None,
) -> UnifiedSlice:
    """Async variant of :func:`unified_read`.

    The two legs share no state and have no ordering dependency, so they run concurrently via
    ``asyncio.gather`` rather than one awaited after the other. ``return_exceptions=True`` so a
    raising leg does not leave the other running un-awaited -- ``gather``'s default behaviour
    does not cancel a sibling task when one raises, so without this the surviving task keeps
    running with no one left to observe its result or a later exception. Both legs are awaited to
    completion, then the first exception (KB's before memory's) is re-raised, matching the sync
    variant's ordering.
    """
    kb_outcome, memory_outcome = await asyncio.gather(
        _akb_leg(knowledge, query, kb_filters),
        _amemory_leg(memory, scope, query),
        return_exceptions=True,
    )
    if isinstance(kb_outcome, BaseException):
        raise kb_outcome
    if isinstance(memory_outcome, BaseException):
        raise memory_outcome
    return UnifiedSlice(kb=kb_outcome, memory=memory_outcome)
