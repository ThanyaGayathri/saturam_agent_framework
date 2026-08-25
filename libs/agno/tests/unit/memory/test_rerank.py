"""The rerank stage (SM-H3 #133): the pure functions, independent of the provider.

``test_provider_co_located.py`` covers the wiring -- ``config.rerank`` toggling the stage on,
Mode-A rejection of a network reranker, the diagnostics landing in ``MemorySlice.fusion``. This
file is only about what ``rerank_records``/``arerank_records`` themselves decide: reordering,
budget enforcement (abandon, not just measure), the tail past ``candidates``, and graceful
fallback to the fused order on every kind of reranker failure.
"""

import asyncio
import time

from egpt.integrations.knowledge.reranker.base import Reranker
from egpt.memory.retrieval.rerank import DEFAULT_BUDGET_MS, DEFAULT_CANDIDATES, arerank_records, rerank_records
from egpt.memory.types import MemoryRecord, MemoryTier, Scope

ACME = Scope(tenant_id="acme")


def record(id_, content=None, score=None):
    return MemoryRecord(
        id=id_, tier=MemoryTier.EPISODIC, content=content if content is not None else id_, scope=ACME, score=score
    )


class ReverseReranker(Reranker):
    """A deterministic double: reverses order, scoring by the new (reversed) position."""

    def rerank(self, query, documents):
        out = list(reversed(documents))
        for index, doc in enumerate(out):
            doc.reranking_score = float(len(out) - index)
        return out


class AsyncOnlyReverseReranker(Reranker):
    """Overrides ``arerank`` directly, the way ``InfinityReranker`` does -- exercises the branch
    that does *not* fall through to the base class's thread-offload default."""

    def rerank(self, query, documents):
        raise AssertionError("sync rerank must not be called when arerank is overridden")

    async def arerank(self, query, documents):
        out = list(reversed(documents))
        for index, doc in enumerate(out):
            doc.reranking_score = float(len(out) - index)
        return out


class SlowReranker(Reranker):
    delay_s: float = 0.5

    def rerank(self, query, documents):
        time.sleep(self.delay_s)
        return documents


class AsyncSlowReranker(Reranker):
    delay_s: float = 0.5

    async def arerank(self, query, documents):
        await asyncio.sleep(self.delay_s)
        return documents


class BoomReranker(Reranker):
    def rerank(self, query, documents):
        raise RuntimeError("boom")


class AsyncBoomReranker(Reranker):
    async def arerank(self, query, documents):
        raise RuntimeError("boom")


class WrongCountReranker(Reranker):
    """A misbehaving adapter: drops a document rather than returning a permutation."""

    def rerank(self, query, documents):
        return documents[:-1]


def test_defaults_match_sm_r8_decision_md():
    assert DEFAULT_CANDIDATES == 50
    assert DEFAULT_BUDGET_MS == 300.0


def test_reranking_reorders_and_carries_the_new_score():
    records = [record(str(i)) for i in range(1, 4)]

    outcome = rerank_records("q", records, reranker=ReverseReranker(), budget_ms=1000)

    assert [r.id for r in outcome.records] == ["3", "2", "1"]
    assert [r.score for r in outcome.records] == [3.0, 2.0, 1.0]
    assert outcome.applied is True
    assert outcome.budget_exceeded is False
    assert outcome.candidates == 3


async def test_async_reranking_agrees_with_sync_for_a_base_class_offload_reranker():
    records = [record(str(i)) for i in range(1, 4)]

    sync_outcome = rerank_records("q", records, reranker=ReverseReranker(), budget_ms=1000)
    async_outcome = await arerank_records("q", records, reranker=ReverseReranker(), budget_ms=1000)

    assert [r.id for r in async_outcome.records] == [r.id for r in sync_outcome.records]


async def test_async_reranking_uses_a_rerankers_own_arerank_override_not_the_sync_path():
    records = [record(str(i)) for i in range(1, 4)]

    outcome = await arerank_records("q", records, reranker=AsyncOnlyReverseReranker(), budget_ms=1000)

    assert [r.id for r in outcome.records] == ["3", "2", "1"]
    assert outcome.applied is True


def test_records_past_candidates_are_left_in_fused_order_untouched():
    records = [record(str(i)) for i in range(1, 6)]

    outcome = rerank_records("q", records, reranker=ReverseReranker(), candidates=3, budget_ms=1000)

    assert [r.id for r in outcome.records[:3]] == ["3", "2", "1"]  # reranked pool
    assert [r.id for r in outcome.records[3:]] == ["4", "5"]  # tail, fused order preserved
    assert outcome.candidates == 3


