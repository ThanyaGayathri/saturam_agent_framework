"""``ConsolidationTrigger`` policies (SM-L2 #123): pure decision logic over a fake clock, no
backend involved -- what is pinned here is threshold-crossing, per-tenant overrides, and the
idle-vs-latency distinction, not anything ``ConsolidationWorker`` integration already covers in
``test_consolidation_worker.py``.
"""

import pytest

from egpt.memory.consolidation.triggers import (
    AnyTrigger,
    ConsolidationTrigger,
    EventCountTrigger,
    IdleTrigger,
    LatencyTrigger,
    PerTenantThreshold,
    SizeTrigger,
)
from egpt.memory.types import Scope

ACME = Scope(tenant_id="acme", user_id="u1")
GLOBEX = Scope(tenant_id="globex", user_id="u1")


class TestEventCountTrigger:
    def test_does_not_fire_below_threshold(self):
        trigger = EventCountTrigger(threshold=3)
        trigger.record_event(ACME, now=0.0)
        trigger.record_event(ACME, now=1.0)
        assert trigger.should_fire(ACME, now=1.0) is False

    def test_fires_once_threshold_reached(self):
        """Mutation guard: a `>` instead of `>=` would leave this False at exactly 3 events."""
        trigger = EventCountTrigger(threshold=3)
        for i in range(3):
            trigger.record_event(ACME, now=float(i))
        assert trigger.should_fire(ACME, now=3.0) is True

    def test_size_kwarg_is_ignored(self):
        """Every `record_event` call counts as one event regardless of `size` -- that unit belongs
        to `SizeTrigger`, not this one.

        Mutation guard: summing `size` instead of counting occurrences would fire this at the
        first call (size=10 >= threshold=3) instead of needing three calls.
        """
        trigger = EventCountTrigger(threshold=3)
        trigger.record_event(ACME, now=0.0, size=10.0)
        assert trigger.should_fire(ACME, now=0.0) is False

    def test_reset_clears_accumulated_count(self):
        trigger = EventCountTrigger(threshold=2)
        trigger.record_event(ACME, now=0.0)
        trigger.record_event(ACME, now=1.0)
        assert trigger.should_fire(ACME, now=1.0) is True

        trigger.reset(ACME)

        assert trigger.should_fire(ACME, now=1.0) is False

    def test_scopes_are_independent(self):
        trigger = EventCountTrigger(threshold=1)
        trigger.record_event(ACME, now=0.0)
        assert trigger.should_fire(ACME, now=0.0) is True
        assert trigger.should_fire(GLOBEX, now=0.0) is False

    def test_reset_of_an_unseen_scope_is_a_no_op(self):
        trigger = EventCountTrigger(threshold=1)
        trigger.reset(ACME)  # must not raise
        assert trigger.should_fire(ACME, now=0.0) is False

    @pytest.mark.parametrize("threshold", [0, -1])
    def test_construction_refuses_a_non_positive_threshold(self, threshold):
        with pytest.raises(ValueError, match="threshold"):
            EventCountTrigger(threshold=threshold)

    def test_per_tenant_threshold_overrides_the_default(self):
        """Mutation guard: reading `.default` unconditionally instead of resolving by tenant would
        make acme's trigger require 10 events too, not fire at 2.
        """
        threshold = PerTenantThreshold(default=10, by_tenant={"acme": 2})
        trigger = EventCountTrigger(threshold=threshold)
        trigger.record_event(ACME, now=0.0)
        trigger.record_event(ACME, now=1.0)
        trigger.record_event(GLOBEX, now=0.0)
        trigger.record_event(GLOBEX, now=1.0)

        assert trigger.should_fire(ACME, now=1.0) is True
        assert trigger.should_fire(GLOBEX, now=1.0) is False

    def test_per_tenant_override_must_be_positive(self):
        with pytest.raises(ValueError, match="by_tenant"):
            EventCountTrigger(threshold=PerTenantThreshold(default=5, by_tenant={"acme": 0}))


