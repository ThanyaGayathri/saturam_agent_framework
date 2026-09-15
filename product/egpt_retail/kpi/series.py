"""``read_kpi_series``: one KPI's value over its last business dates, from gold.

The same rows ``dashboards/data.py`` plots for a ``line``/``bar`` tile, addressed by KPI
identifier rather than by tile -- what the discovery detail panel graphs once a KPI is in the
registry. One ``read_gold`` call (``executors/gold.py``, the seam every interactive read goes
through) with ``history_days`` set, so this is a read of ``gold.kpi_value`` and never a
recomputation: a KPI has exactly the points the nightly run (or ``POST /kpis/{id}/compute``)
has written for it, one per business date, oldest first.

The date rules are the dashboard's, shared from here rather than duplicated: a night with no
row for the followed dimension is left out, not plotted as ``null`` (``null`` is the formula
returning NULL, a different fact); ``delta`` is the latest point minus the previous one,
computed exactly via ``as_decimal`` from the ``str`` values ``read_gold`` hands out; a business
date is stamped at its end (``T23:59:59Z``), the convention gold's own freshness stamps use.

A KPI whose grain is not ``overall`` materialises one row per dimension value each night. A
tile resolves that with its own ``grain_value``; a KPI-level read has no tile, so it follows
one ``dimension`` (the caller's, when a row matches it, else the first the latest night
carried) and lists every ``dimensions`` value seen that night so a screen can offer the rest.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, cast

from egpt.workflow.types import StepInput

from egpt_retail.alerts.condition import as_decimal
from egpt_retail.api.models import (
    KpiHealthStatus,
    KpiSeries,
    KpiSeriesInterval,
    SeriesPoint,
)
from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.executors.gold import grain_key_columns, read_gold

__all__ = [
    "DEFAULT_SERIES_DAYS",
    "MAX_DIMENSIONS",
    "end_of_business_day",
    "history_by_date",
    "read_kpi_series",
    "series_from_gold",
    "status_for_missing",
]

#: Business dates read back from the latest one, when the caller does not say. The same
#: window a dashboard tile plots (``dashboards.data.SERIES_LOOKBACK_DAYS``).
DEFAULT_SERIES_DAYS = 30

#: How many distinct dimension values ``KpiSeries.dimensions`` lists -- a chip row, not a
#: listing; a KPI with more has a ``table`` tile for that.
MAX_DIMENSIONS = 25


def end_of_business_day(value: Optional[str]) -> Optional[datetime]:
    """``read_gold``'s ``as_of`` is a bare business date (``date.isoformat()``, no time or
    zone); the contract's ``as_of`` fields are strict aware datetimes. A business date is
    stamped at its end (``"...T23:59:59Z"``), the convention gold's own freshness stamps and
    the contract's worked examples use -- midnight would read as a different moment."""
    if value is None:
        return None
    return datetime.combine(
        date.fromisoformat(value), time(23, 59, 59), tzinfo=timezone.utc
    )


def status_for_missing(reason: str) -> KpiHealthStatus:
    """``read_gold``'s ``missing`` reason, mapped honestly: a KPI that has never been
    materialised is ``never_computed``; a guard refusal or a warehouse error is ``failed``."""
    if "guard refused" in reason:
        return KpiHealthStatus.failed
    if "never materialised" in reason or "no gold row" in reason:
        return KpiHealthStatus.never_computed
    return KpiHealthStatus.failed


def history_by_date(
    history: Sequence[Mapping[str, Any]],
) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """``read_gold``'s flat ``history`` rows grouped by business date, oldest first. The rows
    arrive ordered by ``as_of`` already (the statement orders by the whole row, and ``as_of``
    is its first column); sorting here only makes that independent of the reader."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in history:
        grouped.setdefault(str(row["as_of"]), []).append(dict(row))
    return sorted(grouped.items())


def _dimension_of(row: Mapping[str, Any], keys: Sequence[str]) -> Dict[str, Any]:
    return {key: row.get(key) for key in keys}


def _matches(row: Mapping[str, Any], dimension: Mapping[str, Any]) -> bool:
    """Loose on type (``str`` on both sides): a query-string dimension arrives as text and a
    gold view returns whatever the formula's column type was."""
    return all(str(row.get(key)) == str(value) for key, value in dimension.items())


def _row_for(
    rows: Sequence[Mapping[str, Any]], dimension: Optional[Mapping[str, Any]]
) -> Optional[Mapping[str, Any]]:
    """The one row of a business date the series follows: the row matching ``dimension``, or
    the only row when the KPI has no dimension. ``None`` when nothing resolves -- the same
    rule for the latest date and for every date in the history, so the series is made of the
    same row a number tile would show that day."""
    if dimension:
        for row in rows:
            if _matches(row, dimension):
                return row
        return None
    if len(rows) == 1:
        return rows[0]
    return None


def _empty(
    kpi: KpiDefinition,
    *,
    days: int,
    status: KpiHealthStatus,
    dimension: Optional[Mapping[str, Any]],
) -> KpiSeries:
    return KpiSeries(
        kpi_identifier=kpi.identifier,
        grain=kpi.grain,
        interval=KpiSeriesInterval.day,
        days=days,
        unit=kpi.unit or "",
        dimension=dict(dimension) if dimension else None,
        dimensions=[],
        points=[],
        value=None,
        delta=None,
        as_of=None,
        computed_at=None,
        status=status,
        provenance_ref=None,
    )


