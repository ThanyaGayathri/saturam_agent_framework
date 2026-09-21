"""Execution health for the use cases the interface created (K20), from the runs table.

The contract's ``UseCaseExecutionHealth`` per use case, computed from ``retail_use_cases``
and ``retail_use_case_runs``: the last run and its status, runs and failures over seven
days, records created and documents held over the same window, and whether the schedule is
*silent* -- one and a half cadences have passed since the last run started and nothing has
fired. What fires the schedule is reported too (``trigger``): the in-process poller when it
is switched on, the orchestrator in the cloud, or nothing, in which case the Run button is
the only trigger and a quiet schedule is not a silent one.

R14's ``assess_execution_health`` reads the framework's own ``WorkflowSession`` history for
the registered workflows; this reads the product's run table for the compiled ones. Same
contract shape, two sources, one screen.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

from egpt_retail.usecases.service import SCHEDULE_SECONDS
from egpt_retail.usecases.store import UseCaseStore

__all__ = [
    "execution_health",
    "aexecution_health",
    "silent_schedules",
    "asilent_schedules",
]

#: How many cadences may pass since the last run before a schedule counts as silent.
SILENCE_FACTOR = 1.5


def _aware(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value)).astimezone(timezone.utc)
    except ValueError:
        return None


def _health_row(
    store: UseCaseStore,
    row: Mapping[str, Any],
    *,
    now: datetime,
    trigger: Optional[str],
) -> Dict[str, Any]:
    use_case_id = str(row["id"])
    runs = store.list_runs(use_case_id, limit=500)
    since = now - timedelta(days=7)
    recent = [r for r in runs if (_aware(r.get("started_at")) or now) >= since]
    live = [r for r in recent if r.get("mode") == "live"]
    last = runs[0] if runs else None
    last_at = _aware(last.get("started_at")) if last else None
    schedule = row.get("schedule")
    interval = SCHEDULE_SECONDS.get(str(schedule or ""))
    activated = row.get("status") == "activated"
    next_expected: Optional[datetime] = None
    silent = False
    if activated and interval and trigger:
        anchor = (
            _aware(last.get("started_at"))
            if last and last.get("mode") == "live"
            else _aware(row.get("updated_at"))
        )
        anchor = anchor or now
        next_expected = anchor + timedelta(seconds=interval)
        silent = (now - anchor) > timedelta(seconds=interval * SILENCE_FACTOR)
    created = sum(
        1
        for r in live
        for d in ((r.get("result") or {}).get("documents") or [])
        if (d.get("written") or {}).get("status") == "written"
    )
    exceptions = sum(len((r.get("result") or {}).get("exceptions") or []) for r in live)
    return {
        "use_case_id": use_case_id,
        "title": row.get("title") or use_case_id,
        "version": int(row.get("version") or 0),
        "status": row.get("status"),
        "schedule": schedule,
        "trigger": trigger if (activated and interval) else None,
        "last_run_status": last.get("status") if last else None,
        "last_run_at": last_at,
        "last_run_summary": (last.get("summary") if last else "") or "",
        "runs_7d": len(recent),
        "failures_7d": sum(1 for r in recent if r.get("status") == "failed"),
        "created_7d": created,
        "exceptions_7d": exceptions,
        "silent": silent,
        "next_expected_at": next_expected,
        "gates_waiting": 0,
        "cost_7d": {"input_tokens": 0, "output_tokens": 0, "drift_pct": None},
        "error": None,
    }


def execution_health(
    store: UseCaseStore, *, trigger: Optional[str], now: Optional[datetime] = None
) -> Dict[str, Any]:
    """The contract's ``ExecutionHealth`` for every use case the interface created."""
    now = now or datetime.now(timezone.utc)
    rows = [
        r
        for r in store.list()
        if r.get("status") in {"activated", "paused"} or r.get("compiled")
    ]
    return {
        "as_of": now,
        "use_cases": [_health_row(store, r, now=now, trigger=trigger) for r in rows],
    }


async def aexecution_health(
    store: UseCaseStore, *, trigger: Optional[str], now: Optional[datetime] = None
) -> Dict[str, Any]:
    return await asyncio.to_thread(execution_health, store, trigger=trigger, now=now)


def silent_schedules(
    store: UseCaseStore, *, trigger: Optional[str], now: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """The contract's ``SilentSchedule`` rows: the use cases whose cadence has elapsed."""
    health = execution_health(store, trigger=trigger, now=now)
    return [
        {
            "schedule_id": f"usecase__{h['use_case_id']}",
            "use_case_id": h["use_case_id"],
            "name": f"{h['title']} · {str(h['schedule']).replace('_', ' ')}",
            "expected_at": h["next_expected_at"],
            "last_fired_at": h["last_run_at"],
        }
        for h in health["use_cases"]
        if h["silent"]
    ]


async def asilent_schedules(
    store: UseCaseStore, *, trigger: Optional[str], now: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(silent_schedules, store, trigger=trigger, now=now)
