"""The diagnostic kind's one new stage (K25): ``decompose_change``.

Between *Compare to baseline* and *Explain*, this step answers "where does the change sit?"
by arithmetic, so the model that follows narrates a ranking rather than choosing a cause:

1. the KPI's formula is read into its parts (``diagnostics.parts``): table, window, and
   numerator / denominator;
2. the fact's rows are grouped under those parts by every candidate dimension -- the
   low-cardinality text columns the table carries that the formula does not itself read --
   for the run's date, the baseline date, and the trailing history;
3. ``diagnostics.decompose`` gives each slice its contribution (rate and mix effects for a
   ratio), its surprise against its own history, and the support floor; dimensions are
   ranked; the winning slice is searched one level down (its intersections with the other
   dimensions);
4. the evidence rows behind the winner are fetched -- for a rate, the losing side, with
   any reason column tallied.

Every statement goes through the warehouse read guard, and every table read is noted for
provenance. The output carries the comparison forward untouched and adds ``diagnosis``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from egpt.exceptions import SQLGuardRefusal
from egpt.workflow.types import StepInput, StepOutput
from sqlalchemy import text
from sqlalchemy.engine import Engine

from egpt_retail.app.db import build_warehouse_engine, build_warehouse_readonly_engine
from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.app.sql_guard import build_warehouse_read_guard
from egpt_retail.diagnostics.decompose import (
    SENSITIVITY,
    DimensionResult,
    Driver,
    decompose_dimension,
    rank_dimensions,
    support_floor,
)
from egpt_retail.diagnostics.parts import FormulaParts, FormulaShapeError, parts_of
from egpt_retail.executors.analytics import comparison_of
from egpt_retail.executors.batch import slot
from egpt_retail.executors.provenance import note_dataset_read, note_sql_execution

__all__ = [
    "candidate_dimensions",
    "decompose_change",
    "diagnosis_of",
    "diagnosis_options",
]

log = logging.getLogger("egpt_retail.executors.diagnostics")

HISTORY_PERIODS = 12
MAX_CARDINALITY = 60
EVIDENCE_ROWS = 50
#: Columns that are never a cause: identifiers, free text, dates, the outcome itself and
#: its explanations (a status, a stage, a reason describe the result, not where it sits).
_NEVER_A_DIMENSION = re.compile(
    r"(^|_)id$|_id$|reason|status|stage|outcome|result|source_system|month$|date$|_at$|"
    r"_flag$|comment|note|email|phone|address|description|url$|sku$|^linked_",
    re.IGNORECASE,
)
_REASON = re.compile(r"reason", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z_][\w]*")


def _options(step_input: StepInput) -> Mapping[str, Any]:
    return step_input.additional_data or {}


def _as_of(options: Mapping[str, Any]) -> date:
    raw = options.get("as_of")
    return date.fromisoformat(str(raw)) if raw else date.today()


def _number(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _definition(
    identifier: str, options: Mapping[str, Any]
) -> Optional[Dict[str, Any]]:
    """Name, unit, formula and window for the KPI: injected, else the registry."""
    injected = options.get("kpi_definitions")
    if isinstance(injected, Mapping) and identifier in injected:
        return dict(injected[identifier])
    try:
        from egpt_retail.app.db import pooled_control_plane_connection
        from egpt_retail.kpi.store import KpiRegistryStore

        connection = pooled_control_plane_connection(RetailSettings())
        try:
            row = KpiRegistryStore(connection).get(identifier)
        finally:
            connection.close()
    except Exception:  # pragma: no cover - no control plane
        return None
    if row is None:
        return None
    window = getattr(row, "time_window", None)
    days = None
    if window is not None:
        unit = str(getattr(window, "unit", "day")).lower()
        length = int(getattr(window, "length", 0) or 0)
        days = length * {"day": 1, "week": 7, "month": 30, "quarter": 90}.get(unit, 1)
    return {
        "identifier": identifier,
        "name": row.name,
        "unit": getattr(row, "unit", "") or "",
        "sql": row.sql,
        "window_days": days,
        "source_tables": list(getattr(row, "source_tables", ()) or ()),
    }


def _engine(settings: RetailSettings) -> Engine:
    if settings.retail_warehouse_readonly_db_url:
        return build_warehouse_readonly_engine(settings)
    log.warning(
        "decompose_change: reading through the warehouse role (no read-only URL)"
    )
    return build_warehouse_engine(settings)


def _rows(
    engine: Engine, statement: str, params: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    with engine.begin() as connection:
        return [
            dict(r)
            for r in connection.execute(text(statement), dict(params)).mappings()
        ]


def _bind(where: str) -> str:
    return where.replace("{as_of}", ":as_of")


def _formula_words(parts: FormulaParts) -> set:
    return {w.lower() for w in _WORD.findall(f"{parts.numerator} {parts.where}")}


def candidate_dimensions(
    reader: Any, engine: Engine, guard: Any, parts: FormulaParts, step_input: StepInput
) -> List[Dict[str, Any]]:
    """The columns of the fact a change can be searched across: text columns with between
    2 and MAX_CARDINALITY distinct values, not read by the formula itself, not an
    identifier, a date, a reason or free text. Each with its distinct count and the
    semantic layer's description, so the form can show what it offers."""
    bare = parts.table.split(".")[-1]
    table = reader.get_table(bare)
    if table is None:
        return []
    used = _formula_words(parts)
    out: List[Dict[str, Any]] = []
    for column in table.columns:
        name = str(column.name)
        kind = str(column.data_type or "").lower()
        if not any(t in kind for t in ("text", "char", "varchar", "string")):
            continue
        if name.lower() in used or name == parts.grain_column:
            continue
        if _NEVER_A_DIMENSION.search(name):
            continue
        statement = f"SELECT COUNT(DISTINCT {name}) AS n FROM {parts.table}"
        try:
            guard.check(statement)
            n = int(_rows(engine, statement, {})[0]["n"] or 0)
        except (
            SQLGuardRefusal,
            Exception,
        ):  # a column the guard or the warehouse refuses
            continue
        if 2 <= n <= MAX_CARDINALITY:
            out.append(
                {
                    "column": name,
                    "label": name.replace("_", " "),
                    "distinct": n,
                    "description": str(getattr(column, "description", "") or ""),
                }
            )
    return out