class TestSizeTrigger:
    def test_fires_on_cumulative_size_not_call_count(self):
        """Mutation guard: counting calls instead of summing `size` would need three calls to
        reach a threshold of 30, not two.
        """
        trigger = SizeTrigger(threshold=30)
        trigger.record_event(ACME, now=0.0, size=12.0)
        assert trigger.should_fire(ACME, now=0.0) is False
        trigger.record_event(ACME, now=1.0, size=18.0)
        assert trigger.should_fire(ACME, now=1.0) is True

    def test_default_size_is_one(self):
        trigger = SizeTrigger(threshold=2)
        trigger.record_event(ACME, now=0.0)
        trigger.record_event(ACME, now=1.0)
        assert trigger.should_fire(ACME, now=1.0) is True

    def test_reset_clears_accumulated_size(self):
        """Mirrors `TestEventCountTrigger`'s equivalent test -- `SizeTrigger` was the one trigger
        class in the PR missing it.
        """
        trigger = SizeTrigger(threshold=10)
        trigger.record_event(ACME, now=0.0, size=10.0)
        assert trigger.should_fire(ACME, now=0.0) is True

        trigger.reset(ACME)

        assert trigger.should_fire(ACME, now=0.0) is False

    def test_reset_of_an_unseen_scope_is_a_no_op(self):
        """`reset` (inherited from `_AccumulatingTrigger`) pops with a default, but nothing had
        pinned that specifically for `SizeTrigger` -- a mutation to a bare (no-default) `.pop(key)`
        would have passed CI for every other trigger class except this one.
        """
        trigger = SizeTrigger(threshold=10)
        trigger.reset(ACME)  # must not raise
        assert trigger.should_fire(ACME, now=0.0) is False

    def test_per_tenant_threshold_overrides_the_default(self):
        """Mirrors `TestEventCountTrigger`'s equivalent test -- both classes share
        `_AccumulatingTrigger`, so a per-tenant resolution regression in the shared base could slip
        through CI if only one of the two subclasses exercised it.

        Mutation guard: reading `.default` unconditionally instead of resolving by tenant would
        make acme's trigger require size 100 too, not fire at size 20.
        """
        threshold = PerTenantThreshold(default=100, by_tenant={"acme": 20})
        trigger = SizeTrigger(threshold=threshold)
        trigger.record_event(ACME, now=0.0, size=20.0)
        trigger.record_event(GLOBEX, now=0.0, size=20.0)

        assert trigger.should_fire(ACME, now=0.0) is True
        assert trigger.should_fire(GLOBEX, now=0.0) is False


class TestLatencyTrigger:
    def test_fires_once_the_oldest_pending_event_ages_past_the_bound(self):
        trigger = LatencyTrigger(max_age_seconds=10)
        trigger.record_event(ACME, now=0.0)
        assert trigger.should_fire(ACME, now=9.9) is False
        assert trigger.should_fire(ACME, now=10.0) is True

    def test_a_later_event_does_not_push_the_oldest_timestamp_forward(self):
        """The trigger tracks the *oldest* un-reset event, not the latest -- a scope under
        constant write pressure must still breach its latency bound.

        Mutation guard: `record_event` overwriting instead of `setdefault`-ing the timestamp would
        keep resetting the clock on every new event and this would never fire.
        """
        trigger = LatencyTrigger(max_age_seconds=10)
        trigger.record_event(ACME, now=0.0)
        trigger.record_event(ACME, now=5.0)
        trigger.record_event(ACME, now=9.0)
        assert trigger.should_fire(ACME, now=10.0) is True

    def test_reset_starts_the_age_clock_over(self):
        trigger = LatencyTrigger(max_age_seconds=10)
        trigger.record_event(ACME, now=0.0)
        trigger.reset(ACME)
        trigger.record_event(ACME, now=8.0)
        assert trigger.should_fire(ACME, now=15.0) is False
        assert trigger.should_fire(ACME, now=18.0) is True

    def test_no_pending_events_never_fires(self):
        trigger = LatencyTrigger(max_age_seconds=10)
        assert trigger.should_fire(ACME, now=1000.0) is False

    def test_reset_of_an_unseen_scope_is_a_no_op(self):
        """Mirrors `TestEventCountTrigger`'s equivalent test. `reset` pops with a default, but
        `record_event` is not the only caller of the underlying dict -- pinning this per-class
        catches a mutation to a bare (no-default) `.pop(key)` slipping into any one of them.
        """
        trigger = LatencyTrigger(max_age_seconds=10)
        trigger.reset(ACME)  # must not raise
        assert trigger.should_fire(ACME, now=0.0) is False

    def test_per_tenant_threshold_overrides_the_default(self):
        """`_resolve` (the per-tenant lookup) is shared code, but nothing exercised it for a
        time-based trigger before this -- only the two `_AccumulatingTrigger` subclasses had this
        test. A regression that made every trigger read `.default` unconditionally would have
        passed CI for `LatencyTrigger` specifically.

        Mutation guard: reading `.default` unconditionally instead of resolving by tenant would
        make acme's trigger require 20s too, not fire at 5s.
        """
        threshold = PerTenantThreshold(default=20, by_tenant={"acme": 5})
        trigger = LatencyTrigger(max_age_seconds=threshold)
        trigger.record_event(ACME, now=0.0)
        trigger.record_event(GLOBEX, now=0.0)

        assert trigger.should_fire(ACME, now=5.0) is True
        assert trigger.should_fire(GLOBEX, now=5.0) is False


