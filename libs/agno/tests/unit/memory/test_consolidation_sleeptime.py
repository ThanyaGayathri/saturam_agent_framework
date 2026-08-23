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
from egpt.memory.consolidation.triggers import IdleTrigger
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

    def test_evaluates_in_the_configured_timezone(self):
        """03:00 UTC is 22:00 the prior day in US/Eastern (UTC-5) -- a window scoped to
        US/Eastern's 01:00-06:00 must not fire on a UTC hour that only looks like it matches.
        """
        window = SleepWindow(start_hour=1, end_hour=6, tz="US/Eastern")
        assert window.contains(_utc(3)) is False  # 22:00 US/Eastern the prior day
        assert window.contains(_utc(9)) is True  # 04:00 US/Eastern


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

    def test_rejects_an_unknown_mode(self):
        with pytest.raises(ValueError):
            SleepTimeScheduleConfig(window=SleepWindow(1, 6), idle_seconds=300, mode="quantum")

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
        from egpt.memory.consolidation.triggers import EventCountTrigger

        config = SleepTimeScheduleConfig(window=SleepWindow(1, 6), idle_seconds=10)
        inner = EventCountTrigger(threshold=2)
        trigger = build_sleep_time_trigger(config, inner=inner)

        trigger.record_event(ACME, now=_utc(2, 0))
        assert trigger.should_fire(ACME, now=_utc(2, 0)) is False
        trigger.record_event(ACME, now=_utc(2, 1))
        assert trigger.should_fire(ACME, now=_utc(2, 1)) is True
