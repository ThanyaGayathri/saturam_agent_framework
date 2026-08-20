"""Consolidation trigger policy (SM-L2 #123): decides *when* a scope has accumulated enough
pending work to be worth a :class:`~egpt.memory.consolidation.worker.ConsolidationWorker` tick, so
the worker (SM-L1 #122) does not drain every configured scope on every poll interval regardless of
whether anything changed.

Four concrete trigger classes, matching the ticket's acceptance criteria: :class:`IdleTrigger`
(time -- idle/sleep-time, the signal SM-L9 #130 schedules its idle-window detection against),
:class:`EventCountTrigger` (event count), :class:`SizeTrigger` (a size threshold), and
:class:`LatencyTrigger` (an age/freshness threshold) -- the ticket's "size/latency thresholds" is
these last two, kept separate because cumulative-size and oldest-timestamp tracking are different
policies. :class:`AnyTrigger` combines several of the above with OR semantics.

A caller (typically the write path) reports pending work with ``record_event``; the worker
consults ``should_fire`` before draining a scope and calls ``reset`` after a successful drain.
Wiring ``record_event`` into a specific write path is a caller's decision, not this module's --
none of these triggers know or care what "an event" is, only that ``record_event`` was called.
"""

from dataclasses import dataclass, field
from typing import Dict, Mapping, Protocol, Sequence, Union, runtime_checkable

from egpt.memory.types import Scope
from egpt.utils.log import log_error

__all__ = [
    "ConsolidationTrigger",
    "PerTenantThreshold",
    "ThresholdSource",
    "EventCountTrigger",
    "SizeTrigger",
    "LatencyTrigger",
    "IdleTrigger",
    "AnyTrigger",
]