class TestIdleTrigger:
    def test_fires_once_idle_long_enough_since_the_last_event(self):
        trigger = IdleTrigger(idle_seconds=30)
        trigger.record_event(ACME, now=0.0)
        assert trigger.should_fire(ACME, now=29.9) is False
        assert trigger.should_fire(ACME, now=30.0) is True

    def test_a_fresh_event_pushes_the_idle_clock_forward(self):
        """Distinguishes idle (latest event) from latency (oldest event): a scope kept busy right
        up to the boundary must not read as idle.

        Mutation guard: `record_event` using `setdefault` instead of overwriting would make this
        fire at t=30 off the *first* event instead of resetting off the second.
        """
        trigger = IdleTrigger(idle_seconds=30)
        trigger.record_event(ACME, now=0.0)
        trigger.record_event(ACME, now=20.0)
        assert trigger.should_fire(ACME, now=30.0) is False
        assert trigger.should_fire(ACME, now=50.0) is True

    def test_a_scope_with_no_recorded_events_never_fires(self):
        """A scope nothing has ever written to must not be mistaken for one that has gone idle --
        otherwise every never-used scope would fire on the very first check.
        """
        trigger = IdleTrigger(idle_seconds=30)
        assert trigger.should_fire(ACME, now=10_000.0) is False

    def test_reset_requires_a_fresh_event_before_firing_again(self):
        trigger = IdleTrigger(idle_seconds=30)
        trigger.record_event(ACME, now=0.0)
        trigger.reset(ACME)
        assert trigger.should_fire(ACME, now=100.0) is False

    def test_reset_of_an_unseen_scope_is_a_no_op(self):
        trigger = IdleTrigger(idle_seconds=30)
        trigger.reset(ACME)  # must not raise
        assert trigger.should_fire(ACME, now=0.0) is False

    def test_per_tenant_threshold_overrides_the_default(self):
        """Mirrors `TestLatencyTrigger`'s equivalent test -- both time-based triggers share the
        same `_resolve` per-tenant lookup.

        Mutation guard: reading `.default` unconditionally instead of resolving by tenant would
        make acme's trigger require 60s of idle too, not fire at 10s.
        """
        threshold = PerTenantThreshold(default=60, by_tenant={"acme": 10})
        trigger = IdleTrigger(idle_seconds=threshold)
        trigger.record_event(ACME, now=0.0)
        trigger.record_event(GLOBEX, now=0.0)

        assert trigger.should_fire(ACME, now=10.0) is True
        assert trigger.should_fire(GLOBEX, now=10.0) is False


class TestPerTenantThreshold:
    def test_default_must_be_positive_standalone(self):
        """`PerTenantThreshold` validates itself at construction, independent of whether it is ever
        passed to a trigger -- a caller building a config object before wiring it up to anything
        must not be able to hold an invalid one.
        """
        with pytest.raises(ValueError, match="default"):
            PerTenantThreshold(default=-1.0)

    def test_by_tenant_override_must_be_positive_standalone(self):
        with pytest.raises(ValueError, match="by_tenant"):
            PerTenantThreshold(default=5.0, by_tenant={"acme": 0.0})

    def test_by_tenant_key_must_be_non_empty(self):
        """`Scope.__post_init__` rejects a blank `tenant_id`, so an empty-string override can never
        be reached by a valid scope lookup -- but that makes it a dead, silently-accepted entry
        rather than a reachable one, worth rejecting at construction all the same.
        """
        with pytest.raises(ValueError, match="non-empty"):
            PerTenantThreshold(default=5.0, by_tenant={"": 2.0})

    def test_by_tenant_key_error_names_the_actual_offending_key(self):
        """SM-L2 review round 3: the error message must name the *actual* key, not a hardcoded
        `''` -- a caller passing a `None` key (a plausible typo, e.g. building `by_tenant` from a
        dict comprehension gone wrong) would otherwise see a misleading `"got ''."` instead of the
        `None` that was actually rejected.

        Mutation guard: hardcoding `"got ''."` instead of `{tenant_id!r}` would make this see the
        message for the empty-string case (`""`) even though the offending key here is `None`.
        """
        with pytest.raises(ValueError, match=r"got None\."):
            PerTenantThreshold(default=5.0, by_tenant={None: 2.0})

    def test_mutating_the_original_mapping_after_construction_does_not_affect_resolve(self):
        """`frozen=True` only stops rebinding `by_tenant` itself, not mutating the dict a caller
        passed in and kept a handle to -- `__post_init__` must defensively copy it, or a threshold
        already validated as positive could be flipped to 0.0 after the fact with nothing to catch
        it a second time.

        Mutation guard: `object.__setattr__(self, "by_tenant", self.by_tenant)` (no `dict(...)`
        copy) would make `resolve("acme")` return the mutated 0.0 instead of the original 2.0.
        """
        overrides = {"acme": 2.0}
        threshold = PerTenantThreshold(default=10.0, by_tenant=overrides)

        overrides["acme"] = 0.0

        assert threshold.resolve("acme") == 2.0


