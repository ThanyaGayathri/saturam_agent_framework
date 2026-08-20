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
from egpt.memory.consolidation import ConsolidationOutcome, ConsolidationWorker, EventCountTrigger
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


class _SpyTrigger:
    """A `ConsolidationTrigger` that never fires and just records every `reset` call -- isolates
    the departed-scope diff in `run_once` from the ordinary post-drain reset, which a real trigger
    (e.g. `EventCountTrigger`) could not do on its own.
    """

    def __init__(self):
        self.reset_calls = []

    def record_event(self, scope, *, now, size=1.0):
        pass

    def should_fire(self, scope, *, now):
        return False

    def reset(self, scope):
        self.reset_calls.append(scope)


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


async def test_start_during_stop_does_not_orphan_a_second_task():
    """A `start()` landing while a `stop()` is still awaiting the outgoing task must not create a
    second one -- `stop()`'s very first line sets `_running = False` before its `await`, so a
    guard on `_running` alone (rather than `_task`) would pass here, hand out a second task, and
    have `self._task` overwritten out from under the `stop()` call joining the first -- orphaned,
    with no reference left to cancel or await, and un-stoppable by any later `stop()` (which would
    see `_task is None` and return immediately).

    Mutation guard: reverting `start()`'s guard to `if self._running: return` makes
    `tasks_created["n"]` come back 1 instead of 0 (a second loop task scheduled during the
    no-op `start()` below), and `worker._task` end up non-``None`` after `stop()` returns,
    holding a stale reference to the now-orphaned original task.
    """
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])
    inside_tick = asyncio.Event()
    release_tick = asyncio.Event()
    tasks_created = {"n": 0}
    real_aconsolidate = provider.aconsolidate
    original_create_task = asyncio.create_task

    def counting_create_task(coro, **kwargs):
        tasks_created["n"] += 1
        return original_create_task(coro, **kwargs)

    async def slow_aconsolidate(scope, tiers=None):
        inside_tick.set()
        await release_tick.wait()
        return await real_aconsolidate(scope, tiers)

    provider.aconsolidate = slow_aconsolidate
    worker = ConsolidationWorker(provider, [ACME], poll_interval=10, retry_delay=0.001)

    worker.start()
    await asyncio.wait_for(inside_tick.wait(), timeout=1)

    # `ensure_future`, not `create_task`, so the counter below tracks only `start()`'s own calls.
    stop_future = asyncio.ensure_future(worker.stop())
    await asyncio.sleep(0)  # let stop() run up to its `await asyncio.wait_for(task, ...)`

    asyncio.create_task = counting_create_task
    try:
        worker.start()  # must be a no-op: the loop is still mid-shutdown
    finally:
        asyncio.create_task = original_create_task
    assert tasks_created["n"] == 0, "start() during stop() must not schedule a second loop task"

    release_tick.set()
    await asyncio.wait_for(stop_future, timeout=1)

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


async def test_the_background_loop_survives_a_scope_source_that_raises():
    """`_drain_scope` catches a per-scope failure; a scope-*source* that raises during resolution
    (before any scope exists to attribute the failure to) is `run_once`'s own machinery breaking,
    caught one level up in `_loop`. The task must keep polling on the next tick rather than dying
    silently -- a background task nobody is awaiting fails invisibly otherwise.
    """
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])
    calls = {"n": 0}

    def flaky_scopes():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("scope directory unavailable")
        return [ACME]

    worker = ConsolidationWorker(provider, flaky_scopes, poll_interval=0.01, retry_delay=0.001)

    worker.start()
    task = worker._task
    try:
        await asyncio.wait_for(_until(lambda: calls["n"] >= 2), timeout=1)
    finally:
        await worker.stop()

    assert not task.cancelled()
    assert task.exception() is None, "the failed tick must not have crashed the loop task"


async def _until(predicate) -> None:
    while not predicate():
        await asyncio.sleep(0.005)


@pytest.mark.parametrize(
    "option,value",
    [("poll_interval", 0), ("poll_interval", -1), ("max_passes_per_scope", 0), ("max_retries", -1), ("retry_delay", 0)],
)
def test_construction_refuses_a_non_positive_bound(option, value):
    provider, _, _ = make_provider()
    with pytest.raises(ValueError, match=option):
        ConsolidationWorker(provider, [ACME], **{option: value})


async def test_run_once_skips_a_scope_the_trigger_has_not_fired_for():
    """SM-L2 (#123): with a trigger configured, a scope that has not crossed its threshold is left
    out of the tick entirely -- not drained with an empty report.

    Mutation guard: if the worker ignored `self.trigger` (SM-L1's original, always-drain
    behaviour), this would return an outcome for ACME instead of an empty list.
    """
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])
    trigger = EventCountTrigger(threshold=3)
    trigger.record_event(ACME, now=0.0)  # below the threshold of 3
    worker = ConsolidationWorker(provider, [ACME], trigger=trigger, clock=lambda: 0.0, retry_delay=0.001)

    outcomes = await worker.run_once()

    assert outcomes == []


async def test_run_once_drains_a_scope_the_trigger_has_fired_for():
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])
    trigger = EventCountTrigger(threshold=1)
    trigger.record_event(ACME, now=0.0)
    worker = ConsolidationWorker(provider, [ACME], trigger=trigger, clock=lambda: 0.0, retry_delay=0.001)

    outcomes = await worker.run_once()

    assert len(outcomes) == 1
    assert outcomes[0].scope == ACME
    assert outcomes[0].error is None


