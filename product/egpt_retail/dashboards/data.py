"""``read_dashboard_data`` (R13): every tile's values, from gold, in one read.

Calls the *existing* ``read_gold`` executor (``executors/gold.py``) directly -- it is a
plain ``async def(step_input)`` function, not workflow-engine-only -- with every tile's
``kpi_identifier`` batched into one call. That function's own docstring names this exact
seam: "the seam an interactive playground reads through." One ``read_gold`` call per
dashboard, never one per tile, matching the API contract's own note that this is what keeps
a four-tile dashboard from issuing four warehouse scans on every open.

**Deliberately simplified for this PR, and said so rather than guessed at:**

* ``delta`` (change against the previous period) ships ``null``. It needs a second,
  previous-period read that neither R13's Verify nor Evidence bullet exercises.
* ``series`` (for ``line``/``bar`` tiles) ships empty. ``read_gold`` returns the *latest*
  materialised value per KPI, not a history across dates -- a real series needs a
  gold-history reader this package does not build. A ``number`` or ``table`` tile is
  unaffected; it never reads ``series``.

Both are honest gaps, the same posture R7's ``tables_exposed``/``guard_role`` placeholders
take: present in the schema, absent in the value, and never invented.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Optional, Sequence, cast

from egpt.workflow.types import StepInput

from egpt_retail.api.models import DashboardData, KpiHealthStatus, TileData
from egpt_retail.contracts.dashboards import ChartKind, Dashboard, Tile
from egpt_retail.executors.gold import read_gold

__all__ = ["read_dashboard_data"]

#: Every column a gold view row carries besides the grain keys -- the rest, whatever their
#: name, are the dimension a ``grain_value`` addresses.
_ROW_METADATA_KEYS = {"as_of", "value", "computed_at", "source_snapshot", "run_id"}


def _row_for_grain(
    rows: Sequence[Dict[str, Any]], grain_value: str
) -> Optional[Dict[str, Any]]:
    for row in rows:
        if any(
            v == grain_value for key, v in row.items() if key not in _ROW_METADATA_KEYS
        ):
            return row
    return None


def _end_of_business_day(value: Optional[str]) -> Optional[datetime]:
    """``read_gold``'s ``as_of`` is a bare business date (``date.isoformat()``, no time or
    zone); ``TileData.as_of`` is a strict ``AwareDatetime``. The common.yaml and
    dashboards.yaml worked examples both stamp a business date at its end
    (``"...T23:59:59Z"``), the same convention gold's own freshness stamps use -- so that is
    what this converts to, rather than midnight, which would read as a different moment."""
    if value is None:
        return None
    return datetime.combine(
        date.fromisoformat(value), time(23, 59, 59), tzinfo=timezone.utc
    )


def _status_for_missing(reason: str) -> KpiHealthStatus:
    if "guard refused" in reason:
        return KpiHealthStatus.failed
    if "never materialised" in reason or "no gold row" in reason:
        return KpiHealthStatus.never_computed
    return KpiHealthStatus.failed


def _empty_tile_data(
    tile: Tile,
    status: KpiHealthStatus,
    *,
    as_of: Optional[datetime] = None,
    computed_at: Optional[str] = None,
    provenance_ref: Optional[str] = None,
) -> TileData:
    """The shape every "nothing to show" case renders as -- unavailable, never a broken
    response and never mistaken for a genuinely current, empty tile."""
    return TileData(
        tile_id=tile.id,
        kpi_identifier=tile.kpi_identifier,
        status=status,
        value=None,
        unit="",
        delta=None,
        series=[],
        rows=[],
        as_of=as_of,
        computed_at=computed_at,
        provenance_ref=provenance_ref,
    )


def _tile_data(
    tile: Tile, result: Optional[Dict[str, Any]], missing_reason: str
) -> TileData:
    if result is None:
        return _empty_tile_data(tile, _status_for_missing(missing_reason))

    rows: List[Dict[str, Any]] = result["rows"]
    if tile.grain_value is not None:
        target_row = _row_for_grain(rows, tile.grain_value)
    elif len(rows) == 1:
        target_row = rows[0]
    else:
        target_row = None

    # A table tile with no single target row shows the whole listing -- that is the tile's
    # own configuration (no grain, more than one row), not a resolution failure. Every other
    # shape expected exactly one value; finding none (a `grain_value` that matched no
    # dimension row, or an ambiguous multi-row result with no grain to disambiguate) is a
    # real gap, not "fresh and empty" -- reporting `fresh` here would be indistinguishable
    # from a genuinely current, correctly empty tile.
    if target_row is None and tile.chart is not ChartKind.TABLE:
        return _empty_tile_data(
            tile,
            KpiHealthStatus.never_computed,
            as_of=_end_of_business_day(result.get("as_of")),
            computed_at=result.get("computed_at"),
            provenance_ref=result.get("run_id"),
        )

    value = (
        float(target_row["value"])
        if target_row is not None and target_row.get("value") is not None
        else None
    )
    table_rows = rows if target_row is None else []

    return TileData(
        tile_id=tile.id,
        kpi_identifier=tile.kpi_identifier,
        status=KpiHealthStatus.fresh,
        value=value,
        unit="",
        delta=None,
        series=[],
        rows=table_rows,
        as_of=_end_of_business_day(result.get("as_of")),
        computed_at=result.get("computed_at"),
        provenance_ref=result.get("run_id"),
    )


async def read_dashboard_data(dashboard: Dashboard) -> DashboardData:
    """Every tile's values, from gold and nothing else -- one ``read_gold`` call for the
    whole dashboard."""
    identifiers = sorted({tile.kpi_identifier for tile in dashboard.tiles})
    step_input = StepInput(additional_data={"kpi_identifiers": identifiers})
    output = await read_gold(step_input)
    # `read_gold` always returns `StepOutput(content={"kpis": ..., "missing": ...})`;
    # `StepOutput.content`'s declared type is a broad union because other executors return
    # other shapes, so this is a narrowing cast, not an assumption this function invents.
    content = cast(Dict[str, Any], output.content or {})
    values: Dict[str, Dict[str, Any]] = content.get("kpis", {})
    missing_reasons: Dict[str, str] = {
        entry["kpi_identifier"]: entry.get("reason", "")
        for entry in content.get("missing", [])
    }

    tiles = [
        _tile_data(
            tile,
            values.get(tile.kpi_identifier),
            missing_reasons.get(tile.kpi_identifier, ""),
        )
        for tile in dashboard.tiles
    ]
    return DashboardData(
        dashboard_id=dashboard.id,
        tiles=tiles,
        read_at=datetime.now(timezone.utc),
    )
