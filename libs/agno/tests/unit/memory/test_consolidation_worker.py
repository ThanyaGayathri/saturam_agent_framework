"""``ConsolidationWorker`` -- draining, retrying, and running off the hot path (SM-L1 #122).

The draining primitive itself (the provider's internal cursor) is pinned in
``test_provider_co_located.py``; what is pinned here is the loop built on top of it: bounded
per-tick draining, retry-on-failure, one scope's exhaustion not blocking another's, and the
background start/stop lifecycle.
"""

import asyncio

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig
from egpt.memory.consolidation import ConsolidationOutcome, ConsolidationWorker
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import ConsolidationReport, MemoryEvent, Scope
from tests.unit.memory._fakes import FakeGraph, FakeVector

ACME = Scope(tenant_id="acme", user_id="u1")
GLOBEX = Scope(tenant_id="globex", user_id="u1")


def event(content, **kwargs):
    return MemoryEvent(content=content, scope=kwargs.pop("scope", ACME), **kwargs)


def make_provider(max_episodes_per_pass=5000):
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located")
    provider = CoLocatedMemoryProvider(
        vector=vector, graph=graph, config=config, max_episodes_per_pass=max_episodes_per_pass
    )
    return provider, vector, graph


async def test_run_once_drains_a_tier_larger_than_one_pass():
    """A tier past the cap must be fully consolidated within one tick, not left for the caller to
    notice and re-invoke -- that is the whole point of a scheduler over a bounded pass.

    Mutation guard: if the worker only ever called ``aconsolidate`` once per scope (no drain
    loop), the union of source ids would stop at 3, not reach all 7.
    """
    provider, vector, _ = make_provider(max_episodes_per_pass=3)
    provider.write(ACME, [event(f"topic{i} unique content", id=f"e{i}") for i in range(7)])
    worker = ConsolidationWorker(provider, [ACME], max_passes_per_scope=10, retry_delay=0.001)

    outcomes = await worker.run_once()

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.error is None
    assert outcome.drained is True
    assert len(outcome.reports) == 3, "7 episodes at a cap of 3 is three passes: 3 + 3 + 1"
    seen = set()
    for fact in vector.rows["acme"].values():
        if fact.tier is MemoryTier.SEMANTIC:
            seen.update(fact.provenance.get("source_episode_ids", []))
    assert {f"e{i}" for i in range(7)} == seen


async def test_run_once_stops_within_the_per_scope_pass_cap():
    """A single scope's tail must not starve the tick indefinitely -- ``max_passes_per_scope`` is
    the safety valve, and a tier that needs more passes than that just rolls into the next tick.

    Mutation guard: with the cap not enforced (an unbounded ``while``), this test would still
    pass -- it is pinned by asserting the exact pass count and `drained is False`, not merely that
    the call returns.
    """
    provider, _, _ = make_provider(max_episodes_per_pass=1)
    provider.write(ACME, [event(f"topic{i} unique content", id=f"e{i}") for i in range(10)])
    worker = ConsolidationWorker(provider, [ACME], max_passes_per_scope=4, retry_delay=0.001)

    outcomes = await worker.run_once()

    outcome = outcomes[0]
    assert outcome.error is None
    assert len(outcome.reports) == 4
    assert outcome.drained is False


async def test_a_second_tick_continues_where_the_first_left_off():
    """Two ticks on one worker must together drain what one tick's cap left pending -- the
    provider's cursor (SM-L1 #122) is what makes the second tick's pass pick up the tail rather
    than re-reading the first eight episodes.

    Cap 4, per-tick pass budget 2: tick one reads e0-3 then e4-7 (8 of 10, still incomplete --
    each pass hit the cap exactly). Tick two reads the remaining e8-9 in a single pass under the
    cap, which reports `complete` immediately.
    """
    provider, vector, _ = make_provider(max_episodes_per_pass=4)
    provider.write(ACME, [event(f"topic{i} unique content", id=f"e{i}") for i in range(10)])
    worker = ConsolidationWorker(provider, [ACME], max_passes_per_scope=2, retry_delay=0.001)

    first = await worker.run_once()
    assert first[0].drained is False
    second = await worker.run_once()
    assert second[0].drained is True

    seen = set()
    for fact in vector.rows["acme"].values():
        if fact.tier is MemoryTier.SEMANTIC:
            seen.update(fact.provenance.get("source_episode_ids", []))
    assert {f"e{i}" for i in range(10)} == seen


