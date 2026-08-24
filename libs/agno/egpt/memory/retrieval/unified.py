"""One retrieval interface spanning the KB and memory, source-labeled (SM-S4, #121).

KB (``knowledge/``, curated/static/read-only) and memory (dynamic/write-heavy/temporal) differ on
mutability, time, and ownership (docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md) and stay
separate stores -- decisions D7/D8, ADR 0003. This module does not touch either store; it composes
:class:`~egpt.memory.provider.MemoryProvider` and :class:`~egpt.knowledge.protocol.KnowledgeProtocol`,
both already real, behind one call that labels each result by the store it came from.

Ranking the two together is out of scope: a KB document's score is a vector-DB similarity (or
``None`` pre-rerank) and a memory record's may already be fused across substrates by the seam
(SM-H2, #132), so the two are not comparable yet. :class:`UnifiedSlice` keeps them apart rather
than interleaving by a comparison this ticket does not make.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from egpt.knowledge.protocol import KnowledgeProtocol
from egpt.memory.provider import MemoryProvider
from egpt.memory.types import MemoryQuery, MemoryRecord, Scope

__all__ = ["RetrievalSource", "UnifiedResult", "UnifiedSlice", "aunified_read", "unified_read"]


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


def _kb_result(document: Any) -> UnifiedResult:
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
        metadata={"tier": record.tier.value, **record.metadata},
    )


def unified_read(
    scope: Scope,
    query: MemoryQuery,
    *,
    memory: Optional[MemoryProvider] = None,
    knowledge: Optional[KnowledgeProtocol] = None,
    kb_filters: Optional[Any] = None,
) -> UnifiedSlice:
    """Fan ``query`` out across the KB and memory, each result labeled by its source.

    ``query.text`` and ``query.limit`` serve both legs -- one request envelope, not a KB-specific
    and a memory-specific query type, since the two already carry what each leg's own ``retrieve``/
    ``read`` needs.
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


async def aunified_read(
    scope: Scope,
    query: MemoryQuery,
    *,
    memory: Optional[MemoryProvider] = None,
    knowledge: Optional[KnowledgeProtocol] = None,
    kb_filters: Optional[Any] = None,
) -> UnifiedSlice:
    """Async variant of :func:`unified_read`."""
    kb_results: List[UnifiedResult] = []
    if knowledge is not None:
        documents = await knowledge.aretrieve(query.text, max_results=query.limit, filters=kb_filters)
        kb_results = [_kb_result(document) for document in documents]

    memory_results: List[UnifiedResult] = []
    if memory is not None:
        memory_slice = await memory.aread(scope, query)
        memory_results = [_memory_result(record) for record in memory_slice.records]

    return UnifiedSlice(kb=kb_results, memory=memory_results)