async def test_a_successful_drain_resets_the_trigger():
    """A scope that just drained must not still read as due next tick -- otherwise every scope
    fires forever once its threshold is crossed once.

    Mutation guard: skipping the `trigger.reset` call after a successful drain would make the
    second `run_once` also return ACME's outcome instead of an empty list.
    """
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])
    trigger = EventCountTrigger(threshold=1)
    trigger.record_event(ACME, now=0.0)
    worker = ConsolidationWorker(provider, [ACME], trigger=trigger, clock=lambda: 0.0, retry_delay=0.001)

    first = await worker.run_once()
    assert len(first) == 1

    second = await worker.run_once()

    assert second == []


async def test_a_failed_drain_does_not_reset_the_trigger():
    """The trigger's accumulated state (still-pending work) must survive a failed tick, so the
    next tick's `should_fire` still reflects it rather than losing the signal to a transient error.
    """
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])

    async def always_fails(scope, tiers=None):
        raise RuntimeError("backend unreachable")

    provider.aconsolidate = always_fails
    trigger = EventCountTrigger(threshold=1)
    trigger.record_event(ACME, now=0.0)
    worker = ConsolidationWorker(provider, [ACME], trigger=trigger, clock=lambda: 0.0, max_retries=0, retry_delay=0.001)

    first = await worker.run_once()
    assert len(first) == 1
    assert first[0].error is not None

    assert trigger.should_fire(ACME, now=0.0) is True


async def test_a_partial_drain_does_not_reset_the_trigger():
    """SM-L2 review: `error is None` alone is not "fully drained" -- `outcome.drained` can be
    `False` with no error when `max_passes_per_scope` caps the tick before the scope's tail
    clears. Resetting the trigger anyway would make `should_fire` stop reflecting the still-
    pending remainder until enough *new* events re-cross the threshold, silently dropping the
    unconsolidated backlog acquired before the cap was hit.

    Mutation guard: reverting the reset condition to `outcome.error is None` (dropping
    `and outcome.drained`) would make `trigger.should_fire` read `False` after this tick instead
    of `True`.
    """
    provider, _, _ = make_provider(max_episodes_per_pass=1)
    provider.write(ACME, [event(f"topic{i} unique content", id=f"e{i}") for i in range(3)])
    trigger = EventCountTrigger(threshold=1)
    trigger.record_event(ACME, now=0.0)
    worker = ConsolidationWorker(
        provider, [ACME], trigger=trigger, clock=lambda: 0.0, max_passes_per_scope=1, retry_delay=0.001
    )

    outcomes = await worker.run_once()

    assert outcomes[0].error is None
    assert outcomes[0].drained is False
    assert trigger.should_fire(ACME, now=0.0) is True


async def test_a_scope_that_drops_out_of_a_dynamic_source_has_its_trigger_reset():
    """SM-L2 review: a dynamic `ScopeSource` (a callable) that stops naming a scope must not leave
    that scope's trigger state alive for the rest of the worker's lifetime -- otherwise a
    deployment with tenant churn grows a trigger's per-scope dict without bound. `run_once` must
    call `reset` for a scope that disappears between ticks, on the tick it disappears.

    `should_fire` always returns `False` here so the only possible source of a `reset` call is the
    departed-scope diff, not the ordinary post-drain reset -- isolating exactly what this test
    means to pin.

    Mutation guard: dropping the departed-scope diff entirely would leave `spy.reset_calls` empty
    after the second tick instead of `[ACME]`.
    """
    provider, _, _ = make_provider()
    calls = {"n": 0}

    def scopes():
        calls["n"] += 1
        return [ACME] if calls["n"] == 1 else []

    spy = _SpyTrigger()
    worker = ConsolidationWorker(provider, scopes, trigger=spy, clock=lambda: 0.0, retry_delay=0.001)

    await worker.run_once()
    assert spy.reset_calls == []

    await worker.run_once()

    assert spy.reset_calls == [ACME]


async def test_an_async_trigger_raises_instead_of_silently_draining_everything():
    """SM-L2 review round 2: `ConsolidationTrigger` is a synchronous Protocol, but nothing at the
    type level stops a caller from passing one whose methods are `async def`. Called as a plain
    function (exactly how `run_once` calls it), Python evaluates the *coroutine object* itself,
    which is always truthy -- an async `should_fire` that means "not due" would otherwise drain
    every scope on every tick instead of raising anywhere. This must fail loudly instead.

    Reproduced first: confirmed via a standalone script that `bool(async_trigger.should_fire(...))`
    is `True` regardless of what the coroutine would have returned if awaited.

    Mutation guard: removing the `asyncio.iscoroutine` guard in `_sync_trigger_result` would make
    this pass with `outcomes` non-empty (every scope drained) instead of raising `TypeError`.
    """
    provider, _, _ = make_provider()
    provider.write(ACME, [event("the deploy failed on friday")])

    class AsyncTrigger:
        async def record_event(self, scope, *, now, size=1.0):
            pass

        async def should_fire(self, scope, *, now):
            return False  # would mean "not due" if this worker awaited it -- it does not

        async def reset(self, scope):
            pass

    worker = ConsolidationWorker(provider, [ACME], trigger=AsyncTrigger(), clock=lambda: 0.0, retry_delay=0.001)

    with pytest.raises(TypeError, match="coroutine"):
        await worker.run_once()


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
