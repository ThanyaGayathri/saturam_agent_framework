"""``ConsolidationWorker`` -- the async runner that drains ``MemoryProvider.consolidate`` off the
hot path (SM-L1 #122).

What it consumes as "the write-back queue" (SM-E2 #109) is the episodic tier itself: SM-E2 shipped
no separate queue or outbox, only ``write``/``awrite`` landing episodes durably (see
``egpt.memory.writeback``'s module docstring). A pass over the episodic tier *is* draining that
queue -- which is exactly what ``consolidate`` already does.

Reused rather than reinvented: ``egpt.scheduler`` (``ScheduleExecutor``/``SchedulePoller``) exists,
but its unit of work is "POST an HTTP endpoint, optionally poll a run status" against a DB-backed
``Schedule`` row -- not "call a coroutine". Repurposing it for a Python callable is SM-L9's (#130),
which also owns cron scheduling for this worker. What is reused here is the *shape*: poll, sleep,
cancel-and-await on stop, structurally the same as ``SchedulePoller``.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Union

from egpt.memory.consolidation.triggers import ConsolidationTrigger
from egpt.memory.provider import MemoryProvider
from egpt.memory.types import ConsolidationReport, Scope, entity_extraction_complete
from egpt.utils.log import log_error, log_info, log_warning

__all__ = ["ConsolidationOutcome", "ConsolidationWorker"]

#: Either a fixed list of scopes, or a callable re-consulted every tick -- so a caller whose
#: tenant set grows can hand over a function rather than restart the worker.
ScopeSource = Union[Sequence[Scope], Callable[[], Sequence[Scope]]]

_DEFAULT_STOP_TIMEOUT = 30.0


def _resolve_scopes(source: ScopeSource) -> Sequence[Scope]:
    return source() if callable(source) else source


def _positive(value: float, option: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{option} must be a positive number, got {value!r}.")
    return value


def _positive_int(value: int, option: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{option} must be an integer >= 1, got {value!r}.")
    return value


def _non_negative_int(value: int, option: str) -> int:
    """For ``max_retries``, where 0 is a legitimate "fail fast, no retry" -- the retry loop
    already handles it correctly (``attempt=1 > 0`` raises on the first failure)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{option} must be an integer >= 0, got {value!r}.")
    return value


@dataclass
class ConsolidationOutcome:
    """What one scope's drain attempt did, in one tick.

    ``reports`` accumulates every pass this tick ran for the scope, in order -- a scope larger
    than one pass has more than one. ``error`` is set only when retries were exhausted; the passes
    that did land before the failure are still in ``reports``, because a pass that committed is
    not undone by a later one failing (each pass upserts by content-derived id, so nothing here
    needs to be rolled back).
    """

    scope: Scope
    reports: List[ConsolidationReport] = field(default_factory=list)
    error: Optional[Exception] = None

    @property
    def drained(self) -> bool:
        """Whether the last pass this tick ran left nothing pending -- both the top-level pass
        and, per the interface's own warning, any entity-extraction tail inside it.

        ``details.get("complete", False)``, not a bare subscript: ``CONSOLIDATION_KEYS`` requires
        every provider to carry the key, but this worker takes a ``MemoryProvider`` by protocol,
        not by concrete type, and a third-party implementation that omits it should read as "not
        drained" -- worth another pass -- rather than crash the whole tick with a ``KeyError``.
        """
        if self.error is not None or not self.reports:
            return False
        details = self.reports[-1].details
        return bool(details.get("complete", False)) and entity_extraction_complete(details)


