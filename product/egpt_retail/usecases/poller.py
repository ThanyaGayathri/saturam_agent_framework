"""The in-process poller (K16): the local stand-in for the cloud orchestrator.

Every activated use case with a schedule is run when its interval has elapsed since its
last run, from inside the API process, through the same ``UseCaseService.start_run`` and
``execute`` the Run button and the cloud orchestrator use. Off unless
``EGPT_RETAIL_USE_CASE_POLLER`` is set: a run is a write, and a switch nobody threw should
not make one. In Azure a Logic App's recurrence trigger calls ``POST /use-cases/{id}/runs``;
in AWS a Step Functions task does; the body of the run does not know which, and this poller
is simply the one that runs on a laptop.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from egpt_retail.usecases.service import (
    SCHEDULE_SECONDS,
    ActivationRefused,
    UseCaseService,
)

__all__ = ["UseCasePoller"]

log = logging.getLogger(__name__)


class UseCasePoller:
    def __init__(self, service: UseCaseService, *, tick_seconds: float = 30.0) -> None:
        self.service = service
        self.tick_seconds = tick_seconds
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def tick(self, now: Optional[datetime] = None) -> int:
        """Start every run that is due. Returns how many were started."""
        now = now or datetime.now(timezone.utc)
        started = 0
        for row in await self.service.store.alist(status="activated"):
            interval = SCHEDULE_SECONDS.get(str(row.get("schedule") or ""))
            if interval is None:
                continue
            last = await self.service.store.alast_run(str(row["id"]))
            if last is not None:
                if last.get("status") in {"queued", "running"}:
                    continue
                since = now - _aware(last["started_at"])
                if since < timedelta(seconds=interval):
                    continue
            try:
                run = await self.service.astart_run(
                    str(row["id"]), mode="live", trigger="schedule"
                )
            except ActivationRefused as exc:
                log.warning("poller: %s not run: %s", row["id"], exc)
                continue
            self.service.launch(str(run["id"]))
            started += 1
            log.info("poller: started run %s for %s", run["id"], row["id"])
        return started

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except (
                Exception
            ):  # the poller survives a bad tick; the run rows say what failed
                log.exception("poller tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
            except asyncio.TimeoutError:
                continue

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
        await self.service.await_runs()


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)