def _grouped(
    engine: Engine,
    guard: Any,
    parts: FormulaParts,
    dimension: str,
    when: date,
    *,
    extra: str = "",
    extra_params: Optional[Mapping[str, Any]] = None,
    step_input: Optional[StepInput] = None,
) -> Dict[Any, Tuple[float, Optional[float], int]]:
    den = f", {parts.denominator} AS denominator" if parts.denominator else ""
    statement = (
        f"SELECT {dimension} AS slice, {parts.numerator} AS numerator{den}, COUNT(*) AS rows_n "
        f"FROM {parts.table} WHERE {_bind(parts.where)}{extra} GROUP BY {dimension}"
    )
    guard.check(statement)
    rows = _rows(engine, statement, {"as_of": when.isoformat(), **(extra_params or {})})
    if step_input is not None:
        note_sql_execution(step_input, statement=statement, row_count=len(rows))
    return {
        r["slice"]: (
            float(r["numerator"] or 0.0),
            float(r["denominator"])
            if parts.denominator and r.get("denominator") is not None
            else None,
            int(r["rows_n"] or 0),
        )
        for r in rows
    }


def _value(parts: FormulaParts, num: float, den: Optional[float]) -> Optional[float]:
    if parts.kind == "ratio":
        return (num / den) * parts.scale if den else None
    return num