class ConsolidationWorker:
    """Periodically drains ``consolidate``/``aconsolidate`` for a set of scopes.

    **Idempotent**: every retry and every re-run after a restart calls the same
    ``aconsolidate(scope)`` a fresh caller would, and the provider's own upsert-by-content-id
    guarantee (SM-C1) is what makes that safe -- this class adds no de-duplication of its own,
    because it needs none.

    **Retryable**: a failed pass is retried up to ``max_retries`` times with a fixed delay before
    the scope's outcome is marked failed for this tick; the next tick tries again from wherever
    the provider's own cursor sits (SM-L1's internal cursor on ``CoLocatedMemoryProvider``, or the
    beginning, for a provider that tracks none).

    **Off the hot path**: ``start``/``stop`` run this as a background ``asyncio`` task. Nothing
    here is called from ``read`` or ``write``.
    """

    def __init__(
        self,
        provider: MemoryProvider,
        scopes: ScopeSource,
        *,
        poll_interval: float = 300.0,
        max_passes_per_scope: int = 20,
        max_retries: int = 3,
        retry_delay: float = 5.0,
        stop_timeout: float = _DEFAULT_STOP_TIMEOUT,
        trigger: Optional[ConsolidationTrigger] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.provider = provider
        self.scopes = scopes
        self.poll_interval = _positive(poll_interval, "poll_interval")
        #: A per-tick safety valve, not a policy: one huge tenant's tail rolls into the next tick
        #: rather than blocking every other scope's drain in this one.
        self.max_passes_per_scope = _positive_int(max_passes_per_scope, "max_passes_per_scope")
        self.max_retries = _non_negative_int(max_retries, "max_retries")
        self.retry_delay = _positive(retry_delay, "retry_delay")
        self.stop_timeout = _positive(stop_timeout, "stop_timeout")
        #: SM-L2's trigger policy (#123). `None` preserves SM-L1's original behaviour exactly --
        #: every configured scope drains on every tick, regardless of whether anything changed.
        self.trigger = trigger
        self.clock = clock
        #: Every scope `_resolve_scopes` returned as of the last tick, keyed by `.key()` -- only
        #: populated (and consulted) when `trigger` is set. A dynamic `ScopeSource` can shrink
        #: (tenant offboarded, scope no longer configured); without this, that scope's trigger
        #: state (`EventCountTrigger`'s counter, `IdleTrigger`'s last-seen time, ...) would never
        #: be cleared, since `reset` only ever runs for scopes the *current* resolution still
        #: names -- unbounded growth over the worker's lifetime in a deployment with tenant churn.
        self._known_scopes: Dict[str, Scope] = {}
        self._task: Optional["asyncio.Task[None]"] = None
        self._running = False
        self._wake: Optional[asyncio.Event] = None

    async def run_once(self) -> List[ConsolidationOutcome]:
        """Drain every configured scope once, concurrently, and return what happened to each.

        One scope's exhausted retries do not stop the others -- each scope's failure is caught
        inside :meth:`_drain_scope`, never propagated through ``gather``, which is what "one
        tenant's backend is down" must not mean "no tenant gets consolidated this tick".

        **Async-only, no sync twin, deliberately** (CLAUDE.md's sync+async rule): concurrency
        here is real ``await`` via ``gather`` over ``provider.aconsolidate``, not a wrapper around
        sync work, so a sync twin would need its own event loop -- which conflicts with a caller
        that already has one running, the same reason ``SchedulePoller`` (``egpt/scheduler/``)
        carries no sync twin either. A caller with no loop of its own can run one tick via
        ``asyncio.run(worker.run_once())``.

        Calling this directly while the background loop (``start()``) is already running is
        safe -- there is nothing here it can corrupt, and a scope's cursor tolerates concurrent
        readers per :meth:`MemoryProvider.consolidate`'s own docstring -- but it duplicates work,
        for the same reason a scope's cursor does not serialize concurrent callers on its own:
        both read it before either advances it. Direct calls are for tests and one-shot
        invocations; a caller running the loop should let it drive `run_once` on its own
        schedule instead of also calling it manually.

        **Trigger-gated (SM-L2 #123)**: with ``self.trigger`` set, a scope is drained only when
        ``trigger.should_fire`` says so as of ``self.clock()`` -- a scope that does not fire is
        left out of the returned list entirely, not included with empty reports, so a caller
        counting drained scopes never mistakes "not due" for "drained nothing". A scope drains to
        completion (``outcome.drained``, no error) has the trigger reset afterward; one that
        errors, or hits ``max_passes_per_scope`` with work still pending, keeps its accumulated
        state, so the next tick's `should_fire` still reflects what is genuinely still unconsolidated
        rather than reading as caught up when it is not.

        A scope that drops out of a dynamic ``ScopeSource`` between ticks has its trigger state
        reset too, on the tick it disappears -- otherwise a trigger keyed by scope (like
        ``EventCountTrigger``'s per-scope counters) would keep dict entries alive for the rest of
        the worker's lifetime for a scope nothing will ever resolve to again.
        """
        scopes = _resolve_scopes(self.scopes)
        if self.trigger is not None:
            now = self.clock()
            current_by_key = {scope.key(): scope for scope in scopes}
            departed = [scope for key, scope in self._known_scopes.items() if key not in current_by_key]
            for scope in departed:
                self.trigger.reset(scope)
            self._known_scopes = current_by_key
            scopes = [scope for scope in scopes if self.trigger.should_fire(scope, now=now)]
        outcomes = list(await asyncio.gather(*(self._drain_scope(scope) for scope in scopes)))
        if self.trigger is not None:
            for outcome in outcomes:
                if outcome.error is None and outcome.drained:
                    self.trigger.reset(outcome.scope)
        return outcomes

    async def _drain_scope(self, scope: Scope) -> ConsolidationOutcome:
        outcome = ConsolidationOutcome(scope=scope)
        try:
            for _ in range(self.max_passes_per_scope):
                report = await self._consolidate_with_retry(scope)
                outcome.reports.append(report)
                if outcome.drained:
                    break
        except Exception as exc:
            outcome.error = exc
            log_error(f"Consolidation exhausted its retries for scope {scope.key()!r}: {exc}")
        return outcome

    async def _consolidate_with_retry(self, scope: Scope) -> ConsolidationReport:
        """One pass, retried up to ``max_retries`` times.

        Every attempt is the same idempotent call -- a transient failure mid-pass leaves the
        provider's cursor exactly where the last *successful* pass left it (``co_located.py``
        advances it only after every write lands), so a retry re-attempts precisely the episodes
        that were not yet consolidated, never more and never fewer.

        ``retry_delay`` is fixed, with no jitter or backoff. Low blast radius for this specific
        retry (a missed one defers a pass, not a lost write), but several worker instances
        retrying the same backend on the same fixed delay could synchronize into a repeating
        burst rather than smoothing out -- worth adding if this is ever run multi-replica against
        one backend under real load.
        """
        attempt = 0
        while True:
            try:
                return await self.provider.aconsolidate(scope)
            except Exception as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                log_warning(
                    f"Consolidation pass failed for scope {scope.key()!r} (attempt {attempt}/{self.max_retries}): {exc}"
                )
                await asyncio.sleep(self.retry_delay)

    def start(self) -> None:
        """Start the background loop. Returns immediately, synchronously -- ``asyncio.create_task``
        schedules ``_loop`` but does not run it, so the first tick never runs before this call
        returns, which is the "does not block the hot path" contract. No ``await`` here means no
        sync/async split to maintain: this **is** the sync form, and the only form.

        Must be called from within a running event loop (an ``async def``, or sync code entered
        via ``asyncio.run``) -- ``asyncio.create_task`` requires one and raises otherwise. That
        is a property of every ``asyncio`` program, not a caveat specific to this method.

        Guarded on ``self._task``, not ``self._running``: the latter is already ``False`` for the
        entire span of a ``stop()`` call in progress (set at its very first line, before the
        ``await`` that can yield to a concurrent ``start()``), so a ``start()`` landing inside
        that window would pass a ``_running``-only guard, create a second task, and overwrite
        ``self._task`` out from under the ``stop()`` call awaiting the first one -- orphaning it
        with no reference left to join or cancel. ``self._task`` stays non-``None`` for that same
        window (cleared only after ``stop()`` finishes joining it), so it is the guard that is
        actually true exactly while a loop is running or being torn down.
        """
        if self._task is not None:
            return
        self._running = True
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._loop())
        log_info(f"Consolidation worker started (poll_interval={self.poll_interval}s)")

    async def stop(self) -> None:
        """Stop the loop and wait for the in-flight tick to finish, up to ``stop_timeout``."""
        self._running = False
        if self._wake is not None:
            self._wake.set()
        if self._task is not None:
            task = self._task
            try:
                await asyncio.wait_for(task, timeout=self.stop_timeout)
            except asyncio.TimeoutError:
                # `wait_for`'s own cancel-on-timeout is not guaranteed to have finished delivering
                # `CancelledError` to the task before it raises here (pre-3.11) -- an explicit
                # cancel-and-await closes that gap on every version rather than only the newer one.
                task.cancel()
                log_warning(f"Consolidation worker did not stop within {self.stop_timeout}s; cancelled")
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            # Only if `self._task` is still the task this call joined: a `start()` that raced in
            # and lost (see `start()`'s docstring) cannot happen now that it is guarded on
            # `self._task`, but clearing unconditionally would silently repeat the same class of
            # bug the moment that guard is ever touched again.
            if self._task is task:
                self._task = None
        log_info("Consolidation worker stopped")

    async def _loop(self) -> None:
        assert self._wake is not None  # set by start() immediately before this task is created
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                # Re-raised, not swallowed: `stop()` now awaits this task after cancelling it, and
                # a caller checking `task.cancelled()` should see the true outcome rather than a
                # plain `None` result masquerading as a clean stop.
                raise
            except Exception as exc:
                # _drain_scope already catches per-scope failures; reaching here means run_once's
                # own machinery (scope resolution, gather) broke, which is worth surfacing loudly
                # rather than spinning silently at full speed.
                log_error(f"Consolidation worker tick failed: {exc}")
            if not self._running:
                break
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass
            else:
                # Cleared, not left set: harmless today (`stop()` is the only setter, and it
                # also clears `_running` first, so the loop exits at the top of the next
                # iteration regardless) but a `_wake` left permanently set is a zero-delay spin
                # trap for any future caller that reuses this event to wake the loop early
                # without stopping it.
                self._wake.clear()