def test_drained_reads_as_false_rather_than_raising_when_complete_is_missing():
    """`ConsolidationWorker` takes a `MemoryProvider` by protocol, not by concrete type, so a
    third-party provider that omits `details["complete"]` (CONSOLIDATION_KEYS requires it, but
    nothing here enforces that on a caller-supplied provider) must not crash the tick with a
    `KeyError` -- it should just read as "not drained, worth another pass".

    Mutation guard: replacing `details.get("complete", False)` with a bare `details["complete"]`
    makes this raise instead of returning `False`.
    """
    outcome = ConsolidationOutcome(
        scope=ACME,
        reports=[ConsolidationReport(scope=ACME, details={"entities": "not extracted"})],
    )
    assert outcome.drained is False


async def test_an_incomplete_entity_extraction_tail_keeps_the_scope_undrained():
    """The interface's own warning: a scheduler reading only the top-level `complete` misses a
    truncated entity-extraction pass. `drained` must read `details["entities"]["complete"]` too.

    Mutation guard: with `ConsolidationOutcome.drained` checking only `details["complete"]`, this
    passes on the first report instead of looping until the entity tail also clears.
    """
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])
    calls = {"n": 0}
    real_aconsolidate = provider.aconsolidate

    async def truncated_entities(scope, tiers=None):
        calls["n"] += 1
        report = await real_aconsolidate(scope, tiers)
        if calls["n"] < 3:
            report.details["entities"] = {"complete": False}
        return report

    provider.aconsolidate = truncated_entities
    worker = ConsolidationWorker(provider, [ACME], max_passes_per_scope=5, retry_delay=0.001)

    outcome = (await worker.run_once())[0]

    assert calls["n"] == 3, "the worker must keep calling aconsolidate while the entity tail is open"
    assert outcome.drained is True
    assert len(outcome.reports) == 3


async def test_a_transient_failure_is_retried_and_recovers():
    """The idempotency contract makes a mid-pass failure safe to just retry -- no special
    handling, no rollback, the same call again."""
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])
    calls = {"n": 0}
    real_aconsolidate = provider.aconsolidate

    async def flaky(scope, tiers=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient backend blip")
        return await real_aconsolidate(scope, tiers)

    provider.aconsolidate = flaky
    worker = ConsolidationWorker(provider, [ACME], max_retries=2, retry_delay=0.001)

    outcomes = await worker.run_once()

    assert calls["n"] == 2
    assert outcomes[0].error is None
    assert outcomes[0].reports[0].episodic_scanned == 1


async def test_retries_exhausted_reports_error_without_crashing_other_scopes():
    """One tenant's backend being down for good must not stop another tenant's drain this tick."""
    provider, _, _ = make_provider()
    provider.write(ACME, [event("acme's episode")])
    provider.write(GLOBEX, [event("globex's episode", scope=GLOBEX)])

    async def always_fails(scope, tiers=None):
        raise RuntimeError("backend unreachable")

    real_aconsolidate = provider.aconsolidate

    async def per_scope(scope, tiers=None):
        if scope == ACME:
            return await always_fails(scope, tiers)
        return await real_aconsolidate(scope, tiers)

    provider.aconsolidate = per_scope
    worker = ConsolidationWorker(provider, [ACME, GLOBEX], max_retries=1, retry_delay=0.001)

    outcomes = await worker.run_once()

    by_scope = {o.scope: o for o in outcomes}
    assert by_scope[ACME].error is not None
    assert by_scope[ACME].reports == []
    assert by_scope[GLOBEX].error is None
    assert by_scope[GLOBEX].reports[0].episodic_scanned == 1


async def test_idempotent_across_a_fresh_provider_instance():
    """Restarting the worker process loses the in-memory cursor, not correctness: a fresh
    provider instance over the same substrates rescans from the beginning and upserts the same
    fact ids rather than duplicating them."""
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located")
    first_provider = CoLocatedMemoryProvider(vector=vector, graph=graph, config=config)
    first_provider.write(ACME, [event("the deploy failed on friday")])
    first_worker = ConsolidationWorker(first_provider, [ACME], retry_delay=0.001)
    await first_worker.run_once()

    second_provider = CoLocatedMemoryProvider(vector=vector, graph=graph, config=config)
    second_worker = ConsolidationWorker(second_provider, [ACME], retry_delay=0.001)
    await second_worker.run_once()

    semantic = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC]
    assert len(semantic) == 1
    assert len({fact.record.id for fact in graph.facts}) == 1


