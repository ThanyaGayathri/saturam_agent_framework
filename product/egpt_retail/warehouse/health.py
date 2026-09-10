"""KPI health: the contract's ``KpiHealth`` rows, assembled from the registry and gold (R5).

Assembled in Python rather than as one warehouse view because the two halves live in two
databases: the registry (control plane) knows every KPI, including ones gold has never seen,
and ``gold.kpi_run`` (warehouse) knows every run. Two of the five statuses the contract names
-- ``never_computed`` and ``not_scheduled`` -- describe registry KPIs with no useful gold row,
so a warehouse-only view could never produce them.

The classification is pure and separately testable; the two reads are the only I/O.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.warehouse.gold import FAILED, MATERIALISED, SKIPPED
from egpt_retail.warehouse.schema import GOLD_SCHEMA

__all__ = [
    "FRESHNESS_BOUND_HOURS",
    "classify_health",
    "kpi_health",
    "akpi_health",
    "latest_runs",
    "latest_materialised_runs",
]

#: A KPI computed for last night is fresh through the working day and the next night's run;
#: past this it is stale. 36 h leaves a whole missed night visible before the second one.
FRESHNESS_BOUND_HOURS = 36.0


def _as_of_timestamp(value: Optional[date]) -> Optional[str]:
    """The contract renders ``as_of`` as a timestamp: the end of the business date, UTC."""
    if value is None:
        return None
    return (
        datetime.combine(value, time(23, 59, 59), tzinfo=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def classify_health(
    kpi: KpiDefinition,
    latest: Optional[Mapping[str, Any]],
    latest_materialised: Optional[Mapping[str, Any]],
    *,
    now: datetime,
    bound_hours: float = FRESHNESS_BOUND_HOURS,
) -> Dict[str, Any]:
    """One ``KpiHealth`` row. Pure.

    ``latest`` is the most recent ``kpi_run`` row of any status; ``latest_materialised`` the
    most recent that wrote values. Status comes from the former, the value stamp from the
    latter, so a KPI whose last run failed shows ``failed`` *and* still says when it last
    computed -- the number on the screen is old, and the screen says both things.
    """
    row: Dict[str, Any] = {
        "kpi_id": kpi.identifier,
        "identifier": kpi.identifier,
        "status": "never_computed",
        "last_computed_at": None,
        "as_of": None,
        "staleness_hours": None,
        "last_run_id": None,
        "last_error": None,
    }
    if latest_materialised is not None:
        finished = latest_materialised.get("finished_at")
        row["last_computed_at"] = _iso(finished)
        row["as_of"] = _as_of_timestamp(latest_materialised.get("as_of"))
        if finished is not None:
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=timezone.utc)
            row["staleness_hours"] = round((now - finished).total_seconds() / 3600.0, 2)
    if latest is None:
        return row
    row["last_run_id"] = latest.get("run_id")
    row["last_error"] = latest.get("error")
    status = latest.get("status")
    if status == FAILED:
        row["status"] = "failed"
    elif status == SKIPPED:
        row["status"] = "not_scheduled"
        row["last_error"] = latest.get("reason")
    elif status == MATERIALISED:
        staleness = row["staleness_hours"]
        row["status"] = (
            "stale" if staleness is not None and staleness > bound_hours else "fresh"
        )
    return row


def latest_runs(engine: Engine) -> Dict[str, Dict[str, Any]]:
    """``gold.kpi_latest_run``, keyed by identifier."""
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT kpi_identifier, run_id, as_of, status, error, reason, finished_at "
                f"FROM {GOLD_SCHEMA}.kpi_latest_run"
            )
        ).mappings()
        return {row["kpi_identifier"]: dict(row) for row in rows}


def latest_materialised_runs(engine: Engine) -> Dict[str, Dict[str, Any]]:
    """The most recent run per KPI that wrote values, keyed by identifier."""
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT DISTINCT ON (kpi_identifier) kpi_identifier, run_id, as_of, finished_at "
                f"FROM {GOLD_SCHEMA}.kpi_run WHERE status = :status "
                "ORDER BY kpi_identifier, started_at DESC"
            ),
            {"status": MATERIALISED},
        ).mappings()
        return {row["kpi_identifier"]: dict(row) for row in rows}


def kpi_health(
    kpis: Sequence[KpiDefinition],
    engine: Engine,
    *,
    now: Optional[datetime] = None,
    bound_hours: float = FRESHNESS_BOUND_HOURS,
) -> List[Dict[str, Any]]:
    """``KpiHealth`` rows for every registry KPI, in identifier order."""
    now = now or datetime.now(timezone.utc)
    latest = latest_runs(engine)
    materialised = latest_materialised_runs(engine)
    return [
        classify_health(
            kpi,
            latest.get(kpi.identifier),
            materialised.get(kpi.identifier),
            now=now,
            bound_hours=bound_hours,
        )
        for kpi in sorted(kpis, key=lambda k: k.identifier)
    ]


async def akpi_health(
    kpis: Sequence[KpiDefinition], engine: Engine, **kwargs: Any
) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(kpi_health, kpis, engine, **kwargs)
