"""Sleep-time compute scheduling (SM-L9 #130): gating heavy consolidation to a configured
idle/off-peak window, so it runs while compute is cheap -- on owned GPUs in Mode A -- rather
than competing with the hot path at arbitrary times.

**What is reused, and what is not.** ``ConsolidationWorker`` (SM-L1 #122) already has the
poll/sleep/cancel-and-await loop this ticket's own affected-files table calls out, and
:class:`~egpt.memory.consolidation.triggers.IdleTrigger` (SM-L2 #123) already detects "no new
events for a while" -- SM-L2's own docstring built it as *"the idle/sleep-time trigger SM-L9
schedules its idle-window detection against."* Neither is reinvented here. What SM-L2 did not
build is a wall-clock window: ``IdleTrigger`` fires the moment a scope has been quiet long enough,
at any hour, which is "idle" but not "sleep-time" -- a tenant that goes quiet at 9am is idle by
that definition alone. :class:`SleepTimeTrigger` below adds the missing piece: an outer
:class:`ConsolidationTrigger` that only lets an inner one (typically an ``IdleTrigger``) fire
inside a configured daily window.

``egpt.scheduler`` (``ScheduleExecutor``/``SchedulePoller``) is deliberately **not** wired here,
matching the call ``worker.py``'s own module docstring already made: that package's unit of work
is a DB-backed ``Schedule`` row driving an HTTP endpoint, not a Python coroutine, and repurposing
it for one would mean giving every consolidation scope its own persisted ``Schedule`` row for no
behaviour a plain trigger does not already provide. What is reused instead is the same idle/window
concept a cron-style scheduler encodes: a caller passes ``SleepTimeTrigger`` as
``ConsolidationWorker(..., trigger=...)`` and gets exactly the "run heavy consolidation on a
schedule" behaviour this ticket asks for, driven by the worker's own existing loop.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from egpt.memory.consolidation._validators import _reject_coroutine
from egpt.memory.consolidation.triggers import ConsolidationTrigger, IdleTrigger, ThresholdSource, validated_threshold
from egpt.memory.errors import MemoryProviderConfigError
from egpt.memory.types import Scope
from egpt.providers.model_provider import ProviderMode

__all__ = ["SleepTimeScheduleConfig", "SleepTimeTrigger", "SleepWindow", "build_sleep_time_trigger"]


@dataclass(frozen=True)
class SleepWindow:
    """A daily wall-clock window (e.g. 01:00-06:00), evaluated in ``tz``.

    Half-open on the hour (``[start_hour, end_hour)``) and wraps past midnight when
    ``start_hour > end_hour`` (e.g. ``22`` -> ``4`` covers 22:00 through 03:59). A window where
    ``start_hour == end_hour`` is rejected rather than treated as "always on" or "never on" --
    either reading would be a silent surprise for a caller who almost certainly meant one specific
    hour and mistyped the other bound.

    **``end_hour=0`` on a wrapped window excludes hour 0 (midnight), by the same exclusive-end
    rule as every other window.** ``SleepWindow(22, 0)`` is a normal two-hour window covering
    22:00 through 23:59: hour 0 is this window's own upper bound, and an upper bound is excluded
    here exactly as it is for ``SleepWindow(1, 6)`` excluding hour 6 -- there is nothing
    wrap-specific about it. A caller who wants hour 0 included writes ``end_hour=1`` instead, the
    same way they would write ``end_hour=7`` to include hour 6 in the non-wrapped case.
    """

    start_hour: int
    end_hour: int
    tz: str = "UTC"
    #: Resolved from `tz` in `__post_init__`, once `tz` is known valid. Not an init/repr/eq field
    #: -- it is derived entirely from `tz`, so it would either duplicate `tz` in every comparison
    #: and printed form, or (worse) let a caller pass a mismatched one through the constructor.
    _tz_obj: Optional[ZoneInfo] = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for option, value in (("start_hour", self.start_hour), ("end_hour", self.end_hour)):
            if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 23):
                raise ValueError(f"{option} must be an integer in [0, 23], got {value!r}.")
        if self.start_hour == self.end_hour:
            raise ValueError(
                "start_hour and end_hour must differ -- a zero-width window can never distinguish "
                "'always on' from 'never on', so it is rejected rather than guessed at."
            )
        if not isinstance(self.tz, str):
            raise ValueError(f"tz must be a string IANA timezone key, got {self.tz!r}.")
        try:
            tz_obj = ZoneInfo(self.tz)
        except ZoneInfoNotFoundError:
            raise ValueError(
                f"Unknown timezone {self.tz!r}. If this is a valid IANA timezone key, the "
                "runtime's IANA timezone database is missing -- some minimal base images "
                "(e.g. Alpine Linux, common for Mode A GPU deployments) ship without one. "
                "Install it with `pip install tzdata`."
            ) from None
        # Cached rather than reconstructed on every `contains()` call. `ZoneInfo` already caches
        # internally, so this is a micro-optimisation, not a correctness fix -- but the object is
        # already in hand here from the validation above, so there is nothing to gain by throwing
        # it away and re-resolving `self.tz` on every call. `object.__setattr__` because the
        # dataclass is frozen; not a declared field, so it plays no part in equality or repr.
        object.__setattr__(self, "_tz_obj", tz_obj)

    def contains(self, now: float) -> bool:
        """Whether the wall-clock hour at epoch second ``now``, in ``tz``, falls in this window."""
        assert self._tz_obj is not None  # set by `__post_init__`, which every construction path runs
        hour = datetime.fromtimestamp(now, tz=self._tz_obj).hour
        if self.start_hour < self.end_hour:
            return self.start_hour <= hour < self.end_hour
        return hour >= self.start_hour or hour < self.end_hour


class SleepTimeTrigger(ConsolidationTrigger):
    """A :class:`~egpt.memory.consolidation.triggers.ConsolidationTrigger` that only lets
    ``inner`` fire inside ``window``.

    Declares ``ConsolidationTrigger`` as a base rather than relying only on the Protocol's
    structural match: ``ConsolidationTrigger`` is ``@runtime_checkable`` so this adds no ABC
    overhead, and it means a future rename of any Protocol method fails this class at
    type-check time instead of only at the ``AnyTrigger`` construction-time ``isinstance``
    check, or later still, at the first call through ``_reject_coroutine``.

    ``record_event``/``reset`` pass straight through to ``inner`` unconditionally: pending work
    still accumulates outside the window (an event at 9am must still count toward "idle since
    9am"), only *firing* is gated. Structurally the same wrap-and-delegate shape
    ``AnyTrigger`` already uses for combining triggers, narrowed to one inner trigger and one
    extra condition rather than several triggers combined by OR -- including the same
    ``_reject_coroutine`` guard on every delegated call, for the same reason: ``inner`` is a
    caller-supplied ``ConsolidationTrigger``, and nothing at the type level stops it from being an
    ``async def`` implementation that would otherwise have its coroutine silently discarded
    (``record_event``/``reset``) or misread as an always-truthy "fired" (``should_fire``).
    """

    def __init__(self, inner: ConsolidationTrigger, window: SleepWindow) -> None:
        self._inner = inner
        self._window = window

    def record_event(self, scope: Scope, *, now: float, size: float = 1.0) -> None:
        result = self._inner.record_event(scope, now=now, size=size)  # type: ignore[func-returns-value]
        _reject_coroutine(result, self._inner, "record_event")

    def should_fire(self, scope: Scope, *, now: float) -> bool:
        # The window is checked first, and short-circuits: `_inner.should_fire` must not run
        # outside the window at all, not merely have its result discarded, since a stateful inner
        # trigger (any accumulating one is not, but a caller-supplied one might be) could treat
        # being asked as an observation in its own right.
        if not self._window.contains(now):
            return False
        return bool(_reject_coroutine(self._inner.should_fire(scope, now=now), self._inner, "should_fire"))

    def reset(self, scope: Scope) -> None:
        _reject_coroutine(self._inner.reset(scope), self._inner, "reset")  # type: ignore[func-returns-value]


@dataclass(frozen=True)
class SleepTimeScheduleConfig:
    """Everything needed to wire a :class:`ConsolidationWorker`'s heavy consolidation passes to
    sleep-time, with no call site touched to change any of it -- the same "one config object"
    shape :class:`~egpt.memory.config.MemoryProviderConfig` already uses.

    ``mode`` records which compute this schedule is costed against, and does not change what
    runs: Mode A (``LOCAL``) is the deployment where sleep-time scheduling buys something real
    (idle owned GPUs are free capacity a running-anyway fleet would otherwise waste); Mode B
    (``FRONTIER``) pays the same per-call cost at any hour, so the window is honoured identically
    but the cost argument for using one does not apply. Gating *whether* consolidation runs on
    ``mode`` would be unvalidated -- nothing here measures a Mode B cost difference -- so ``mode``
    is carried through for configuration and observability, not branched on.
    """

    window: SleepWindow
    idle_seconds: ThresholdSource
    mode: ProviderMode = ProviderMode.LOCAL

    def __post_init__(self) -> None:
        # Validates only; the validated value itself is not stored here because
        # `build_sleep_time_trigger`'s default `IdleTrigger(config.idle_seconds)` re-validates
        # it at construction, so this config object has nothing further to do with the result.
        validated_threshold(self.idle_seconds, "idle_seconds")
        # Coerce at the boundary, the same way `MemoryProviderConfig.mode` does: a value that
        # arrived from JSON or a settings file is equal-but-not-identical to the enum member --
        # and wrapped in the same domain-specific error, naming the field and the valid choices,
        # rather than letting a bare `ValueError` escape with neither.
        if not isinstance(self.mode, ProviderMode):
            try:
                object.__setattr__(self, "mode", ProviderMode(str(self.mode).strip().lower()))
            except ValueError:
                known = ", ".join(repr(m.value) for m in ProviderMode)
                raise MemoryProviderConfigError(
                    f"Unknown mode {self.mode!r}. Expected one of: {known}.",
                    field_name="mode",
                ) from None


def build_sleep_time_trigger(
    config: SleepTimeScheduleConfig, *, inner: Optional[ConsolidationTrigger] = None
) -> ConsolidationTrigger:
    """Builds the trigger to hand ``ConsolidationWorker(..., trigger=...)``.

    ``inner`` defaults to a fresh :class:`IdleTrigger` sized from ``config.idle_seconds`` -- the
    ordinary case, "fire once a scope has been quiet ``idle_seconds`` inside the window." A caller
    combining idle with another policy (event-count, size) passes its own composed
    :class:`~egpt.memory.consolidation.triggers.AnyTrigger` here instead, so sleep-time windowing
    layers on top of whatever trigger policy the deployment already runs.

    ``config.mode`` is not read here or forwarded anywhere -- see :class:`SleepTimeScheduleConfig`'s
    own docstring for why it is carried for observability only.
    """
    return SleepTimeTrigger(inner if inner is not None else IdleTrigger(config.idle_seconds), config.window)