def _dimension_result(
    engine: Engine,
    guard: Any,
    parts: FormulaParts,
    dimension: str,
    dates: Sequence[date],
    *,
    floor: int,
    z_bar: float,
    extra: str = "",
    extra_params: Optional[Mapping[str, Any]] = None,
    step_input: Optional[StepInput] = None,
) -> Tuple[DimensionResult, Dict[str, Any]]:
    """Decompose one dimension over ``dates`` = [as_of, baseline, history...]."""
    series = [
        _grouped(
            engine,
            guard,
            parts,
            dimension,
            when,
            extra=extra,
            extra_params=extra_params,
            step_input=step_input,
        )
        for when in dates
    ]
    current, baseline = series[0], series[1]
    history: Dict[Any, List[float]] = {}
    for grouped in series[1:]:
        for key, (num, den, _) in grouped.items():
            value = _value(parts, num, den)
            if value is not None:
                history.setdefault(key, []).append(value)
    # Support is the slice's row count in the period; the maths takes (numerator, denominator).
    cur = {
        k: (v[0], v[1] if parts.kind == "ratio" else None) for k, v in current.items()
    }
    base = {
        k: (v[0], v[1] if parts.kind == "ratio" else None) for k, v in baseline.items()
    }
    result = decompose_dimension(
        dimension,
        parts.kind,
        cur,
        base,
        history,
        scale=parts.scale,
        floor=floor,
        z_bar=z_bar,
    )
    # Additive support is the row count, not the sum: patch it in from the grouped rows.
    if parts.kind != "ratio":
        counts = {
            (str(k) if k is not None else "(blank)"): v[2] for k, v in current.items()
        }
        kept: List[Driver] = []
        for driver in result.drivers:
            driver.support = counts.get(driver.value, driver.support)
            if driver.support >= floor:
                kept.append(driver)
            else:
                result.below_support.append((driver.value, driver.support))
        result.drivers = sorted(kept, key=lambda d: -abs(d.contribution))
    totals = {
        "current": (
            sum(v[0] for v in current.values()),
            sum(v[1] or 0 for v in current.values()),
            sum(v[2] for v in current.values()),
        ),
        "baseline": (
            sum(v[0] for v in baseline.values()),
            sum(v[1] or 0 for v in baseline.values()),
            sum(v[2] for v in baseline.values()),
        ),
        "history": [
            _value(
                parts,
                sum(v[0] for v in g.values()),
                sum(v[1] or 0 for v in g.values()) or None,
            )
            for g in series[1:]
        ],
    }
    return result, totals


def _evidence(
    engine: Engine,
    guard: Any,
    parts: FormulaParts,
    reader: Any,
    dimension: str,
    value: str,
    when: date,
    columns: Sequence[str],
    step_input: StepInput,
) -> Dict[str, Any]:
    """The rows behind the winning slice: the losing side for a filtered-count ratio, else
    the slice's rows; any reason column tallied over the same rows."""
    losing = (
        f" AND NOT ({parts.numerator_predicate})" if parts.numerator_predicate else ""
    )
    slice_pred = (
        f" AND {dimension} = :slice"
        if value != "(blank)"
        else f" AND {dimension} IS NULL"
    )
    params: Dict[str, Any] = {"as_of": when.isoformat()}
    if value != "(blank)":
        params["slice"] = value
    bare = parts.table.split(".")[-1]
    table = reader.get_table(bare)
    names = [str(c.name) for c in (table.columns if table else [])]
    wanted = [c for c in columns if c in names] or _default_evidence_columns(
        names, dimension, parts
    )
    order = f" ORDER BY {parts.measure_column} DESC" if parts.measure_column else ""
    statement = (
        f"SELECT {', '.join(wanted)} FROM {parts.table} WHERE {_bind(parts.where)}"
        f"{slice_pred}{losing}{order} LIMIT {EVIDENCE_ROWS}"
    )
    guard.check(statement)
    rows = _rows(engine, statement, params)
    note_sql_execution(step_input, statement=statement, row_count=len(rows))
    count_statement = f"SELECT COUNT(*) AS n FROM {parts.table} WHERE {_bind(parts.where)}{slice_pred}{losing}"
    guard.check(count_statement)
    count = int(_rows(engine, count_statement, params)[0]["n"] or 0)
    tallies: Dict[str, List[Dict[str, Any]]] = {}
    for column in names:
        if not _REASON.search(column):
            continue
        tally = (
            f"SELECT {column} AS value, COUNT(*) AS n FROM {parts.table} WHERE {_bind(parts.where)}"
            f"{slice_pred}{losing} AND {column} IS NOT NULL GROUP BY {column} ORDER BY n DESC LIMIT 8"
        )
        guard.check(tally)
        tallies[column] = [
            {"value": str(r["value"]), "count": int(r["n"])}
            for r in _rows(engine, tally, params)
        ]
    return {
        "side": "losing" if losing else "rows",
        "columns": wanted,
        "rows": [
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}
            for r in rows
        ],
        "count": count,
        "tallies": tallies,
    }


