"""``sleeptime`` (SM-L9 #130): pure window/gating logic over a fake clock, no backend involved --
what is pinned here is the wall-clock window (including midnight wraparound), that
``SleepTimeTrigger`` only ever narrows an inner trigger rather than replacing its accounting, and
``SleepTimeScheduleConfig``'s validation/coercion.
"""

from datetime import datetime, timezone

import pytest

from egpt.memory.consolidation.sleeptime import (
    SleepTimeScheduleConfig,
    SleepTimeTrigger,
    SleepWindow,
    build_sleep_time_trigger,
)
from egpt.memory.consolidation.triggers import ConsolidationTrigger, EventCountTrigger, IdleTrigger
from egpt.memory.errors import MemoryProviderConfigError
from egpt.memory.types import Scope
from egpt.providers.model_provider import ProviderMode

ACME = Scope(tenant_id="acme", user_id="u1")


def _utc(hour: int, minute: int = 0) -> float:
    return datetime(2026, 1, 1, hour, minute, tzinfo=timezone.utc).timestamp()


class TestSleepWindow:
    @pytest.mark.parametrize("start_hour,end_hour", [(-1, 5), (24, 5), (5, 24), (5, -1)])
    def test_construction_refuses_an_out_of_range_hour(self, start_hour, end_hour):
        with pytest.raises(ValueError, match="hour"):
            SleepWindow(start_hour=start_hour, end_hour=end_hour)

    def test_construction_refuses_a_zero_width_window(self):
        with pytest.raises(ValueError, match="zero-width"):
            SleepWindow(start_hour=3, end_hour=3)

    def test_construction_refuses_an_unknown_timezone(self):
        with pytest.raises(ValueError, match="timezone"):
            SleepWindow(start_hour=1, end_hour=5, tz="Not/A_Zone")

    @pytest.mark.parametrize("tz", [None, 5])
    def test_construction_refuses_a_non_string_timezone(self, tz):
        """A `None` or integer `tz` (e.g. from JSON deserialization) must raise the same
        `ValueError` every other invalid input here does, not a bare `TypeError` from `ZoneInfo`
        itself escaping unwrapped.
        """
        with pytest.raises(ValueError, match="tz"):
            SleepWindow(start_hour=1, end_hour=5, tz=tz)

    @pytest.mark.parametrize("start_hour,end_hour", [(True, 5), (5, False)])
    def test_construction_refuses_a_bool_hour(self, start_hour, end_hour):
        """`bool` is an `int` subclass in Python -- without an explicit `isinstance(value, bool)`
        guard, `True`/`False` would pass the plain `isinstance(value, int)` check silently.
        """
        with pytest.raises(ValueError, match="hour"):
            SleepWindow(start_hour=start_hour, end_hour=end_hour)

    def test_contains_true_inside_a_same_day_window(self):
        window = SleepWindow(start_hour=1, end_hour=6)
        assert window.contains(_utc(3)) is True

    def test_contains_false_outside_a_same_day_window(self):
        window = SleepWindow(start_hour=1, end_hour=6)
        assert window.contains(_utc(12)) is False

    def test_start_hour_is_inclusive(self):
        """Mutation guard: `>` instead of `>=` on the lower bound would exclude the window's own
        start hour.
        """
        window = SleepWindow(start_hour=1, end_hour=6)
        assert window.contains(_utc(1, 0)) is True

    def test_end_hour_is_exclusive(self):
        """Mutation guard: `<=` instead of `<` on the upper bound would let the window's own end
        hour fire, one hour past what was configured.
        """
        window = SleepWindow(start_hour=1, end_hour=6)
        assert window.contains(_utc(6, 0)) is False

    def test_wraps_past_midnight(self):
        """22:00-04:00 covers late night through early morning, not the (empty, same-day) range
        `22 <= hour < 4` would describe if wraparound were not handled.
        """
        window = SleepWindow(start_hour=22, end_hour=4)
        assert window.contains(_utc(23)) is True
        assert window.contains(_utc(2)) is True
        assert window.contains(_utc(12)) is False

    def test_wrapped_start_hour_is_inclusive(self):
        """Mirrors `test_start_hour_is_inclusive` for the wrapped branch of `contains`."""
        window = SleepWindow(start_hour=22, end_hour=4)
        assert window.contains(_utc(22, 0)) is True

    def test_wrapped_end_hour_is_exclusive(self):
        """Mirrors `test_end_hour_is_exclusive` for the wrapped branch of `contains`."""
        window = SleepWindow(start_hour=22, end_hour=4)
        assert window.contains(_utc(4, 0)) is False

    def test_end_hour_zero_on_a_wrapped_window_excludes_midnight(self):
        """`SleepWindow(22, 0)` is a normal two-hour window (22:00-23:59): hour 0 is this
        window's own upper bound, excluded by the same exclusive-end rule `test_end_hour_is_exclusive`
        already pins for the non-wrapped case -- there is nothing wrap-specific about it, and it is
        not a way to spell "unbounded end". A caller wanting hour 0 included writes `end_hour=1`.
        """
        window = SleepWindow(start_hour=22, end_hour=0)
        assert window.contains(_utc(22)) is True
        assert window.contains(_utc(23)) is True
        assert window.contains(_utc(0)) is False

    def test_evaluates_in_the_configured_timezone(self):
        """03:00 UTC is 22:00 the prior day in US/Eastern (UTC-5) -- a window scoped to
        US/Eastern's 01:00-06:00 must not fire on a UTC hour that only looks like it matches.
        """
        window = SleepWindow(start_hour=1, end_hour=6, tz="US/Eastern")
        assert window.contains(_utc(3)) is False  # 22:00 US/Eastern the prior day
        assert window.contains(_utc(9)) is True  # 04:00 US/Eastern

    def test_correct_across_spring_forward(self):
        """2026-03-08 07:00 UTC is 03:00 EDT in `America/New_York` -- clocks jumped from 02:00 to
        03:00, so the skipped hour never occurs, but the UTC->local conversion is unambiguous and
        must land exactly on 03:00, not 02:00.
        """
        window = SleepWindow(start_hour=1, end_hour=6, tz="America/New_York")
        at = datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc).timestamp()
        assert window.contains(at) is True

    def test_correct_across_fall_back(self):
        """2025-11-02 06:00 UTC is 01:00 EST in `America/New_York`, the second (repeated) time
        01:00 occurs that day -- still an unambiguous conversion from a UTC epoch second.
        """
        window = SleepWindow(start_hour=1, end_hour=6, tz="America/New_York")
        at = datetime(2025, 11, 2, 6, 0, tzinfo=timezone.utc).timestamp()
        assert window.contains(at) is True


