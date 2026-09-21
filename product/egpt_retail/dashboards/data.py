"""``read_dashboard_data`` (R13): every tile's values in one read -- ``kpi`` tiles from gold,
``query`` tiles from their cached result.

Calls the *existing* ``read_gold`` executor (``executors/gold.py``) directly -- it is a
plain ``async def(step_input)`` function, not workflow-engine-only -- with every ``kpi``
tile's ``kpi_identifier`` batched into one call. That function's own docstring names this
exact seam: "the seam an interactive playground reads through." One ``read_gold`` call per
dashboard, never one per tile, matching the API contract's own note that this is what keeps
a four-tile dashboard from issuing four warehouse scans on every open.

A ``query`` tile (contract 1.2.0) is read from ``dashboard_query_result``
(:mod:`egpt_retail.dashboards.query_results`), filled by :mod:`egpt_retail.dashboards.queries`
when the tile is written and on ``POST /dashboards/{id}/refresh``. The caller hands the
cached records in (``query_results``); this module never runs a tile's SQL, so a dashboard
open stays a read of two tables. A tile with no cached row is ``never_computed``; one whose
last run was refused or failed is ``failed`` -- both render as unavailable, never as a broken
response and never as a genuinely current, empty tile.

``series`` and ``delta`` for a ``kpi`` tile come from the same gold call: ``read_gold``'s
``history_days`` option returns each KPI's rows for the last :data:`SERIES_LOOKBACK_DAYS`
business dates next to the current one, and this module resolves each date's row the same
way it resolves the current one (the tile's ``grain_value``, or the single row at grain
``overall``). ``gold.kpi_value`` keeps every nightly row, so a series is a read, not a
recomputation. A ``line``/``bar`` tile gets one point per business date that had a row for
its grain; every tile that resolved a current value gets ``delta`` = current value minus the
value at the previous business date. A date with no row for the tile's grain is left out of
the series rather than plotted as ``null`` -- ``null`` means the formula returned NULL, which
is a different fact. A KPI with one materialised date has ``delta: null``: there is no
previous period, and nothing is invented. The history is requested only for the KPI
identifiers a non-``table`` tile actually reads (``history_for``), so a dashboard of
``table`` tiles -- or a KPI shown only on ``table`` tiles in a dashboard that mixes chart
kinds -- costs exactly what it did before.

For a ``query`` tile the series is the cached rows themselves, read through the tile's own
axes: an x axis of ``kind: time`` and the first y axis give one point per row; a ``number``
chart's value is the first y column of the first row; ``delta`` is the last two points of a
time series. ``rows`` and ``columns`` always carry the full cached result, so a table chart
and the editor's preview render from one shape.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, cast

from egpt.workflow.types import StepInput

from egpt_retail.alerts.condition import as_decimal
from egpt_retail.api.models import DashboardData, KpiHealthStatus, SeriesPoint, TileData
from egpt_retail.contracts.dashboards import (
    AxisKind,
    ChartKind,
    Dashboard,
    Tile,
    TileSource,
)
from egpt_retail.dashboards.query_results import QueryResultRecord
from egpt_retail.executors.gold import (
    GOLD_ROW_METADATA_KEYS,
    read_gold,
    row_for_grain_value,
)
from egpt_retail.kpi.series import (
    end_of_business_day as _end_of_business_day,
)
from egpt_retail.kpi.series import (
    history_by_date as _history_by_date,
)
from egpt_retail.kpi.series import (
    status_for_missing as _status_for_missing,
)

__all__ = ["SERIES_LOOKBACK_DAYS", "query_tile_data", "read_dashboard_data"]

#: Business dates of history a ``line``/``bar`` tile plots and ``delta`` reads back over.
#: Gold materialises nightly (D7), so this is at most one point per day.
SERIES_LOOKBACK_DAYS = 30

#: Only these chart kinds plot a series; ``number`` and ``table`` never read it.
_SERIES_CHARTS = frozenset({ChartKind.LINE, ChartKind.BAR})


def _row_for_grain(
    rows: Sequence[Dict[str, Any]], grain_value: str
) -> Optional[Dict[str, Any]]:
    """Thin alias over ``executors.gold.row_for_grain_value`` -- kept so this module's own
    call sites read the same as before. See that function's own docstring for the ambiguity
    it accepts: a ``Tile`` has no ``grain_column`` to disambiguate with, unlike R12's
    ``AlertScope``.

    The ``cast`` is only a type narrowing: ``row_for_grain_value`` returns one of ``rows``
    unchanged, and every row this module ever passes in is already a ``Dict``, not merely a
    ``Mapping`` -- ``row_for_grain_value``'s own signature is the wider ``Mapping`` because
    ``executors/alerts.py``'s caller passes rows it never mutates.
    """
    return cast(Optional[Dict[str, Any]], row_for_grain_value(rows, grain_value))


def _resolve_row(
    tile: Tile, rows: Sequence[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """The one row of a business date this tile shows: the row matching its ``grain_value``,
    or the only row when there is exactly one. ``None`` when nothing resolves -- the same
    rule for the current date and for every date in the history, so a series is made of the
    same row the number tile would show that day."""
    if tile.grain_value is not None:
        return _row_for_grain(rows, tile.grain_value)
    if len(rows) == 1:
        return rows[0]
    return None


def _empty_tile_data(
    tile: Tile,
    status: KpiHealthStatus,
    *,
    as_of: Optional[datetime] = None,
    computed_at: Optional[Any] = None,
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
        columns=[],
        as_of=as_of,
        computed_at=computed_at,
        provenance_ref=provenance_ref,
    )


def _series_and_delta(
    tile: Tile, result: Mapping[str, Any], current_value: Optional[str]
) -> Tuple[List[SeriesPoint], Optional[float]]:
    """The tile's series (``line``/``bar`` only) and its delta against the previous business
    date, both from ``result["history"]`` resolved date by date with :func:`_resolve_row`.

    The delta is computed via :func:`~egpt_retail.alerts.condition.as_decimal` from the
    ``str`` values ``read_gold`` hands out, then converted to ``float`` once at the end:
    ``0.91 - 0.95`` in binary float is ``-0.040000000000000036``, and the DDL chose
    ``numeric`` precisely so a customer never reads that. A tile with no current value has
    no delta, whatever the history holds.

    "Previous" is the resolved date whose ``date.fromisoformat()`` value is earliest before
    the current one, not a lexicographic string comparison -- ``as_of`` happens to be a
    zero-padded ISO date today, but the comparison should not depend on that staying true.
    """
    resolved: List[Tuple[str, Optional[str]]] = []
    for as_of, rows in _history_by_date(result.get("history", ())):
        row = _resolve_row(tile, rows)
        if row is None:
            continue
        raw = row.get("value")
        resolved.append((as_of, None if raw is None else str(raw)))

    series: List[SeriesPoint] = []
    if tile.chart in _SERIES_CHARTS:
        series = [
            SeriesPoint(
                at=cast(datetime, _end_of_business_day(as_of)),
                value=None if raw is None else float(raw),
            )
            for as_of, raw in resolved
        ]

    delta: Optional[float] = None
    current_as_of = date.fromisoformat(str(result.get("as_of")))
    if current_value is not None:
        previous = [
            raw for as_of, raw in resolved if date.fromisoformat(as_of) < current_as_of
        ]
        if previous and previous[-1] is not None:
            delta = float(as_decimal(current_value) - as_decimal(previous[-1]))
    return series, delta


def _breakdown_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """A gold row as a listing row: the grain keys first, then ``value``, without the
    per-row metadata (``as_of``, ``computed_at``, ``run_id``) a table would otherwise show."""
    keys = {
        k: v for k, v in row.items() if k not in GOLD_ROW_METADATA_KEYS and k != "value"
    }
    value = row.get("value")
    return {**keys, "value": float(value) if value is not None else None}


def _tile_data(
    tile: Tile,
    result: Optional[Dict[str, Any]],
    missing_reason: str,
    unit: str = "",
) -> TileData:
    if result is None:
        return _empty_tile_data(tile, _status_for_missing(missing_reason))

    rows: List[Dict[str, Any]] = result["rows"]
    target_row = _resolve_row(tile, rows)

    # A table tile configured with *no* grain_value shows the whole listing when it has no
    # single target row -- that is the tile's own configuration (more than one row, nothing
    # to scope to), not a resolution failure. Every other case expected exactly one value:
    # finding none -- a `grain_value` (table tile or not) that matched no dimension row, or
    # an ambiguous multi-row result with no grain to disambiguate -- is a real gap, not
    # "fresh and empty". A table tile *with* a `grain_value` that matched nothing must not
    # fall through to showing the unrelated, unfiltered listing -- that is as wrong as
    # reporting `fresh` with an empty value.
    # A bar tile with no grain_value on a KPI at a grain is the breakdown across that grain
    # -- quotes by brand, lost reasons -- drawn as category bars from the same rows a table
    # tile lists (K22). One value per bar, the current business date's; no series.
    table_showing_everything = (
        tile.chart in (ChartKind.TABLE, ChartKind.BAR)
        and tile.grain_value is None
        and len(rows) > 1
    ) or (tile.chart is ChartKind.TABLE and tile.grain_value is None)
    if target_row is None and not table_showing_everything:
        return _empty_tile_data(
            tile,
            KpiHealthStatus.never_computed,
            as_of=_end_of_business_day(result.get("as_of")),
            computed_at=result.get("computed_at"),
            provenance_ref=result.get("run_id"),
        )

    raw_value = target_row.get("value") if target_row is not None else None
    value = float(raw_value) if raw_value is not None else None
    table_rows = (
        sorted(
            (_breakdown_row(row) for row in rows),
            key=lambda r: (r["value"] is None, -(r["value"] or 0)),
        )
        if target_row is None
        else []
    )
    series, delta = (
        _series_and_delta(tile, result, None if raw_value is None else str(raw_value))
        if target_row is not None
        else ([], None)
    )

    return TileData(
        tile_id=tile.id,
        kpi_identifier=tile.kpi_identifier,
        status=KpiHealthStatus.fresh,
        value=value,
        unit=unit,
        delta=delta,
        series=series,
        rows=table_rows,
        columns=[],
        as_of=_end_of_business_day(result.get("as_of")),
        computed_at=result.get("computed_at"),
        provenance_ref=result.get("run_id"),
    )


# --- query tiles ---------------------------------------------------------------------------


def _as_datetime(value: Any) -> Optional[datetime]:
    """A cached x value as an aware datetime, or ``None`` when it is not one. Cached rows hold
    ISO strings (``queries._serialise``); a bare date becomes that day's midnight UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            day = date.fromisoformat(value)
        except ValueError:
            return None
        return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def query_tile_data(tile: Tile, record: Optional[QueryResultRecord]) -> TileData:
    """One ``query`` tile's ``TileData`` from its cached record. Pure -- no I/O -- so the
    axis-to-series mapping is unit-testable on its own."""
    assert tile.query is not None
    if record is None:
        return _empty_tile_data(tile, KpiHealthStatus.never_computed)
    if record.error:
        return _empty_tile_data(
            tile, KpiHealthStatus.failed, computed_at=record.computed_at
        )

    query = tile.query
    rows = [dict(row) for row in record.rows]
    columns = list(record.columns)
    y_axis = query.y[0] if query.y else None
    y_column = y_axis.column if y_axis is not None else None
    if y_column is None or y_column not in columns:
        # No usable y axis named: the last column is the value, the way a person reading a
        # `SELECT wk, count(*)` would take it.
        y_column = columns[-1] if columns else None
    unit = y_axis.unit if y_axis is not None else ""

    value: Optional[float] = None
    if rows and y_column is not None:
        value = _as_float(rows[0].get(y_column))

    series: List[SeriesPoint] = []
    if (
        query.x is not None
        and query.x.kind is AxisKind.TIME
        and query.series_by is None
        and y_column is not None
        and tile.chart in _SERIES_CHARTS
    ):
        for row in rows:
            at = _as_datetime(row.get(query.x.column))
            if at is None:
                continue
            series.append(SeriesPoint(at=at, value=_as_float(row.get(y_column))))
        series.sort(key=lambda p: p.at)

    delta: Optional[float] = None
    plotted = [p.value for p in series if p.value is not None]
    if len(plotted) >= 2:
        delta = plotted[-1] - plotted[-2]
        value = plotted[-1]

    return TileData(
        tile_id=tile.id,
        kpi_identifier=None,
        status=KpiHealthStatus.fresh,
        value=value,
        unit=unit,
        delta=delta,
        series=series,
        rows=rows,
        columns=columns,
        as_of=None,
        computed_at=record.computed_at,
        provenance_ref=None,
    )