#: What a reader needs to recognise a row, in the order they read it: the identifier,
#: the dimension the finding sits in, who and what, when it closed, the measure, why it
#: was lost, its outcome. One or two per tier, eight at most.
_EVIDENCE_TIERS = (
    (re.compile(r"(^|_)(id|number|reference|ref)$", re.IGNORECASE), 1),
    (re.compile(r"(customer|account|client|company).*name$", re.IGNORECASE), 1),
    (re.compile(r"(product|item|sku).*name$|^description$", re.IGNORECASE), 1),
    (re.compile(r"(close|closed|end|completed|won|lost).*date$", re.IGNORECASE), 1),
    (re.compile(r"date$", re.IGNORECASE), 1),
    (re.compile(r"amount|value|price|revenue|total", re.IGNORECASE), 1),
    (re.compile(r"reason", re.IGNORECASE), 1),
    (re.compile(r"stage|status|outcome", re.IGNORECASE), 1),
)


def _default_evidence_columns(
    names: Sequence[str], dimension: str, parts: FormulaParts
) -> List[str]:
    """The columns a reader needs to recognise a row without being asked, at most eight."""
    keep: List[str] = []
    for pattern, limit in _EVIDENCE_TIERS:
        # The generic date tier only when the closing-date tier found nothing.
        if pattern.pattern == r"date$" and any(k.endswith("date") for k in keep):
            continue
        hits = [
            n for n in names if pattern.search(n) and n not in keep and n != dimension
        ]
        if pattern.pattern.startswith("(customer") and parts.measure_column in hits:
            hits.remove(parts.measure_column)
        keep += hits[:limit]
        if len(keep) == 1 and dimension in names and dimension not in keep:
            keep.append(dimension)
    if (
        parts.measure_column
        and parts.measure_column in names
        and parts.measure_column not in keep
    ):
        keep.append(parts.measure_column)
    if dimension in names and dimension not in keep:
        keep.insert(1 if keep else 0, dimension)
    if not keep:
        keep = list(names)
    return keep[:8]


def _serialise(result: DimensionResult) -> Dict[str, Any]:
    return {
        "dimension": result.dimension,
        "power": round(result.power, 4),
        "unusual": result.unusual,
        "drivers": [d.as_dict() for d in result.drivers[:3]],
        "below_support": [
            {"value": v, "support": n} for v, n in result.below_support[:6]
        ],
    }