class TestAnyTrigger:
    def test_fires_when_any_child_fires(self):
        counts = EventCountTrigger(threshold=100)
        idle = IdleTrigger(idle_seconds=10)
        combined = AnyTrigger([counts, idle])

        combined.record_event(ACME, now=0.0)
        assert combined.should_fire(ACME, now=5.0) is False
        assert combined.should_fire(ACME, now=10.0) is True, "idle alone should have fired"

    def test_record_event_fans_out_to_every_child(self):
        """Mutation guard: forwarding to only the first child would leave the second (`counts`)
        never incremented, so it would never fire regardless of how many events are recorded.
        """
        counts = EventCountTrigger(threshold=2)
        idle = IdleTrigger(idle_seconds=1000)
        combined = AnyTrigger([counts, idle])

        combined.record_event(ACME, now=0.0)
        combined.record_event(ACME, now=1.0)

        assert combined.should_fire(ACME, now=1.0) is True

    def test_reset_clears_every_child(self):
        counts = EventCountTrigger(threshold=1)
        idle = IdleTrigger(idle_seconds=1000)
        combined = AnyTrigger([counts, idle])
        combined.record_event(ACME, now=0.0)
        assert combined.should_fire(ACME, now=0.0) is True

        combined.reset(ACME)

        assert combined.should_fire(ACME, now=0.0) is False
        assert combined.should_fire(ACME, now=999.0) is False

    def test_construction_refuses_an_empty_trigger_list(self):
        with pytest.raises(ValueError, match="at least one"):
            AnyTrigger([])

    def test_record_event_reaches_every_child_even_when_one_raises(self):
        """`ConsolidationTrigger` is a `Protocol` -- a caller-supplied implementation (DB-backed,
        network-backed) can raise from `record_event`. A raise from the first child must not
        starve the second of the event it depends on to ever fire.

        Mutation guard: dropping the `try`/`except` around each child call would propagate the
        `RuntimeError` out of `record_event` instead of letting `counts` still receive the event.
        """
        raising = _RaisingTrigger()
        counts = EventCountTrigger(threshold=1)
        combined = AnyTrigger([raising, counts])

        combined.record_event(ACME, now=0.0)  # must not raise

        assert combined.should_fire(ACME, now=0.0) is True, "the second child must still have fired"

    def test_reset_reaches_every_child_even_when_one_raises(self):
        """Mutation guard: dropping the `try`/`except` around each child call would leave `counts`
        still armed after `reset`, so `should_fire` would incorrectly read `True` on the next tick
        for a scope that was, in fact, already drained.
        """
        raising = _RaisingTrigger()
        counts = EventCountTrigger(threshold=1)
        combined = AnyTrigger([raising, counts])
        combined.record_event(ACME, now=0.0)
        assert combined.should_fire(ACME, now=0.0) is True

        combined.reset(ACME)  # must not raise

        assert combined.should_fire(ACME, now=0.0) is False, "counts must have been reset"

    def test_should_fire_reaches_every_child_even_when_one_raises(self):
        """Named separately from the `record_event`/`reset` resilience tests above -- the
        previous round's `test_fires_when_any_child_fires` incidentally exercised this same
        `try`/`except` via `_RaisingTrigger.should_fire`, but nothing said so, so a future reader
        (or a future removal of that incidental coverage) would not notice `should_fire`'s own
        resilience had gone untested.

        Mutation guard: dropping the `try`/`except` around each child's `should_fire` call would
        propagate `RuntimeError` here instead of `counts` still being consulted and firing `True`.
        """
        raising = _RaisingTrigger()
        counts = EventCountTrigger(threshold=1)
        combined = AnyTrigger([raising, counts])
        combined.record_event(ACME, now=0.0)

        assert combined.should_fire(ACME, now=0.0) is True  # must not raise

    def test_construction_refuses_a_child_that_is_not_a_consolidation_trigger(self):
        """`ConsolidationTrigger` is `@runtime_checkable` specifically so this guard can exist --
        without it, a non-conforming child is accepted silently and only fails later, inside the
        `try`/`except` in `record_event`/`should_fire`/`reset`, where it looks like just another
        trigger that happened to fail rather than a construction-time mistake.
        """
        with pytest.raises(TypeError, match="not a ConsolidationTrigger"):
            AnyTrigger([EventCountTrigger(threshold=1), object()])

    def test_record_event_raises_on_an_async_child_instead_of_discarding_it(self):
        """SM-L2 review round 3: an async child satisfies `isinstance(_, ConsolidationTrigger)`
        structurally (the construction-time guard above cannot distinguish sync from async), so it
        passes construction -- the failure mode is `record_event`'s coroutine being silently
        discarded un-awaited, leaving the child's state never actually updated.

        Reproduced first: a standalone script confirmed `child.events` stayed `[]` after
        `record_event` with no guard in place.

        Mutation guard: removing the `_reject_coroutine` call after the try/except would make this
        pass with no exception raised, `AsyncChild.events` still empty, instead of raising.
        """
        combined = AnyTrigger([_AsyncTrigger()])

        with pytest.raises(TypeError, match="coroutine"):
            combined.record_event(ACME, now=0.0)

    def test_reset_raises_on_an_async_child_instead_of_discarding_it(self):
        combined = AnyTrigger([_AsyncTrigger()])

        with pytest.raises(TypeError, match="coroutine"):
            combined.reset(ACME)

    def test_a_raising_sync_child_is_still_resilience_handled_alongside_an_async_one(self):
        """The coroutine guard sits outside the try/except specifically so it does not disturb the
        existing resilience contract -- a child that raises synchronously (not an async-shape
        mismatch) must still be logged-and-skipped, not turned into a hard failure.
        """
        combined = AnyTrigger([_RaisingTrigger(), EventCountTrigger(threshold=1)])

        combined.record_event(ACME, now=0.0)  # must not raise -- _RaisingTrigger just logs

        assert combined.should_fire(ACME, now=0.0) is True, "the second child must still have fired"


