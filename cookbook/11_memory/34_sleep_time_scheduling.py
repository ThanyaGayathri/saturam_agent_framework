"""
Sleep-Time Compute Scheduling
=============================

This example shows sleep-time scheduling (SM-L9): confining heavy consolidation
to a configured idle/off-peak window instead of letting `ConsolidationWorker`
drain a quiet scope at any hour.

Key concepts:
- `SleepWindow` is a daily wall-clock window, evaluated in a named timezone --
  here `America/New_York`, to show the conversion is not just UTC passthrough.
- `SleepTimeScheduleConfig` bundles the window, the idle threshold, and
  `mode` (Mode A / `ProviderMode.LOCAL` by default -- the deployment where an
  idle owned GPU is free capacity, which is the cost argument for scheduling
  at all).
- `build_sleep_time_trigger` wraps a fresh `IdleTrigger` in a
  `SleepTimeTrigger`, so the same object both detects "quiet long enough" and
  "it is currently the configured hour."
- The trigger only *gates firing*; `record_event` still accumulates pending
  work at any hour -- a write at 9am is not lost, it just waits for the
  window.
- `ConsolidationWorker` takes any `ConsolidationTrigger` as `trigger=`, so
  this is a config change, not a worker change -- see `23_consolidation_worker.py`
  for the worker without a trigger at all.

The vector substrate is the same tiny in-memory stand-in `23_consolidation_worker.py`
uses, trimmed further since this file's point is the schedule, not the store. This
needs no database, no API key, and no network -- the "hour" is a controllable fake
clock, not the wall clock, so the example is deterministic regardless of when it runs.
"""

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from egpt.memory import (
    MemoryEvent,
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    Scope,
    Substrate,
    require_scope,
)
from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig
from egpt.memory.consolidation import ConsolidationWorker
from egpt.memory.consolidation.sleeptime import (
    SleepTimeScheduleConfig,
    SleepWindow,
    build_sleep_time_trigger,
)

SCOPE = Scope(tenant_id="acme", user_id="ada")
TZ = "America/New_York"


class InMemoryVector:
    """Keyword overlap standing in for embedding similarity -- see
    `09_memory_provider_seam.py` for the fuller, annotated version this trims."""

    def __init__(self) -> None:
        self.rows: Dict[str, MemoryRecord] = {}

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="upsert")
        for record in records:
            self.rows[record.id] = replace(record, substrate=Substrate.VECTOR)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="aupsert")
        self.upsert(scope, records)

    def search(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="search")
        return []

    async def asearch(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="asearch")
        return self.search(scope, query, tier)

    def scan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="scan")
        rows = [replace(r) for r in self.rows.values() if r.tier is tier]
        rows.sort(key=lambda r: ((r.created_at or 0), r.id))
        if after is not None:
            rows = [r for r in rows if ((r.created_at or 0), r.id) > after]
        return rows if limit is None else rows[:limit]

    async def ascan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        return self.scan(scope, tier, limit, after)

    def get_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="get_record")
        return self.rows.get(id)

    async def aget_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="aget_record")
        return self.get_record(scope, id)


def semantic_rows(vector: InMemoryVector) -> int:
    return sum(1 for r in vector.rows.values() if r.tier is MemoryTier.SEMANTIC)


def eastern(day: int, hour: int, minute: int = 0) -> float:
    """A 2026-01-`day` wall-clock hour in `TZ`, as an epoch second -- January so the
    example is not sensitive to `America/New_York`'s DST transition."""
    return datetime(2026, 1, day, hour, minute, tzinfo=ZoneInfo(TZ)).timestamp()


async def main() -> None:
    vector = InMemoryVector()
    provider = CoLocatedMemoryProvider(
        vector=vector, config=MemoryProviderConfig(provider="co_located")
    )
    provider.write(
        SCOPE, [MemoryEvent(content="a note about deploys", scope=SCOPE, id="e0")]
    )

    # 01:00-06:00 America/New_York -- the schedule's sleep-time window.
    config = SleepTimeScheduleConfig(
        window=SleepWindow(start_hour=1, end_hour=6, tz=TZ), idle_seconds=300
    )
    trigger = build_sleep_time_trigger(config)

    print("Sleep-time scheduling: idle window 01:00-06:00 America/New_York")
    print("=" * 64)
    print(
        f"config.mode: {config.mode.value} (Mode A -- owned compute is idle capacity)"
    )

    # A mutable holder so the worker's `clock` can be moved forward by the example
    # without constructing a new worker each time.
    written_at = eastern(15, 14, 0)  # Jan 15, 14:00 -- outside the window
    clock_state = {"now": written_at}
    worker = ConsolidationWorker(
        provider, [SCOPE], trigger=trigger, clock=lambda: clock_state["now"]
    )

    # The write path reports pending work regardless of the hour -- accumulation
    # is unconditional; only firing is gated by the window.
    trigger.record_event(SCOPE, now=written_at)

    # 10 minutes later: past the 300s idle threshold, but still outside the window.
    clock_state["now"] = eastern(15, 14, 10)

    print()
    print("14:10 -- idle threshold met, but outside the window:")
    outcomes = await worker.run_once()
    print(f"scopes drained this tick: {len(outcomes)}")
    print(f"semantic facts now: {semantic_rows(vector)}")

    # Advance the fake clock into the next window occurrence -- well past the idle
    # threshold measured from the `record_event` call above.
    clock_state["now"] = eastern(16, 2, 0)

    print()
    print("Jan 16, 02:00 -- idle threshold met, and inside the window:")
    outcomes = await worker.run_once()
    print(f"scopes drained this tick: {len(outcomes)}")
    print(f"fully drained: {outcomes[0].drained if outcomes else None}")
    print(f"semantic facts now: {semantic_rows(vector)}")


if __name__ == "__main__":
    asyncio.run(main())