def _positive(value: float, option: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{option} must be a positive number, got {value!r}.")


@dataclass(frozen=True)
class PerTenantThreshold:
    """A threshold overridable per tenant, falling back to ``default`` for any tenant with no
    explicit entry -- the "configurable per tenant" half of every acceptance criterion here.

    Self-validating: constructing one directly (not just passing it to a trigger) rejects a
    non-positive ``default`` or override immediately, so a bad value cannot sit unnoticed until
    whichever trigger it is eventually handed to happens to validate it.
    """

    default: float
    by_tenant: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _positive(self.default, "default")
        for tenant_id, override in self.by_tenant.items():
            _positive(override, f"by_tenant[{tenant_id!r}]")
        # Defensive copy into a plain, private dict: `by_tenant` accepts any `Mapping`, and
        # `frozen=True` only stops rebinding the field, not a caller holding the original mapping
        # and mutating it after construction -- which would let a threshold already validated
        # above silently turn invalid (e.g. `overrides["acme"] = 0.0`) with nothing here to catch
        # it a second time.
        object.__setattr__(self, "by_tenant", dict(self.by_tenant))

    def resolve(self, tenant_id: str) -> float:
        return self.by_tenant.get(tenant_id, self.default)


#: A plain number applies to every tenant alike; a ``PerTenantThreshold`` narrows per tenant.
ThresholdSource = Union[float, PerTenantThreshold]


def _validated_threshold(value: ThresholdSource, option: str) -> ThresholdSource:
    """Validates a plain threshold. A ``PerTenantThreshold`` validates itself in
    ``__post_init__``, so there is nothing left to check here beyond the plain-number case --
    re-validating it would just repeat work `__post_init__` already did.
    """
    if not isinstance(value, PerTenantThreshold):
        _positive(value, option)
    return value


def _resolve(threshold: ThresholdSource, tenant_id: str) -> float:
    return threshold.resolve(tenant_id) if isinstance(threshold, PerTenantThreshold) else threshold


@runtime_checkable
class ConsolidationTrigger(Protocol):
    """What :class:`ConsolidationWorker` consults before draining a scope, and what it must update
    once it has.

    ``@runtime_checkable`` is a pure addition with no behavioural change -- every trigger here is
    already used structurally, never through ``isinstance`` -- but it lets a caller that wants to
    guard-check a trigger before passing it in actually do so instead of hitting a ``TypeError``.
    """

    def record_event(self, scope: Scope, *, now: float, size: float = 1.0) -> None:
        """Report pending work for ``scope`` at wall-clock time ``now``. ``size`` is an optional
        caller-supplied weight (bytes, episode count, ...); triggers that do not use it ignore it.
        """
        ...

    def should_fire(self, scope: Scope, *, now: float) -> bool:
        """Whether ``scope`` has crossed this trigger's policy as of ``now``."""
        ...

    def reset(self, scope: Scope) -> None:
        """Clear whatever state was accumulated for ``scope``, after a tick has drained it."""
        ...


class _AccumulatingTrigger:
    """Shared machinery for a trigger that fires once a per-scope accumulated total crosses a
    threshold. ``EventCountTrigger`` and ``SizeTrigger`` are this same policy with a different unit
    accumulated per ``record_event`` call -- kept as one implementation so the threshold-crossing
    and per-tenant resolution logic is written, and tested, exactly once rather than twice.
    """

    def __init__(self, threshold: ThresholdSource, *, option: str) -> None:
        self._threshold = _validated_threshold(threshold, option)
        self._totals: Dict[str, float] = {}

    def _accumulated(self, scope: Scope) -> float:
        return self._totals.get(scope.key(), 0.0)

    def _add(self, scope: Scope, amount: float) -> None:
        self._totals[scope.key()] = self._accumulated(scope) + amount

    def should_fire(self, scope: Scope, *, now: float) -> bool:
        return self._accumulated(scope) >= _resolve(self._threshold, scope.tenant_id)

    def reset(self, scope: Scope) -> None:
        self._totals.pop(scope.key(), None)


class EventCountTrigger(_AccumulatingTrigger):
    """Fires once ``threshold`` events have been recorded for a scope since its last reset --
    every ``record_event`` call counts as exactly one event, regardless of ``size``.
    """

    def __init__(self, threshold: ThresholdSource) -> None:
        super().__init__(threshold, option="threshold")

    def record_event(self, scope: Scope, *, now: float, size: float = 1.0) -> None:
        self._add(scope, 1.0)


class SizeTrigger(_AccumulatingTrigger):
    """Fires once the cumulative ``size`` of recorded events (bytes, episode count, or whatever
    unit the caller passes) crosses ``threshold`` for a scope.
    """

    def __init__(self, threshold: ThresholdSource) -> None:
        super().__init__(threshold, option="threshold")

    def record_event(self, scope: Scope, *, now: float, size: float = 1.0) -> None:
        self._add(scope, size)


class LatencyTrigger:
    """Fires once the *oldest* un-reset pending event for a scope has waited ``max_age_seconds`` --
    a freshness SLA, distinct from :class:`IdleTrigger`: idle looks at the most recent event,
    latency looks at the oldest one still un-consolidated. A scope under constant write pressure
    never goes idle but can still breach a latency bound.

    **Known bound, not exactly ``max_age_seconds``, under concurrent writes.** A tick drains a
    scope across an ``await`` (the worker's ``asyncio.gather``); an event recorded mid-drain finds
    ``_oldest_pending_at`` still populated, so ``record_event``'s ``setdefault`` drops its own
    timestamp rather than overwriting the one already being drained -- correct while the drain is
    in flight, since that event may itself be part of what gets drained. If it is not (the drain
    already scanned past it) and the tick's `reset` then clears the entry, that event's own age
    stops being tracked until *its own* `record_event` timestamp would have mattered, which it no
    longer does. The worst case this produces is an effective SLA of
    ``max_age_seconds + this tick's drain duration``, not an unbounded loss -- the next event for
    the scope starts a fresh, correctly-tracked window. Deferred rather than fixed: a fix that
    actually preserves the dropped event's own timestamp needs a per-event queue, not the single
    scalar this class keeps per scope, and no real write path calls `record_event` concurrently
    with a drain yet (see the consolidation package's `record_event`-wiring note) to measure
    whether that complexity is worth it against this documented bound.
    """

    def __init__(self, max_age_seconds: ThresholdSource) -> None:
        self._max_age_seconds = _validated_threshold(max_age_seconds, "max_age_seconds")
        self._oldest_pending_at: Dict[str, float] = {}

    def record_event(self, scope: Scope, *, now: float, size: float = 1.0) -> None:
        # `setdefault`, not overwrite: the first un-reset event is what "oldest" means here, so a
        # later event in the same window must not push the clock forward and mask the breach.
        self._oldest_pending_at.setdefault(scope.key(), now)

    def should_fire(self, scope: Scope, *, now: float) -> bool:
        oldest = self._oldest_pending_at.get(scope.key())
        if oldest is None:
            return False
        return (now - oldest) >= _resolve(self._max_age_seconds, scope.tenant_id)

    def reset(self, scope: Scope) -> None:
        self._oldest_pending_at.pop(scope.key(), None)


class IdleTrigger:
    """Fires once a scope with pending events has gone ``idle_seconds`` without a new one -- the
    "idle/sleep-time" trigger SM-L9 (#130) schedules its idle-window detection against.

    Requires at least one recorded event: a scope nothing has ever written to reads as "nothing to
    consolidate", not "idle enough to fire", so this never fires on a tenant that has simply never
    sent an event, only on one that sent events and then went quiet.
    """

    def __init__(self, idle_seconds: ThresholdSource) -> None:
        self._idle_seconds = _validated_threshold(idle_seconds, "idle_seconds")
        self._last_event_at: Dict[str, float] = {}

    def record_event(self, scope: Scope, *, now: float, size: float = 1.0) -> None:
        self._last_event_at[scope.key()] = now

    def should_fire(self, scope: Scope, *, now: float) -> bool:
        last = self._last_event_at.get(scope.key())
        if last is None:
            return False
        return (now - last) >= _resolve(self._idle_seconds, scope.tenant_id)

    def reset(self, scope: Scope) -> None:
        self._last_event_at.pop(scope.key(), None)


class AnyTrigger:
    """Combines several triggers with OR semantics: fires the moment any one of them would, so a
    caller wiring multiple policies (idle *or* event-count *or* size) hands the worker one object
    instead of looping every configured trigger itself.
    """

    def __init__(self, triggers: Sequence[ConsolidationTrigger]) -> None:
        if not triggers:
            raise ValueError("AnyTrigger requires at least one trigger.")
        self._triggers = list(triggers)

    def record_event(self, scope: Scope, *, now: float, size: float = 1.0) -> None:
        """Fans out to every child, even if one raises -- ``ConsolidationTrigger`` is a
        ``Protocol``, so a caller-supplied implementation (DB-backed, network-backed) can fail
        mid-fan-out. Without the guard here, a raise from one child would leave every later child
        never told about this event, so its own accumulated state would silently drift out of
        sync with what actually happened -- worse than the one child's own failure.
        """
        for trigger in self._triggers:
            try:
                trigger.record_event(scope, now=now, size=size)
            except Exception as exc:
                log_error(f"AnyTrigger: record_event failed for {type(trigger).__name__}: {exc}")

    def should_fire(self, scope: Scope, *, now: float) -> bool:
        """Same all-attempted guarantee as ``record_event``/``reset``: one child raising on
        ``should_fire`` must not stop the *other* children from being consulted -- ``any()`` over a
        bare generator would propagate the first exception and never reach the rest, silently
        turning "one policy is unavailable" into "this scope never fires at all". A raising child
        counts as "no" from that child, not as a tick-ending failure.
        """
        fired = False
        for trigger in self._triggers:
            try:
                fired = trigger.should_fire(scope, now=now) or fired
            except Exception as exc:
                log_error(f"AnyTrigger: should_fire failed for {type(trigger).__name__}: {exc}")
        return fired

    def reset(self, scope: Scope) -> None:
        """Same all-attempted guarantee as ``record_event``: one child raising on ``reset`` must
        not leave every later child still armed -- that scope would then re-fire on the very next
        tick for a child that had, in fact, already been drained.
        """
        for trigger in self._triggers:
            try:
                trigger.reset(scope)
            except Exception as exc:
                log_error(f"AnyTrigger: reset failed for {type(trigger).__name__}: {exc}")