class _AsyncTrigger:
    """A `ConsolidationTrigger` whose every method is `async def` -- structurally conforms to the
    Protocol (so it passes `AnyTrigger`'s construction-time `isinstance` guard) but is the wrong
    shape at call time, per `ConsolidationTrigger`'s own docstring.
    """

    def __init__(self):
        self.events = []
        self.was_reset = False

    async def record_event(self, scope: Scope, *, now: float, size: float = 1.0) -> None:
        self.events.append(now)

    async def should_fire(self, scope: Scope, *, now: float) -> bool:
        return False

    async def reset(self, scope: Scope) -> None:
        self.was_reset = True


class _RaisingTrigger:
    """A `ConsolidationTrigger` whose every method raises -- stands in for a caller-supplied
    implementation (DB-backed, network-backed) failing mid-call.
    """

    def record_event(self, scope: Scope, *, now: float, size: float = 1.0) -> None:
        raise RuntimeError("record_event boom")

    def should_fire(self, scope: Scope, *, now: float) -> bool:
        raise RuntimeError("should_fire boom")

    def reset(self, scope: Scope) -> None:
        raise RuntimeError("reset boom")


class TestConsolidationTriggerRuntimeCheckable:
    def test_a_conforming_trigger_satisfies_isinstance(self):
        assert isinstance(EventCountTrigger(threshold=1), ConsolidationTrigger)

    def test_isinstance_does_not_raise_typeerror(self):
        """Before `@runtime_checkable`, `isinstance(obj, ConsolidationTrigger)` raised `TypeError`
        regardless of `obj` -- this pins that a caller can now guard-check any object, conforming
        or not, instead of being forced to catch `TypeError` around the check itself.
        """
        assert isinstance(object(), ConsolidationTrigger) is False