# --- the read ------------------------------------------------------------------------------


def _units_for(identifiers: Sequence[str]) -> Dict[str, str]:
    """``identifier -> unit`` from the KPI registry; empty when nothing is registered or
    the control plane does not answer (the tiles then show bare numbers, as before)."""
    if not identifiers:
        return {}
    try:
        from egpt_retail.app.db import pooled_control_plane_connection
        from egpt_retail.app.settings import RetailSettings
        from egpt_retail.kpi.store import KpiRegistryStore

        connection = pooled_control_plane_connection(RetailSettings())
        try:
            store = KpiRegistryStore(connection)
            return {
                i: str(getattr(d, "unit", "") or "")
                for i in identifiers
                if (d := store.get(i)) is not None
            }
        finally:
            connection.close()
    except Exception:  # the registry is context, never the tile's blocker
        return {}


async def read_dashboard_data(
    dashboard: Dashboard,
    *,
    query_results: Optional[Mapping[str, QueryResultRecord]] = None,
) -> DashboardData:
    """Every tile's values -- one ``read_gold`` call for the ``kpi`` tiles, with history when
    any of them plots a series or shows a delta, and the caller's ``query_results`` (keyed by
    tile id) for the ``query`` tiles. Nothing here runs a tile's SQL."""
    kpi_tiles = [t for t in dashboard.tiles if t.source is TileSource.KPI]
    values: Dict[str, Dict[str, Any]] = {}
    missing_reasons: Dict[str, str] = {}
    if kpi_tiles:
        identifiers = sorted({cast(str, t.kpi_identifier) for t in kpi_tiles})
        additional_data: Dict[str, Any] = {"kpi_identifiers": identifiers}
        history_for = sorted(
            {
                cast(str, tile.kpi_identifier)
                for tile in kpi_tiles
                if tile.chart is not ChartKind.TABLE
            }
        )
        if history_for:
            additional_data["history_days"] = SERIES_LOOKBACK_DAYS
            additional_data["history_for"] = history_for
        step_input = StepInput(additional_data=additional_data)
        output = await read_gold(step_input)
        # `read_gold` always returns `StepOutput(content={"kpis": ..., "missing": ...})`;
        # `StepOutput.content`'s declared type is a broad union because other executors
        # return other shapes, so this is a narrowing cast, not an assumption this function
        # invents.
        content = cast(Dict[str, Any], output.content or {})
        values = content.get("kpis", {})
        missing_reasons = {
            entry["kpi_identifier"]: entry.get("reason", "")
            for entry in content.get("missing", [])
        }

    # The unit each KPI declares in the registry, so a tile can say $9.67M or 18.5%
    # rather than a bare number. Read once per dashboard; a registry that does not
    # answer leaves the units blank, never the tiles.
    units = _units_for(sorted({cast(str, t.kpi_identifier) for t in kpi_tiles}))
    cached = query_results or {}
    tiles = [
        (
            query_tile_data(tile, cached.get(tile.id))
            if tile.source is TileSource.QUERY
            else _tile_data(
                tile,
                values.get(cast(str, tile.kpi_identifier)),
                missing_reasons.get(cast(str, tile.kpi_identifier), ""),
                units.get(cast(str, tile.kpi_identifier), ""),
            )
        )
        for tile in dashboard.tiles
    ]
    return DashboardData(
        dashboard_id=dashboard.id,
        tiles=tiles,
        read_at=datetime.now(timezone.utc),
    )