async def decompose_change(step_input: StepInput) -> StepOutput:
    """Where the KPI's change sits: the ranked drivers, their intersections, the evidence.

    Slots: ``metrics`` (the KPI, first of the list), ``comparison`` (``prior_period`` or
    ``prior_year``; a diagnostic without one compares with the period before), ``window``
    (days between the two periods; else the KPI's own), ``dimensions`` (columns to search
    across; empty means every candidate), ``sensitivity`` (``clear`` / ``usual`` / ``more``),
    ``evidence columns``. ``additional_data``: ``as_of``, ``history_periods``.
    """
    options = _options(step_input)
    as_of = _as_of(options)
    identifiers = [
        str(i)
        for i in (slot(step_input, "metrics") or options.get("kpi_identifiers") or [])
    ]
    if not identifiers:
        return StepOutput(
            content={"refused": "no KPI to diagnose"}, success=False, stop=True
        )
    identifier = identifiers[0]
    definition = _definition(identifier, options)
    if definition is None or not definition.get("sql"):
        return StepOutput(
            content={"refused": f"KPI {identifier!r} is not in the registry"},
            success=False,
            stop=True,
        )
    try:
        parts = parts_of(str(definition["sql"]))
    except FormulaShapeError as exc:
        return StepOutput(
            content={"refused": f"KPI {identifier!r} cannot be diagnosed: {exc}"},
            success=False,
            stop=True,
        )

    comparison = str(slot(step_input, "comparison") or "prior_period").strip().lower()
    chosen_window = _number(slot(step_input, "window"))
    step_days = int(chosen_window or definition.get("window_days") or 7)
    baseline_as_of = as_of - timedelta(
        days=365 if comparison == "prior_year" else step_days
    )
    periods = int(options.get("history_periods") or HISTORY_PERIODS)
    history_dates = [
        as_of - timedelta(days=step_days * k) for k in range(1, periods + 1)
    ]
    dates = [as_of, baseline_as_of] + [d for d in history_dates if d != baseline_as_of]
    sensitivity = str(slot(step_input, "sensitivity") or "usual").strip().lower()
    if sensitivity not in SENSITIVITY:
        sensitivity = "usual"
    z_bar = SENSITIVITY[sensitivity]["z"]
    raw_dims = slot(step_input, "dimensions")
    wanted_dims = (
        [str(d) for d in raw_dims if str(d).strip()]
        if isinstance(raw_dims, list)
        else []
    )

    evidence_columns = [str(c) for c in (slot(step_input, "evidence columns") or [])]

    settings = RetailSettings()
    engine = _engine(settings)

    def work() -> Dict[str, Any]:
        with open_semantic_reader(settings) as reader:
            guard = build_warehouse_read_guard(
                reader, kpi_identifiers=[identifier], row_cap=1000
            )
            candidates = candidate_dimensions(reader, engine, guard, parts, step_input)
            names = [c["column"] for c in candidates]
            dims = [d for d in wanted_dims if d in names] or names
            note_dataset_read(
                step_input, table=parts.table.split(".")[-1], as_of=as_of.isoformat()
            )
            if not dims:
                return {
                    "refused": "the fact carries no dimension a change can be searched across"
                }

            results: List[DimensionResult] = []
            totals: Dict[str, Any] = {}
            # The support floor needs the period's row count: read once from the first
            # dimension's totals, then decompose every dimension with it.
            first, totals = _dimension_result(
                engine,
                guard,
                parts,
                dims[0],
                dates,
                floor=0,
                z_bar=z_bar,
                step_input=step_input,
            )
            rows_in_period = int(totals["current"][2])
            floor = support_floor(rows_in_period, sensitivity=sensitivity)
            for dim in dims:
                result, _ = _dimension_result(
                    engine,
                    guard,
                    parts,
                    dim,
                    dates,
                    floor=floor,
                    z_bar=z_bar,
                    step_input=step_input,
                )
                results.append(result)
            ordered, ties, by_surprise = rank_dimensions(results, z_bar=z_bar)

            cur_n, cur_d, _ = totals["current"]
            base_n, base_d, base_rows = totals["baseline"]
            current_value = _value(
                parts, cur_n, cur_d if parts.kind == "ratio" else None
            )
            baseline_value = _value(
                parts, base_n, base_d if parts.kind == "ratio" else None
            )
            delta = (
                (current_value - baseline_value)
                if current_value is not None and baseline_value is not None
                else None
            )
            # The headline is rounded to the formula's own precision -- what gold stores and
            # the KPI tile shows -- and its delta is the difference of the rounded values,
            # so the sentence, the tile and the diagnosis quote one number. The shares of
            # the gap stay exact underneath (they sum to the true change).
            places = parts.precision
            shown_current = (
                round(current_value, places) if current_value is not None else None
            )
            shown_baseline = (
                round(baseline_value, places) if baseline_value is not None else None
            )
            shown_delta = (
                round(shown_current - shown_baseline, places)
                if shown_current is not None and shown_baseline is not None
                else None
            )
            headline = {
                "kpi_identifier": identifier,
                "name": definition.get("name") or identifier,
                "unit": definition.get("unit") or "",
                "kind": parts.kind,
                "as_of": as_of.isoformat(),
                "baseline_as_of": baseline_as_of.isoformat(),
                "step_days": step_days,
                "current": shown_current,
                "baseline": shown_baseline,
                "delta": shown_delta,
                "delta_pct": round(100 * shown_delta / shown_baseline, 1)
                if shown_delta is not None and shown_baseline
                else None,
                "exact_delta": round(delta, 4) if delta is not None else None,
                "rows_in_period": rows_in_period,
                "rows_in_baseline": int(base_rows),
                "history": [
                    round(v, places) for v in totals["history"] if v is not None
                ],
            }

            winner = ordered[0].drivers[0] if ordered and ordered[0].drivers else None
            intersections: List[Dict[str, Any]] = []
            evidence: Optional[Dict[str, Any]] = None
            if winner is not None:
                extra = (
                    f" AND {winner.dimension} = :slice"
                    if winner.value != "(blank)"
                    else f" AND {winner.dimension} IS NULL"
                )
                extra_params = (
                    {"slice": winner.value} if winner.value != "(blank)" else {}
                )
                inner: List[DimensionResult] = []
                for dim in dims:
                    if dim == winner.dimension:
                        continue
                    result, _ = _dimension_result(
                        engine,
                        guard,
                        parts,
                        dim,
                        dates,
                        floor=floor,
                        z_bar=z_bar,
                        extra=extra,
                        extra_params=extra_params,
                        step_input=step_input,
                    )
                    inner.append(result)
                inner_ordered, _, _ = rank_dimensions(inner, z_bar=z_bar)
                intersections = [_serialise(r) for r in inner_ordered[:3] if r.drivers]
                evidence = _evidence(
                    engine,
                    guard,
                    parts,
                    reader,
                    winner.dimension,
                    winner.value,
                    as_of,
                    evidence_columns,
                    step_input,
                )

            caveats: List[str] = []
            if delta is None:
                caveats.append(
                    "one of the two periods has no value; nothing to decompose"
                )
            elif not by_surprise:
                caveats.append(
                    "no slice is unusual against its own history at this sensitivity; the ranking is by size of contribution alone"
                )
            if ties:
                caveats.append(f"tied within a tenth: {', '.join(ties)}")
            dropped = sum(len(r.below_support) for r in results)
            if dropped:
                caveats.append(
                    f"{dropped} slice(s) below the support floor of {floor} rows were not named"
                )
            return {
                "headline": headline,
                "sensitivity": sensitivity,
                "z_bar": z_bar,
                "support_floor": floor,
                "history_periods": periods,
                "dimensions_searched": dims,
                "candidates": candidates,
                "ranking": [_serialise(r) for r in ordered],
                "winner": winner.as_dict() if winner else None,
                "intersections": intersections,
                "evidence": evidence,
                "confidence": {
                    "share_explained_by_top": round(winner.share_of_gap, 4)
                    if winner and winner.share_of_gap is not None
                    else None,
                    "by_surprise": by_surprise,
                    "ties": ties,
                },
                "caveats": caveats,
            }

    try:
        diagnosis = await asyncio.to_thread(work)
    except SQLGuardRefusal as exc:
        return StepOutput(
            content={"refused": f"guard refused: {exc.message}"},
            success=False,
            stop=True,
        )
    finally:
        engine.dispose()
    if "refused" in diagnosis:
        return StepOutput(content=diagnosis, success=False, stop=True)
    carried = comparison_of(step_input)
    return StepOutput(
        content={"comparison_result": carried, "diagnosis": diagnosis},
        success=True,
    )


