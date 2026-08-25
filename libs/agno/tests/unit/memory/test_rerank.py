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
from egpt.knowledge.document import Document
from egpt.memory.retrieval import rerank as rerank_module
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


class TopNReranker(Reranker):
    """A well-behaved adapter narrowing via its own ``top_n`` -- the idiomatic pattern every
    shipped `egpt` reranker supports (`aws_bedrock.py`'s own docstring shows `top_n=5` as the
    normal construction, not an edge case). Returns fewer, distinct documents -- not corruption."""

    top_n: int = 2

    def rerank(self, query, documents):
        return documents[: self.top_n]


class DuplicateIdReranker(Reranker):
    """A misbehaving adapter: returns one real document twice while silently dropping another,
    at the *same* output length as the input -- the corruption a naive length check cannot see."""

    def rerank(self, query, documents):
        return [documents[0], documents[0], *documents[1:-1]]


class AsyncDuplicateIdReranker(Reranker):
    async def arerank(self, query, documents):
        return [documents[0], documents[0], *documents[1:-1]]


class AllForeignIdsReranker(Reranker):
    """A misbehaving adapter: every returned id is foreign to the pool -- `_reordered` drops all
    of them, `_has_duplicate_pool_id` sees no *pool* id repeated (there are none), so neither
    round-3 check alone catches it. The output is a non-empty, duplicate-free `Document` list,
    which is exactly why a length- or duplicate-only check reports `applied=True` with zero real
    survivors (round-4 review finding)."""

    def rerank(self, query, documents):
        return [Document(id=f"not-a-pool-id-{i}", content=doc.content) for i, doc in enumerate(documents)]


class AsyncAllForeignIdsReranker(Reranker):
    async def arerank(self, query, documents):
        return [Document(id=f"not-a-pool-id-{i}", content=doc.content) for i, doc in enumerate(documents)]


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


def test_a_rerankers_own_top_n_truncation_is_accepted_not_treated_as_corruption():
    """Round-3 review finding: a naive length check against `pool` broke every reranker with
    `top_n` set, since that's an intentional narrowing to fewer, distinct records -- not the
    reranker failing to return a full permutation."""
    records = [record(str(i)) for i in range(1, 6)]

    outcome = rerank_records("q", records, reranker=TopNReranker(top_n=2), candidates=5, budget_ms=1000)

    assert outcome.applied is True
    assert len(outcome.records) == 2


def test_a_reranker_returning_a_duplicate_id_falls_back_rather_than_silently_dropping_a_record():
    """Round-3 review finding: same output *length* as the pool, but one real record is doubled
    while another vanishes -- a defect a length-only check cannot see."""
    records = [record(str(i)) for i in range(1, 4)]

    outcome = rerank_records("q", records, reranker=DuplicateIdReranker(), budget_ms=1000)

    assert outcome.applied is False
    assert [r.id for r in outcome.records] == ["1", "2", "3"]


def test_corrupted_reranker_output_is_logged_not_silent(monkeypatch):
    """Round-5 review finding: the corruption-fallback branch had no `log_warning` call, unlike
    the sibling budget-overrun and exception branches immediately above it -- so a genuinely
    corrupted response produced zero log signal, only the (correctly logged) no-op case did."""
    calls = []
    monkeypatch.setattr(rerank_module, "log_warning", lambda *args, **kwargs: calls.append(args))

    records = [record(str(i)) for i in range(1, 4)]
    rerank_records("q", records, reranker=DuplicateIdReranker(), budget_ms=1000)

    assert len(calls) == 1
    assert "corrupted" in calls[0][0].lower()


async def test_an_async_reranker_returning_a_duplicate_id_also_falls_back():
    records = [record(str(i)) for i in range(1, 4)]

    outcome = await arerank_records("q", records, reranker=AsyncDuplicateIdReranker(), budget_ms=1000)

    assert outcome.applied is False
    assert [r.id for r in outcome.records] == ["1", "2", "3"]


def test_a_reranker_returning_only_foreign_ids_falls_back_rather_than_reporting_zero_survivors_as_success():
    """Round-4 review finding: a non-empty, duplicate-free response that matches *no* pool id
    used to reach `applied=True` with an empty `records` list -- a data-loss bug disguised as
    success, since the round-3 duplicate check has nothing to catch here (there are no repeated
    *pool* ids -- there are no pool ids at all)."""
    records = [record(str(i)) for i in range(1, 4)]

    outcome = rerank_records("q", records, reranker=AllForeignIdsReranker(), budget_ms=1000)

    assert outcome.applied is False
    assert [r.id for r in outcome.records] == ["1", "2", "3"]


async def test_an_async_reranker_returning_only_foreign_ids_also_falls_back():
    records = [record(str(i)) for i in range(1, 4)]

    outcome = await arerank_records("q", records, reranker=AsyncAllForeignIdsReranker(), budget_ms=1000)

    assert outcome.applied is False
    assert [r.id for r in outcome.records] == ["1", "2", "3"]


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
        "rerank_no_op",
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