def test_a_budget_overrun_is_enforced_by_abandoning_the_call_not_by_measuring_it():
    records = [record(str(i)) for i in range(1, 4)]

    start = time.monotonic()
    outcome = rerank_records("q", records, reranker=SlowReranker(delay_s=1.0), budget_ms=50)
    wall_ms = (time.monotonic() - start) * 1000.0

    assert outcome.applied is False
    assert outcome.budget_exceeded is True
    assert [r.id for r in outcome.records] == ["1", "2", "3"]  # fused order, unchanged
    # The caller gets control back near the budget, not after the reranker's full 1s -- this is
    # the "abandon, don't just time" contract the module docstring promises.
    assert wall_ms < 500


async def test_an_async_budget_overrun_is_also_enforced_by_abandoning_the_call():
    records = [record(str(i)) for i in range(1, 4)]

    start = time.monotonic()
    outcome = await arerank_records("q", records, reranker=AsyncSlowReranker(delay_s=1.0), budget_ms=50)
    wall_ms = (time.monotonic() - start) * 1000.0

    assert outcome.applied is False
    assert outcome.budget_exceeded is True
    assert [r.id for r in outcome.records] == ["1", "2", "3"]
    assert wall_ms < 500


def test_a_reranker_exception_falls_back_to_fused_order_rather_than_propagating():
    records = [record(str(i)) for i in range(1, 4)]

    outcome = rerank_records("q", records, reranker=BoomReranker(), budget_ms=1000)

    assert outcome.applied is False
    assert outcome.budget_exceeded is False
    assert [r.id for r in outcome.records] == ["1", "2", "3"]


async def test_an_async_reranker_exception_also_falls_back_to_fused_order():
    records = [record(str(i)) for i in range(1, 4)]

    outcome = await arerank_records("q", records, reranker=AsyncBoomReranker(), budget_ms=1000)

    assert outcome.applied is False
    assert [r.id for r in outcome.records] == ["1", "2", "3"]


def test_a_reranker_returning_a_non_permutation_falls_back_rather_than_returning_a_short_page():
    records = [record(str(i)) for i in range(1, 4)]

    outcome = rerank_records("q", records, reranker=WrongCountReranker(), budget_ms=1000)

    assert outcome.applied is False
    assert len(outcome.records) == 3


def test_reranking_an_empty_list_is_a_no_op():
    outcome = rerank_records("q", [], reranker=ReverseReranker(), budget_ms=1000)

    assert outcome.records == []
    assert outcome.applied is False
    assert outcome.candidates == 0


def test_reranking_does_not_mutate_the_input_records():
    original = record("a", score=0.5)

    rerank_records("q", [original], reranker=ReverseReranker(), budget_ms=1000)

    assert original.score == 0.5


def test_diagnostics_carries_every_key_fusion_diagnostics_folds_in():
    records = [record(str(i)) for i in range(1, 4)]
    outcome = rerank_records("q", records, reranker=ReverseReranker(), budget_ms=1000)

    diagnostics = outcome.diagnostics()

    assert set(diagnostics) == {
        "rerank_applied",
        "rerank_budget_exceeded",
        "rerank_candidates",
        "rerank_elapsed_ms",
    }


def test_zero_candidates_yields_no_pool_to_rerank():
    """A positive ``candidates`` is enforced at ``MemoryProviderConfig`` construction
    (``_validate_rerank``); this pins the function's own behavior at the boundary value."""
    records = [record(str(i)) for i in range(1, 4)]

    outcome = rerank_records("q", records, reranker=ReverseReranker(), candidates=0, budget_ms=1000)

    assert outcome.applied is False
    assert outcome.candidates == 0
    assert [r.id for r in outcome.records] == ["1", "2", "3"]


def test_a_reranker_that_drops_the_reranking_score_keeps_the_records_fused_score():
    """`SentenceTransformerReranker.rerank`'s own exception handler returns the *input* documents
    unmodified on a construction failure (`reranking_score` stays `None`) -- this must not be
    read as "reranked to a null score", or a real quality regression in the reranker itself would
    silently zero out every downstream ranking signal."""

    class PassthroughReranker(Reranker):
        def rerank(self, query, documents):
            return documents  # unmodified: reranking_score left at its Document default (None)

    records = [record("a", score=0.7)]

    outcome = rerank_records("q", records, reranker=PassthroughReranker(), budget_ms=1000)

    assert outcome.records[0].score == 0.7
    assert outcome.applied is True


def test_document_ids_travel_through_reranking_unaltered():
    """A regression guard for the id-based join in ``_reordered``: content is deliberately
    identical across records here, so if the join fell back to matching on content this test
    would still pass by accident."""
    records = [record("a", content="same"), record("b", content="same")]

    outcome = rerank_records("q", records, reranker=ReverseReranker(), budget_ms=1000)

    assert {r.id for r in outcome.records} == {"a", "b"}