def diagnosis_of(step_input: StepInput) -> Optional[Dict[str, Any]]:
    """The diagnosis carried from ``decompose_change``, wherever it sits in the chain."""
    previous = step_input.previous_step_content
    if isinstance(previous, Mapping):
        if isinstance(previous.get("diagnosis"), Mapping):
            return dict(previous["diagnosis"])
        for key in ("explanation", "comparison_result"):
            nested = previous.get(key)
            if isinstance(nested, Mapping) and isinstance(
                nested.get("diagnosis"), Mapping
            ):
                return dict(nested["diagnosis"])
    for output in reversed(list(step_input.previous_step_outputs or [])):
        content = getattr(output, "content", None)
        if isinstance(content, Mapping) and isinstance(
            content.get("diagnosis"), Mapping
        ):
            return dict(content["diagnosis"])
    return None


def diagnosis_options(
    identifier: str, *, as_of: Optional[date] = None, window_days: Optional[int] = None
) -> Dict[str, Any]:
    """What the form shows for a diagnostic ask on this KPI (no model, no run): whether the
    formula can be decomposed and as what, the candidate dimensions with their distinct
    counts, the rows per period and the support floor each sensitivity implies."""
    definition = _definition(identifier, {})
    if definition is None or not definition.get("sql"):
        return {
            "kpi_identifier": identifier,
            "diagnosable": False,
            "reason": "not in the registry",
        }
    try:
        parts = parts_of(str(definition["sql"]))
    except FormulaShapeError as exc:
        return {"kpi_identifier": identifier, "diagnosable": False, "reason": str(exc)}
    settings = RetailSettings()
    engine = _engine(settings)
    when = as_of or date.today()
    step_days = int(window_days or definition.get("window_days") or 7)
    try:
        with open_semantic_reader(settings) as reader:
            guard = build_warehouse_read_guard(
                reader, kpi_identifiers=[identifier], row_cap=1000
            )
            probe = StepInput(additional_data={})
            candidates = candidate_dimensions(reader, engine, guard, parts, probe)
            count = (
                f"SELECT COUNT(*) AS n FROM {parts.table} WHERE {_bind(parts.where)}"
            )
            guard.check(count)
            rows_now = int(
                _rows(engine, count, {"as_of": when.isoformat()})[0]["n"] or 0
            )
            rows_then = int(
                _rows(
                    engine,
                    count,
                    {"as_of": (when - timedelta(days=step_days)).isoformat()},
                )[0]["n"]
                or 0
            )
            bare = parts.table.split(".")[-1]
            table = reader.get_table(bare)
            columns = [
                {
                    "column": str(c.name),
                    "description": str(getattr(c, "description", "") or ""),
                }
                for c in (table.columns if table else [])
            ]
    finally:
        engine.dispose()
    return {
        "kpi_identifier": identifier,
        "diagnosable": True,
        "kind": parts.kind,
        "numerator": parts.numerator,
        "denominator": parts.denominator,
        "table": parts.table,
        "window_days": definition.get("window_days"),
        "step_days": step_days,
        "as_of": when.isoformat(),
        "rows_in_period": rows_now,
        "rows_in_baseline": rows_then,
        "history_periods": HISTORY_PERIODS,
        "support_floors": {
            k: support_floor(rows_now, sensitivity=k) for k in SENSITIVITY
        },
        "dimensions": candidates,
        "columns": columns,
        "reason_columns": [c["column"] for c in columns if _REASON.search(c["column"])],
    }