class PassthroughReranker(Reranker):
    """Mirrors `SentenceTransformerReranker.rerank`'s own exception handler: on an internal
    construction/connection failure it swallows the error and returns `documents` unmodified,
    `reranking_score` left at its `Document` default (`None`), rather than raising."""

    def rerank(self, query, documents):
        return documents


class AsyncPassthroughReranker(Reranker):
    async def arerank(self, query, documents):
        return documents


def test_a_reranker_that_silently_swallowed_its_own_failure_is_not_reported_as_applied():
    """Round-5 review finding: an adapter-internal failure that returns the input unchanged is a
    *valid* permutation (no duplicates, no foreign ids) -- `_rerank_corrupted` cannot see it, so
    it used to reach `applied=True`, indistinguishable from a reranker that genuinely ran and
    happened to agree with the fused order. Multi-record on purpose: a single-record pool makes
    "unchanged order" trivially true regardless of cause, which is exactly what let this through
    the first time."""
    records = [record(str(i), score=1.0 / i) for i in range(1, 4)]

    outcome = rerank_records("q", records, reranker=PassthroughReranker(), budget_ms=1000)

    assert outcome.applied is False
    assert outcome.no_op is True
    assert outcome.budget_exceeded is False
    # Falls back to the original fused records, scores included -- not "reranked to a null score".
    assert [r.id for r in outcome.records] == ["1", "2", "3"]
    assert [r.score for r in outcome.records] == [1.0, 0.5, 1.0 / 3]


async def test_an_async_reranker_that_silently_swallowed_its_own_failure_is_also_not_applied():
    records = [record(str(i)) for i in range(1, 4)]

    outcome = await arerank_records("q", records, reranker=AsyncPassthroughReranker(), budget_ms=1000)

    assert outcome.applied is False
    assert outcome.no_op is True
    assert [r.id for r in outcome.records] == ["1", "2", "3"]


def test_a_reranker_that_genuinely_agrees_with_the_fused_order_is_still_reported_as_applied():
    """The other half of the round-5 fix: unchanged order alone must not trigger the no-op signal
    -- a reranker that did real work and simply agreed with the fused ranking sets
    `reranking_score` on every document, which is what distinguishes it from a silent failure."""

    class AgreesButScoresReranker(Reranker):
        def rerank(self, query, documents):
            for index, doc in enumerate(documents):
                doc.reranking_score = float(len(documents) - index)  # same order, real scores
            return documents

    records = [record(str(i)) for i in range(1, 4)]

    outcome = rerank_records("q", records, reranker=AgreesButScoresReranker(), budget_ms=1000)

    assert outcome.applied is True
    assert outcome.no_op is False
    assert [r.id for r in outcome.records] == ["1", "2", "3"]


def test_the_sync_executor_is_a_bounded_module_level_singleton_not_one_per_call():
    """Round-3 review finding: a fresh `ThreadPoolExecutor` per call, abandoned on every budget
    overrun, leaks one OS thread per overrun -- unbounded. Pins the fix: `rerank_records` submits
    to the one shared, bounded, module-level pool rather than constructing its own -- spying on
    `_EXECUTOR.submit` itself, not just checking the module attribute is untouched, since a
    per-call executor built alongside it would leave the singleton's identity unchanged too."""
    real_submit = rerank_module._EXECUTOR.submit
    calls = []

    def spy_submit(*args, **kwargs):
        calls.append(1)
        return real_submit(*args, **kwargs)

    rerank_module._EXECUTOR.submit = spy_submit
    try:
        records = [record("a")]
        rerank_records("q", records, reranker=ReverseReranker(), budget_ms=1000)
        rerank_records("q", records, reranker=ReverseReranker(), budget_ms=1000)
    finally:
        rerank_module._EXECUTOR.submit = real_submit

    assert len(calls) == 2
    assert len(rerank_module._EXECUTOR._threads) == 4


def test_the_sync_executors_workers_are_daemon_threads():
    """Round-4 review finding: non-daemon worker threads block graceful process exit (CPython's
    `atexit` hook joins them, abandoned/in-flight work included). `_EXECUTOR`'s own type
    (`BoundedDaemonExecutor`) guarantees this; `egpt.utils.concurrency`'s own tests cover the
    mechanism in depth -- this just pins that the module-level singleton this stage actually uses
    is one."""
    assert all(t.daemon for t in rerank_module._EXECUTOR._threads)


def test_document_ids_travel_through_reranking_unaltered():
    """A regression guard for the id-based join in ``_reordered``: content is deliberately
    identical across records here, so if the join fell back to matching on content this test
    would still pass by accident."""
    records = [record("a", content="same"), record("b", content="same")]

    outcome = rerank_records("q", records, reranker=ReverseReranker(), budget_ms=1000)

    assert {r.id for r in outcome.records} == {"a", "b"}
