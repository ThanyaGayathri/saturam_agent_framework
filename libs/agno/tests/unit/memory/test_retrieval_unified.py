"""The unified KB + memory retrieval interface (SM-S4, #121).

The property under test: KB and memory stay separate stores, one call reaches both, and every
result is labeled by the store it came from -- the issue's three acceptance criteria, each with a
test that fails without the code (a merged-store or unlabeled-result regression would fail these).
"""

from typing import Any, Dict, List, Optional, Sequence

import pytest

from egpt.knowledge.document import Document
from egpt.memory.provider import MemoryProvider
from egpt.memory.retrieval import RetrievalSource, UnifiedResult, UnifiedSlice, aunified_read, unified_read
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    ConsolidationReport,
    MemoryEvent,
    MemoryQuery,
    MemoryRecord,
    MemorySlice,
    Scope,
    Substrate,
    WriteReceipt,
)

SCOPE = Scope(tenant_id="acme")


class RecordingProvider(MemoryProvider):
    """A memory leg that reports exactly the scope/query it was asked with.

    Only ``read``/``aread`` are exercised by the module under test; the rest raise so a call this
    module has no business making shows up as a test failure rather than a silent stub answer.
    """

    provider_key = "recording"

    def __init__(self, records: Sequence[MemoryRecord]) -> None:
        self.config = None
        self._records = list(records)
        self.calls: List[Any] = []

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        self.calls.append((scope, query))
        return MemorySlice(scope=scope, query=query, records=list(self._records))

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        return self.read(scope, query)

    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        raise NotImplementedError

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        raise NotImplementedError

    def consolidate(self, scope: Scope, tiers=None) -> ConsolidationReport:
        raise NotImplementedError

    async def aconsolidate(self, scope: Scope, tiers=None) -> ConsolidationReport:
        raise NotImplementedError

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        raise NotImplementedError

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        raise NotImplementedError

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        raise NotImplementedError

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        raise NotImplementedError

    def substrates(self) -> Dict[Substrate, bool]:
        return {Substrate.VECTOR: True, Substrate.GRAPH: False}

    def tiers(self) -> Dict[MemoryTier, bool]:
        return {tier: False for tier in MemoryTier}


class RecordingKnowledge:
    """A KB leg that reports exactly the query/max_results/filters it was asked with."""

    def __init__(self, documents: Sequence[Document]) -> None:
        self._documents = list(documents)
        self.calls: List[Any] = []

    def retrieve(
        self, query: str, max_results: Optional[int] = None, filters: Optional[Any] = None, **kwargs
    ) -> List[Document]:
        self.calls.append((query, max_results, filters))
        return list(self._documents)

    async def aretrieve(
        self, query: str, max_results: Optional[int] = None, filters: Optional[Any] = None, **kwargs
    ) -> List[Document]:
        return self.retrieve(query, max_results=max_results, filters=filters)


def _memory_record(record_id: str = "m1", content: str = "user prefers dark mode") -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        tier=MemoryTier.SEMANTIC,
        content=content,
        scope=SCOPE,
        score=0.9,
    )


def _document(doc_id: str = "d1", content: str = "the API rate limit is 100 rpm") -> Document:
    return Document(content=content, id=doc_id, reranking_score=0.7, meta_data={"page": 3})


# ---------------------------------------------------------------------------
# AC1 -- KB and memory are separate stores (no merging, each queried independently)
# ---------------------------------------------------------------------------


def test_kb_and_memory_are_queried_as_independent_stores():
    knowledge = RecordingKnowledge([_document()])
    memory = RecordingProvider([_memory_record()])
    query = MemoryQuery(text="rate limit", limit=5)

    unified_read(SCOPE, query, memory=memory, knowledge=knowledge)

    assert knowledge.calls == [("rate limit", 5, None)]
    assert memory.calls == [(SCOPE, query)]


def test_a_leg_with_no_store_wired_is_empty_not_absent():
    memory = RecordingProvider([_memory_record()])
    query = MemoryQuery(text="rate limit", limit=5)

    result = unified_read(SCOPE, query, memory=memory, knowledge=None)

    assert result.kb == []
    assert len(result.memory) == 1


def test_neither_leg_wired_returns_an_empty_slice():
    result = unified_read(SCOPE, MemoryQuery(text="anything"))
    assert result == UnifiedSlice(kb=[], memory=[])


# ---------------------------------------------------------------------------
# AC2 -- one retrieval interface spans both
# ---------------------------------------------------------------------------


def test_results_property_carries_both_legs_kb_first():
    knowledge = RecordingKnowledge([_document()])
    memory = RecordingProvider([_memory_record()])

    result = unified_read(SCOPE, MemoryQuery(text="q"), memory=memory, knowledge=knowledge)

    assert [r.source for r in result.results] == [RetrievalSource.KB, RetrievalSource.MEMORY]


@pytest.mark.asyncio
async def test_async_variant_reaches_both_legs_the_same_way():
    knowledge = RecordingKnowledge([_document()])
    memory = RecordingProvider([_memory_record()])
    query = MemoryQuery(text="rate limit", limit=5)

    result = await aunified_read(SCOPE, query, memory=memory, knowledge=knowledge)

    assert knowledge.calls == [("rate limit", 5, None)]
    assert memory.calls == [(SCOPE, query)]
    assert [r.source for r in result.results] == [RetrievalSource.KB, RetrievalSource.MEMORY]


# ---------------------------------------------------------------------------
# AC3 -- source is labeled on results, and a KB fact vs a memory fact are distinguishable
# ---------------------------------------------------------------------------


def test_a_kb_fact_and_a_memory_fact_are_distinguishable_by_source():
    knowledge = RecordingKnowledge([_document(content="the API rate limit is 100 rpm")])
    memory = RecordingProvider([_memory_record(content="user prefers dark mode")])

    result = unified_read(SCOPE, MemoryQuery(text="q"), memory=memory, knowledge=knowledge)

    by_content = {r.content: r.source for r in result.results}
    assert by_content["the API rate limit is 100 rpm"] == RetrievalSource.KB
    assert by_content["user prefers dark mode"] == RetrievalSource.MEMORY


def test_kb_result_carries_document_identity_and_score():
    knowledge = RecordingKnowledge([_document(doc_id="d1")])
    result = unified_read(SCOPE, MemoryQuery(text="q"), knowledge=knowledge)

    [kb_result] = result.kb
    assert kb_result == UnifiedResult(
        content="the API rate limit is 100 rpm",
        source=RetrievalSource.KB,
        score=0.7,
        id="d1",
        metadata={"page": 3},
    )


def test_memory_result_carries_record_identity_tier_and_score():
    memory = RecordingProvider([_memory_record(record_id="m1")])
    result = unified_read(SCOPE, MemoryQuery(text="q"), memory=memory)

    [memory_result] = result.memory
    assert memory_result.id == "m1"
    assert memory_result.source == RetrievalSource.MEMORY
    assert memory_result.score == 0.9
    assert memory_result.metadata["tier"] == MemoryTier.SEMANTIC.value
