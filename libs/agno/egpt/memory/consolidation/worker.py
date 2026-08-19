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
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Union

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
    error: Optional[BaseException] = None

    @property
    def drained(self) -> bool:
        """Whether the last pass this tick ran left nothing pending -- both the top-level pass
        and, per the interface's own warning, any entity-extraction tail inside it."""
        if self.error is not None or not self.reports:
            return False
        details = self.reports[-1].details
        return bool(details["complete"]) and entity_extraction_complete(details)


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
    ) -> None:
        self.provider = provider
        self.scopes = scopes
        self.poll_interval = _positive(poll_interval, "poll_interval")
        #: A per-tick safety valve, not a policy: one huge tenant's tail rolls into the next tick
        #: rather than blocking every other scope's drain in this one.
        self.max_passes_per_scope = _positive_int(max_passes_per_scope, "max_passes_per_scope")
        self.max_retries = _positive_int(max_retries, "max_retries")
        self.retry_delay = _positive(retry_delay, "retry_delay")
        self.stop_timeout = _positive(stop_timeout, "stop_timeout")
        self._task: Optional["asyncio.Task[None]"] = None
        self._running = False
        self._wake: Optional[asyncio.Event] = None

    async def run_once(self) -> List[ConsolidationOutcome]:
        """Drain every configured scope once, concurrently, and return what happened to each.

        One scope's exhausted retries do not stop the others -- each scope's failure is caught
        inside :meth:`_drain_scope`, never propagated through ``gather``, which is what "one
        tenant's backend is down" must not mean "no tenant gets consolidated this tick".
        """
        scopes = _resolve_scopes(self.scopes)
        return list(await asyncio.gather(*(self._drain_scope(scope) for scope in scopes)))

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

    async def start(self) -> None:
        """Start the background loop. Returns immediately -- the first tick runs on the task,
        not before this call returns, which is the "does not block the hot path" contract."""
        if self._running:
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
            try:
                await asyncio.wait_for(self._task, timeout=self.stop_timeout)
            except asyncio.TimeoutError:
                self._task.cancel()
                log_warning(f"Consolidation worker did not stop within {self.stop_timeout}s; cancelled")
            self._task = None
        log_info("Consolidation worker stopped")

    async def _loop(self) -> None:
        assert self._wake is not None  # set by start() immediately before this task is created
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
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