async def test_start_returns_before_the_first_tick_completes():
    """The hot-path contract: starting the worker must not block on a slow first pass."""
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])
    started = asyncio.Event()

    async def slow_aconsolidate(scope, tiers=None):
        started.set()
        await asyncio.sleep(0.2)
        return ConsolidationReport(scope=scope, details={"complete": True, "entities": "not extracted"})

    provider.aconsolidate = slow_aconsolidate
    worker = ConsolidationWorker(provider, [ACME], poll_interval=10, retry_delay=0.001)

    worker.start()
    try:
        assert not started.is_set(), "start() must return before the tick it scheduled has run"
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        await worker.stop()


async def test_stop_waits_for_the_in_flight_tick_then_returns():
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])
    tick_ran = asyncio.Event()
    real_aconsolidate = provider.aconsolidate

    async def observed(scope, tiers=None):
        report = await real_aconsolidate(scope, tiers)
        tick_ran.set()
        return report

    provider.aconsolidate = observed
    worker = ConsolidationWorker(provider, [ACME], poll_interval=10, retry_delay=0.001)

    worker.start()
    await asyncio.wait_for(tick_ran.wait(), timeout=1)  # observable side effect, not a time budget
    await worker.stop()

    assert worker._task is None


async def test_a_cancelled_loop_reports_itself_cancelled_not_a_clean_return():
    """`_loop`'s `CancelledError` handler must re-raise, not swallow -- a caller inspecting the
    task after cancellation should see the true outcome, matching ordinary asyncio convention.

    Cancelled while genuinely suspended inside ``run_once`` (not merely before the task has had a
    chance to start), so the cancellation actually passes through the handler under test rather
    than short-circuiting a task that never began.

    Mutation guard: replacing the `raise` in `_loop`'s `except asyncio.CancelledError` clause with
    `break` (SM-L1's original shape, before this round's fix) makes `task.cancelled()` come back
    `False` here instead of `True`.
    """
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])
    inside_run_once = asyncio.Event()

    async def slow_aconsolidate(scope, tiers=None):
        inside_run_once.set()
        await asyncio.sleep(10)
        raise AssertionError("should have been cancelled first")

    provider.aconsolidate = slow_aconsolidate
    worker = ConsolidationWorker(provider, [ACME], poll_interval=10, retry_delay=0.001)

    worker.start()
    task = worker._task
    await asyncio.wait_for(inside_run_once.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled() is True


async def test_scopes_callable_is_re_resolved_every_tick():
    """A caller whose tenant set grows can hand over a function instead of a fixed list."""
    provider, _, _ = make_provider()
    provider.write(ACME, [event("acme's episode")])
    provider.write(GLOBEX, [event("globex's episode", scope=GLOBEX)])
    seen_scopes = []

    def growing():
        scopes = [ACME] if not seen_scopes else [ACME, GLOBEX]
        seen_scopes.append(tuple(scopes))
        return scopes

    worker = ConsolidationWorker(provider, growing, retry_delay=0.001)

    first = await worker.run_once()
    second = await worker.run_once()

    assert len(first) == 1
    assert len(second) == 2


@pytest.mark.parametrize(
    "option,value",
    [("poll_interval", 0), ("poll_interval", -1), ("max_passes_per_scope", 0), ("max_retries", -1), ("retry_delay", 0)],
)
def test_construction_refuses_a_non_positive_bound(option, value):
    provider, _, _ = make_provider()
    with pytest.raises(ValueError, match=option):
        ConsolidationWorker(provider, [ACME], **{option: value})


def test_max_retries_zero_is_accepted_as_fail_fast():
    """0 is "no retry", a real setting -- not a bound violation like the negative case above."""
    provider, _, _ = make_provider()
    worker = ConsolidationWorker(provider, [ACME], max_retries=0)
    assert worker.max_retries == 0


async def test_max_retries_zero_raises_on_the_first_failure_with_no_retry():
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])
    calls = {"n": 0}

    async def always_fails(scope, tiers=None):
        calls["n"] += 1
        raise RuntimeError("down")

    provider.aconsolidate = always_fails
    worker = ConsolidationWorker(provider, [ACME], max_retries=0, retry_delay=0.001)

    outcome = (await worker.run_once())[0]

    assert calls["n"] == 1
    assert outcome.error is not None