def series_from_gold(
    kpi: KpiDefinition,
    result: Optional[Mapping[str, Any]],
    missing_reason: str = "",
    *,
    days: int = DEFAULT_SERIES_DAYS,
    dimension: Optional[Mapping[str, Any]] = None,
) -> KpiSeries:
    """The wire series from one KPI's ``read_gold`` entry (``result``: its ``rows`` at the
    latest date plus its ``history``), or from the reason it was missing. Pure, so the whole
    date and dimension rule is testable without a warehouse."""
    if result is None:
        return _empty(
            kpi,
            days=days,
            status=status_for_missing(missing_reason),
            dimension=dimension,
        )

    rows: Sequence[Mapping[str, Any]] = result.get("rows") or ()
    keys = grain_key_columns(rows)
    dimensions: List[Dict[str, Any]] = []
    if keys:
        for row in rows:
            candidate = _dimension_of(row, keys)
            if candidate not in dimensions:
                dimensions.append(candidate)
            if len(dimensions) == MAX_DIMENSIONS:
                break

    followed: Optional[Dict[str, Any]] = None
    if keys:
        # Restricted to the real grain keys before matching: an unrecognised key in the
        # caller's `dimension` (e.g. `run_id`, a bookkeeping column, not a grain key at all)
        # must not participate in row selection, even inertly -- ignoring it here means it is
        # simply dropped rather than silently compared against a column it was never meant to
        # name. An empty result after restricting is the same as no dimension having been
        # given at all.
        restricted = (
            {key: value for key, value in dimension.items() if key in keys}
            if dimension
            else None
        )
        matched = _row_for(rows, restricted) if restricted else None
        followed = (
            _dimension_of(matched, keys)
            if matched is not None
            else (dimensions[0] if dimensions else None)
        )

    # `(business date, raw value)` for every date the followed row resolved on, oldest first.
    # The latest date is normally the last history entry (`read_gold` reads `(as_of - days,
    # as_of]`); when the history read failed it is appended from `rows`, so the series always
    # carries at least the point the number tile would show.
    resolved: List[Tuple[str, Optional[str]]] = []
    for as_of, dated_rows in history_by_date(result.get("history") or ()):
        dated = _row_for(dated_rows, followed)
        if dated is None:
            continue
        raw = dated.get("value")
        resolved.append((as_of, None if raw is None else str(raw)))
    latest_as_of = str(result.get("as_of"))
    if not any(as_of == latest_as_of for as_of, _ in resolved):
        current = _row_for(rows, followed)
        if current is not None:
            raw = current.get("value")
            resolved.append((latest_as_of, None if raw is None else str(raw)))
            resolved.sort()

    if not resolved:
        # Rows exist but none is the followed dimension's (a `grain_value`-less multi-row
        # KPI with no dimension resolvable): the KPI has values, just not one line of them.
        empty = _empty(
            kpi, days=days, status=KpiHealthStatus.never_computed, dimension=followed
        )
        return empty.model_copy(
            update={
                "dimensions": dimensions,
                "as_of": end_of_business_day(result.get("as_of")),
                "computed_at": result.get("computed_at"),
                "provenance_ref": result.get("run_id"),
            }
        )

    points = [
        SeriesPoint(
            at=end_of_business_day(as_of),  # type: ignore[arg-type]
            value=None if raw is None else float(raw),
        )
        for as_of, raw in resolved
    ]
    latest_raw = resolved[-1][1]
    value = None if latest_raw is None else float(latest_raw)
    delta: Optional[float] = None
    if len(resolved) >= 2 and latest_raw is not None:
        previous_raw = resolved[-2][1]
        if previous_raw is not None:
            delta = float(as_decimal(latest_raw) - as_decimal(previous_raw))

    return KpiSeries(
        kpi_identifier=kpi.identifier,
        grain=kpi.grain,
        interval=KpiSeriesInterval.day,
        days=days,
        unit=kpi.unit or "",
        dimension=followed,
        dimensions=dimensions,
        points=points,
        value=value,
        delta=delta,
        as_of=end_of_business_day(result.get("as_of")),
        computed_at=result.get("computed_at"),
        status=KpiHealthStatus.fresh,
        provenance_ref=result.get("run_id"),
    )


async def read_kpi_series(
    kpi: KpiDefinition,
    *,
    days: int = DEFAULT_SERIES_DAYS,
    dimension: Optional[Mapping[str, Any]] = None,
) -> KpiSeries:
    """One KPI's series from gold: one ``read_gold`` call with ``history_days=days`` for this
    identifier alone, then :func:`series_from_gold`. ``async`` only, like
    ``dashboards.data.read_dashboard_data``: ``read_gold`` is an ``async def`` executor."""
    step_input = StepInput(
        additional_data={
            "kpi_identifiers": [kpi.identifier],
            "history_days": days,
            "history_for": [kpi.identifier],
        }
    )
    output = await read_gold(step_input)
    # `StepOutput.content`'s declared type is a broad union because other executors return
    # other shapes; `read_gold` always returns a dict, so this is a narrowing, not a guess.
    content = cast(Mapping[str, Any], output.content or {})
    values: Mapping[str, Mapping[str, Any]] = content.get("kpis", {})
    missing_reasons = {
        entry["kpi_identifier"]: entry.get("reason", "")
        for entry in content.get("missing", [])
    }
    return series_from_gold(
        kpi,
        values.get(kpi.identifier),
        missing_reasons.get(kpi.identifier, ""),
        days=days,
        dimension=dimension,
    )
