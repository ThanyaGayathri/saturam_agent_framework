"""The unified KB + memory retrieval interface (SM-S4, #121).

The property under test: KB and memory stay separate stores, one call reaches both, and every
result is labeled by the store it came from -- the issue's three acceptance criteria, each with a
test that fails without the code (a merged-store or unlabeled-result regression would fail these).
"""

import asyncio
from typing import Any, Dict, List, Optional, Sequence

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


class RaisingKnowledge:
    """A KB leg that always fails, recording whether it was ever called."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.called = False

    def retrieve(
        self, query: str, max_results: Optional[int] = None, filters: Optional[Any] = None, **kwargs
    ) -> List[Document]:
        self.called = True
        raise self._error

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


def test_kb_filters_are_forwarded_to_the_kb_leg_only():
    knowledge = RecordingKnowledge([_document()])
    memory = RecordingProvider([_memory_record()])
    query = MemoryQuery(text="rate limit", limit=5)
    kb_filters = {"section": "api"}

    unified_read(SCOPE, query, memory=memory, knowledge=knowledge, kb_filters=kb_filters)

    assert knowledge.calls == [("rate limit", 5, {"section": "api"})]
    # No memory-side equivalent: the memory leg always reads the unfiltered query.
    assert memory.calls == [(SCOPE, query)]


def test_limit_zero_means_zero_kb_results_not_the_kb_default_page():
    # Knowledge.search computes `max_results or self.max_results`, so a naive forward of
    # limit=0 would silently fall back to the KB's own default page instead of returning nothing.
    knowledge = RecordingKnowledge([_document()])
    memory = RecordingProvider([_memory_record()])
    query = MemoryQuery(text="q", limit=0)

    result = unified_read(SCOPE, query, memory=memory, knowledge=knowledge)

    assert knowledge.calls == []  # the KB leg must not even be called
    assert result.kb == []


async def test_async_limit_zero_means_zero_kb_results():
    knowledge = RecordingKnowledge([_document()])
    result = await aunified_read(SCOPE, MemoryQuery(text="q", limit=0), knowledge=knowledge)

    assert knowledge.calls == []
    assert result.kb == []


# ---------------------------------------------------------------------------
# Fault isolation: one leg raising must not silently discard the other's result
# ---------------------------------------------------------------------------


def test_a_raising_kb_leg_does_not_prevent_the_memory_leg_from_running():
    knowledge = RaisingKnowledge(ValueError("kb boom"))
    memory = RecordingProvider([_memory_record()])

    try:
        unified_read(SCOPE, MemoryQuery(text="q"), memory=memory, knowledge=knowledge)
        assert False, "expected the KB leg's exception to propagate"
    except ValueError as exc:
        assert str(exc) == "kb boom"

    assert knowledge.called
    assert memory.calls == [(SCOPE, MemoryQuery(text="q"))]  # memory leg still ran


async def test_async_raising_kb_leg_still_lets_the_memory_leg_run_to_completion():
    knowledge = RaisingKnowledge(ValueError("kb boom"))
    memory = RecordingProvider([_memory_record()])

    try:
        await aunified_read(SCOPE, MemoryQuery(text="q"), memory=memory, knowledge=knowledge)
        assert False, "expected the KB leg's exception to propagate"
    except ValueError as exc:
        assert str(exc) == "kb boom"

    assert knowledge.called
    assert memory.calls == [(SCOPE, MemoryQuery(text="q"))]  # memory leg still ran, not orphaned


async def test_async_raising_kb_leg_does_not_leave_the_memory_leg_orphaned():
    # A slower memory leg that genuinely suspends, paired with a KB leg that raises with no
    # await at all -- without gather(..., return_exceptions=True), gather propagates the KB
    # exception as soon as it fires and does not wait for the still-sleeping memory task, so this
    # test would observe memory_finished as False if that flag were removed (verified by
    # mutation: reverting return_exceptions=True to the gather default makes this test flaky-to-
    # reliably-fail on a slow machine, and fails outright when the sleep is lengthened).
    memory_finished = False

    class SlowMemory(MemoryProvider):
        provider_key = "slow"
        config = None

        async def aread(self, scope, query):
            nonlocal memory_finished
            await asyncio.sleep(0.05)
            memory_finished = True
            return MemorySlice(scope=scope, query=query, records=[])

        def read(self, scope, query):
            raise NotImplementedError

        async def awrite(self, scope, events):
            raise NotImplementedError

        def write(self, scope, events):
            raise NotImplementedError

        def consolidate(self, scope, tiers=None):
            raise NotImplementedError

        async def aconsolidate(self, scope, tiers=None):
            raise NotImplementedError

        def read_tier(self, scope, tier, query):
            raise NotImplementedError

        async def aread_tier(self, scope, tier, query):
            raise NotImplementedError

        def write_tier(self, scope, tier, events):
            raise NotImplementedError

        async def awrite_tier(self, scope, tier, events):
            raise NotImplementedError

        def substrates(self):
            return {}

        def tiers(self):
            return {}

    knowledge = RaisingKnowledge(ValueError("kb boom"))

    try:
        await aunified_read(SCOPE, MemoryQuery(text="q"), memory=SlowMemory(), knowledge=knowledge)
        assert False, "expected the KB leg's exception to propagate"
    except ValueError:
        pass

    # aunified_read must not return control to the caller until the memory leg has actually
    # finished -- checked immediately, with no extra sleep on the test's side.
    assert memory_finished is True


def test_kb_error_wins_over_memory_error_when_both_legs_raise():
    knowledge = RaisingKnowledge(ValueError("kb boom"))

    class RaisingMemory(RecordingProvider):
        def read(self, scope, query):
            super().read(scope, query)
            raise RuntimeError("memory boom")

    memory = RaisingMemory([])

    try:
        unified_read(SCOPE, MemoryQuery(text="q"), memory=memory, knowledge=knowledge)
        assert False, "expected an exception"
    except ValueError as exc:
        assert str(exc) == "kb boom"

    assert knowledge.called
    assert len(memory.calls) == 1  # memory leg still ran despite the kb failure


# ---------------------------------------------------------------------------
# AC2 -- one retrieval interface spans both
# ---------------------------------------------------------------------------


def test_results_property_carries_both_legs_kb_first():
    knowledge = RecordingKnowledge([_document()])
    memory = RecordingProvider([_memory_record()])

    result = unified_read(SCOPE, MemoryQuery(text="q"), memory=memory, knowledge=knowledge)

    assert [r.source for r in result.results] == [RetrievalSource.KB, RetrievalSource.MEMORY]


async def test_async_variant_reaches_both_legs_the_same_way():
    knowledge = RecordingKnowledge([_document()])
    memory = RecordingProvider([_memory_record()])
    query = MemoryQuery(text="rate limit", limit=5)

    result = await aunified_read(SCOPE, query, memory=memory, knowledge=knowledge)

    assert knowledge.calls == [("rate limit", 5, None)]
    assert memory.calls == [(SCOPE, query)]
    assert [r.source for r in result.results] == [RetrievalSource.KB, RetrievalSource.MEMORY]


async def test_async_leg_with_no_store_wired_is_not_called_and_is_empty():
    knowledge = RecordingKnowledge([_document()])
    memory = RecordingProvider([_memory_record()])
    query = MemoryQuery(text="rate limit", limit=5)

    result = await aunified_read(SCOPE, query, memory=memory, knowledge=None)

    # The KB leg must not run at all, not merely return nothing.
    assert knowledge.calls == []
    assert result.kb == []
    assert len(result.memory) == 1


async def test_aunified_read_with_neither_leg_wired_returns_an_empty_slice():
    result = await aunified_read(SCOPE, MemoryQuery(text="anything"))
    assert result == UnifiedSlice(kb=[], memory=[])


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


def test_kb_result_id_falls_back_to_content_id_only_when_id_is_none_not_empty():
    knowledge = RecordingKnowledge([Document(content="c", id="", content_id="fallback")])
    result = unified_read(SCOPE, MemoryQuery(text="q"), knowledge=knowledge)

    [kb_result] = result.kb
    assert kb_result.id == ""  # an explicit empty string is not "missing"


def test_the_stamped_tier_wins_over_a_colliding_tier_key_in_record_metadata():
    record = MemoryRecord(
        id="m2",
        tier=MemoryTier.SEMANTIC,
        content="user prefers dark mode",
        scope=SCOPE,
        metadata={"tier": "not-a-real-tier"},
    )
    memory = RecordingProvider([record])

    result = unified_read(SCOPE, MemoryQuery(text="q"), memory=memory)

    [memory_result] = result.memory
    assert memory_result.metadata["tier"] == MemoryTier.SEMANTIC.value