class TestSleepTimeTrigger:
    def test_does_not_fire_outside_the_window_even_if_inner_would(self):
        inner = IdleTrigger(idle_seconds=10)
        trigger = SleepTimeTrigger(inner, SleepWindow(start_hour=1, end_hour=6))
        trigger.record_event(ACME, now=_utc(12, 0))
        assert inner.should_fire(ACME, now=_utc(12, 10)) is True
        assert trigger.should_fire(ACME, now=_utc(12, 10)) is False

    def test_fires_inside_the_window_once_inner_would(self):
        inner = IdleTrigger(idle_seconds=10)
        trigger = SleepTimeTrigger(inner, SleepWindow(start_hour=1, end_hour=6))
        start = _utc(2, 0)
        trigger.record_event(ACME, now=start)
        assert trigger.should_fire(ACME, now=start + 5) is False
        assert trigger.should_fire(ACME, now=start + 10) is True

    def test_record_event_delegates_to_inner_regardless_of_window(self):
        """Pending work must still accumulate outside the window -- only firing is gated, not
        accounting. Verified by checking `inner`'s own state directly, not just through the
        wrapper.
        """
        inner = IdleTrigger(idle_seconds=10)
        trigger = SleepTimeTrigger(inner, SleepWindow(start_hour=1, end_hour=6))
        trigger.record_event(ACME, now=_utc(12, 0))
        assert inner.should_fire(ACME, now=_utc(12, 10)) is True

    def test_reset_delegates_to_inner(self):
        inner = IdleTrigger(idle_seconds=10)
        trigger = SleepTimeTrigger(inner, SleepWindow(start_hour=1, end_hour=6))
        trigger.record_event(ACME, now=_utc(2, 0))
        trigger.reset(ACME)
        assert inner.should_fire(ACME, now=_utc(2, 0) + 10) is False

    def test_fires_through_a_midnight_wrapping_window(self):
        """`TestSleepWindow.test_wraps_past_midnight` pins `SleepWindow.contains` in isolation;
        this pins the same wraparound through the full `record_event` -> `should_fire` path at
        the trigger level, so a future refactor that breaks the wrap only at that layer (e.g. a
        trigger-level rewrite that stops delegating to `window.contains` correctly) is caught here
        too, not only in the window's own tests.
        """
        inner = IdleTrigger(idle_seconds=10)
        trigger = SleepTimeTrigger(inner, SleepWindow(start_hour=22, end_hour=4))
        start = _utc(23, 0)
        trigger.record_event(ACME, now=start)
        assert trigger.should_fire(ACME, now=start + 10) is True

    def test_is_a_consolidation_trigger(self):
        """`AnyTrigger` gates at construction on `isinstance(trigger, ConsolidationTrigger)` and
        raises `TypeError` if it returns `False` -- pinning this here turns a future structural
        break into an immediate, obvious failure instead of a confusing `TypeError` surfacing
        inside `AnyTrigger`.
        """
        trigger = SleepTimeTrigger(IdleTrigger(idle_seconds=10), SleepWindow(start_hour=1, end_hour=6))
        assert isinstance(trigger, ConsolidationTrigger)

    def test_should_fire_does_not_consult_inner_outside_the_window(self):
        """The window is checked *first*, and short-circuits: `inner.should_fire` must not even
        be called outside the window, not merely have its result discarded. `triggers.py`'s own
        `ConsolidationTrigger` docstring allows a caller-supplied implementation to be "DB-backed"
        or "network-backed" -- calling it needlessly on every tick outside the window would be a
        real, avoidable cost for such an implementation, not just a style preference.

        A plain boolean-return assertion cannot distinguish call-then-AND from AND-short-circuit,
        since both produce the same `False` result here -- this needs a trigger that observes
        whether it was called at all.
        """

        class ExplodingInner:
            def record_event(self, scope, *, now, size=1.0):
                pass

            def should_fire(self, scope, *, now):
                raise AssertionError("inner.should_fire must not be called outside the window")

            def reset(self, scope):
                pass

        trigger = SleepTimeTrigger(ExplodingInner(), SleepWindow(start_hour=1, end_hour=6))
        assert trigger.should_fire(ACME, now=_utc(12, 0)) is False  # outside the window

    def test_an_async_inner_trigger_raises_instead_of_being_silently_discarded(self):
        """`inner` is a caller-supplied `ConsolidationTrigger`, and nothing at the type level
        stops it from being `async def` -- mirrors
        `test_consolidation_worker.test_an_async_trigger_raises_instead_of_silently_draining_everything`,
        pinned here at the `SleepTimeTrigger` layer specifically, since it wraps `inner` with its
        own delegation rather than being `inner` itself.

        Mutation guard: removing `_reject_coroutine` from `should_fire` would make this pass with
        a `bool(<coroutine>)` (always `True`) instead of raising `TypeError` -- inside the window,
        exactly the scenario `AnyTrigger`'s own guard exists to prevent.
        """

        class AsyncInner:
            async def record_event(self, scope, *, now, size=1.0):
                pass

            async def should_fire(self, scope, *, now):
                return False  # would mean "not due" if this were awaited -- it is not

            async def reset(self, scope):
                pass

        trigger = SleepTimeTrigger(AsyncInner(), SleepWindow(start_hour=1, end_hour=6))

        with pytest.raises(TypeError, match="coroutine"):
            trigger.should_fire(ACME, now=_utc(2, 0))  # inside the window, so `inner` is actually consulted


