"""``ConsolidationTrigger`` policies (SM-L2 #123): pure decision logic over a fake clock, no
backend involved -- what is pinned here is threshold-crossing, per-tenant overrides, and the
idle-vs-latency distinction, not anything ``ConsolidationWorker`` integration already covers in
``test_consolidation_worker.py``.
"""

import pytest

from egpt.memory.consolidation.triggers import (
    AnyTrigger,
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
