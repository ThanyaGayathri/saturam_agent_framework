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
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from egpt.knowledge.document import Document
from egpt.memory.provider import MemoryProvider
from egpt.memory.types import MemoryQuery, MemoryRecord, Scope

__all__ = [
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
        self, query: str, max_results: Optional[int] = None, filters: Optional[Any] = None, **kwargs: Any
    ) -> List[Document]: ...

    async def aretrieve(
        self, query: str, max_results: Optional[int] = None, filters: Optional[Any] = None, **kwargs: Any
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
    convention the memory seam's own substrate legs use (``RetrievalLegs``, SM-H1), so a caller
    can distinguish "no KB configured" from "KB configured but no match" by whether it passed
    ``knowledge`` in the first place, not by inspecting this result.
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
        id=document.id or document.content_id,
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


def unified_read(
    scope: Scope,
    query: MemoryQuery,
    *,
    memory: Optional[MemoryProvider] = None,
    knowledge: Optional[KnowledgeRetrievable] = None,
    kb_filters: Optional[Any] = None,
) -> UnifiedSlice:
    """Fan ``query`` out across the KB and memory, each result labeled by its source.

    ``query.text`` and ``query.limit`` serve both legs -- one request envelope, not a KB-specific
    and a memory-specific query type, since the two already carry what each leg's own ``retrieve``/
    ``read`` needs.

    Args:
        kb_filters: Forwarded to ``knowledge.retrieve`` only -- there is no memory-side
            equivalent, so the memory leg always reads with the unfiltered ``query``. Pass
            ``None`` (the default) to retrieve from the KB without filtering.
    """
    kb_results: List[UnifiedResult] = []
    if knowledge is not None:
        documents = knowledge.retrieve(query.text, max_results=query.limit, filters=kb_filters)
        kb_results = [_kb_result(document) for document in documents]

    memory_results: List[UnifiedResult] = []
    if memory is not None:
        memory_slice = memory.read(scope, query)
        memory_results = [_memory_result(record) for record in memory_slice.records]

    return UnifiedSlice(kb=kb_results, memory=memory_results)


async def _akb_leg(
    knowledge: Optional[KnowledgeRetrievable], query: MemoryQuery, kb_filters: Optional[Any]
) -> List[UnifiedResult]:
    if knowledge is None:
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
    kb_filters: Optional[Any] = None,
) -> UnifiedSlice:
    """Async variant of :func:`unified_read`.

    The two legs share no state and have no ordering dependency, so they run concurrently via
    ``asyncio.gather`` rather than one awaited after the other.
    """
    kb_results, memory_results = await asyncio.gather(
        _akb_leg(knowledge, query, kb_filters),
        _amemory_leg(memory, scope, query),
    )
    return UnifiedSlice(kb=kb_results, memory=memory_results)