class TestSleepTimeScheduleConfig:
    def test_defaults_to_mode_a(self):
        config = SleepTimeScheduleConfig(window=SleepWindow(1, 6), idle_seconds=300)
        assert config.mode is ProviderMode.LOCAL

    def test_coerces_a_string_mode(self):
        """Mirrors `MemoryProviderConfig.mode`'s own coercion: a value loaded from JSON or a
        settings file is equal-but-not-identical to the enum member, and every consumer compares
        with `is`.
        """
        config = SleepTimeScheduleConfig(window=SleepWindow(1, 6), idle_seconds=300, mode="frontier")
        assert config.mode is ProviderMode.FRONTIER

    def test_coerces_a_whitespace_padded_mode(self):
        config = SleepTimeScheduleConfig(window=SleepWindow(1, 6), idle_seconds=300, mode="  frontier  ")
        assert config.mode is ProviderMode.FRONTIER

    def test_rejects_an_unknown_mode(self):
        """Matches `MemoryProviderConfig`'s error shape: a domain-specific error naming the field
        and listing the valid choices, not a bare `ValueError` with neither.
        """
        with pytest.raises(MemoryProviderConfigError, match="quantum") as exc_info:
            SleepTimeScheduleConfig(window=SleepWindow(1, 6), idle_seconds=300, mode="quantum")
        assert exc_info.value.field_name == "mode"
        assert "local" in str(exc_info.value)
        assert "frontier" in str(exc_info.value)

    @pytest.mark.parametrize("idle_seconds", [0, -5])
    def test_rejects_a_non_positive_idle_seconds(self, idle_seconds):
        with pytest.raises(ValueError, match="idle_seconds"):
            SleepTimeScheduleConfig(window=SleepWindow(1, 6), idle_seconds=idle_seconds)


class TestBuildSleepTimeTrigger:
    def test_defaults_to_an_idle_trigger_sized_from_config(self):
        config = SleepTimeScheduleConfig(window=SleepWindow(1, 6), idle_seconds=10)
        trigger = build_sleep_time_trigger(config)
        trigger.record_event(ACME, now=_utc(2, 0))
        assert trigger.should_fire(ACME, now=_utc(2, 0) + 5) is False
        assert trigger.should_fire(ACME, now=_utc(2, 0) + 10) is True

    def test_uses_a_caller_supplied_inner_trigger_instead(self):
        config = SleepTimeScheduleConfig(window=SleepWindow(1, 6), idle_seconds=10)
        inner = EventCountTrigger(threshold=2)
        trigger = build_sleep_time_trigger(config, inner=inner)

        trigger.record_event(ACME, now=_utc(2, 0))
        assert trigger.should_fire(ACME, now=_utc(2, 0)) is False
        trigger.record_event(ACME, now=_utc(2, 1))
        assert trigger.should_fire(ACME, now=_utc(2, 1)) is True
