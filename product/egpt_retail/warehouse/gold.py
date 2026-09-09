"""Gold materialisation: a registry formula, executed in the warehouse, as rows with a stamp.

R5 (P3, after K11; implements D6 and D7). The registry (``semantic.kpi_definition``, control
plane) holds the *definitions*; this module turns one into rows of ``gold.kpi_value`` inside
the warehouse, so "the product holds definitions and orchestration, not values" (D6) stays
true. Nothing here decides anything a model could be persuaded of: bind, guard, compile, run.

The pass for one KPI, in its own transaction, in this order and for these reasons:

1. **Session setup, before any savepoint.** ``SET LOCAL search_path`` so a formula's bare
   ``FROM crm_deal`` resolves in silver (the reader role has no search_path -- R6's live
   finding), and ``SET LOCAL statement_timeout`` so one runaway formula cannot hold the
   nightly run. Postgres reverts a GUC set inside a subtransaction that is rolled back, so
   these must precede the compile probe's savepoint or a failed probe would silently unset
   them for the execution that follows.
2. **Bind.** ``{scope}`` (``ParameterKind.PREDICATE``) is interpolated from the KPI's concept's
   accepted ``sql_filter`` -- an accepted proposal, never user input -- or ``TRUE`` when the
   concept has none, and that choice is recorded. ``{as_of}`` (``SCALAR``) is rendered as a
   ``:as_of`` driver bind of the run date. Any other placeholder is a refusal, by name: the
   nightly run has no value to give it.
3. **Guard.** The assembled statement goes through R6's guard: reads only, tables on the
   allow-list, no unresolved placeholder. "No SQL runs outside the guard" applies to the
   product's own formulas exactly as it applies to a model's.
4. **Compile.** ``SELECT * FROM (formula) LIMIT 0`` inside a savepoint, reading the cursor
   description: how many columns, their names, the value column's type. ``EXPLAIN`` would not
   reveal result columns. This is where ``column "amount" does not exist`` surfaces -- before
   any write, as ``kpi_run.status = 'failed'`` with Postgres's message verbatim.
5. **Execute and write.** ``INSERT ... SELECT`` from the formula into ``gold.kpi_value`` with
   ``ON CONFLICT DO UPDATE`` on ``(kpi_identifier, as_of, dimension)`` -- a same-night re-run
   overwrites, so the pass is idempotent -- then the ``kpi_run`` row, then the per-KPI view
   projecting the grain keys as columns, which is how the API contract queries gold.

A failure at any step for one KPI never aborts the pass: the transaction rolls back to the
savepoint, the run row records why, and the next KPI starts its own transaction. That is the
delivery plan's evidence bar -- "the broken KPI fails alone while the rest materialise, with
the failure visible in run history rather than only in logs" -- as code.

**Result-column convention** (announced to Lane K as additive to the KPI contract): the last
column a formula returns is the value; any columns before it are the grain keys; a formula at
grain ``overall`` returns exactly one column. Column names are whatever the formula gives them
-- ``sum``, ``count`` -- so the convention is positional for the value and by-name for the keys.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from egpt.exceptions import SQLGuardRefusal
from egpt.guardrails.sql_guard import SQLGuard
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from egpt_retail.contracts.kpi import (
    Feasibility,
    FeasibilityVerdict,
    KpiDefinition,
    ParameterKind,
    ResultShape,
    bound_parameter_names,
)
from egpt_retail.warehouse.schema import GOLD_SCHEMA, SILVER_SCHEMA, gold_view

__all__ = [
    "BoundFormula",
    "FormulaBindError",
    "GRAIN_OVERALL",
    "KpiRunResult",
    "MATERIALISED",
    "FAILED",
    "SKIPPED",
    "RESERVED_AS_OF",
    "RESERVED_SCOPE",
    "ResultColumns",
    "bind_formula",
    "classify_result_columns",
    "formula_sha256",
    "insert_statement",
    "latest_as_of",
    "materialise_kpi",
    "amaterialise_kpi",
    "materialise_registry",
    "amaterialise_registry",
    "probe_statement",
    "read_gold_statement",
    "view_statements",
]

log = logging.getLogger(__name__)

#: ``gold.kpi_run.status`` values; the CHECK constraint in 070_gold.sql lists the same three.
MATERIALISED = "materialised"
FAILED = "failed"
SKIPPED = "skipped"

#: The grain whose formula returns one column and no keys (``ontology/vocabularies.json``).
GRAIN_OVERALL = "overall"

#: The two parameter names materialisation knows how to bind. Reserved: a formula declaring
#: either gets it filled; one declaring anything else is refused, by name, at bind time.
RESERVED_SCOPE = "scope"
RESERVED_AS_OF = "as_of"

#: Postgres type OIDs a value column may have. Anything else known is refused at compile with
#: the type named; an unknown OID is left to the CAST at execution.
_NUMERIC_OIDS = frozenset({20, 21, 23, 700, 701, 1700})
_TYPE_NAMES: Mapping[int, str] = {
    16: "boolean",
    25: "text",
    114: "json",
    1042: "character",
    1043: "character varying",
    1082: "date",
    1083: "time",
    1114: "timestamp",
    1184: "timestamptz",
    1186: "interval",
    2950: "uuid",
    3802: "jsonb",
}


class FormulaBindError(ValueError):
    """The nightly run has nothing to bind a placeholder to. Recorded, never raised past the pass."""


@dataclass(frozen=True)
class BoundFormula:
    """A formula with its predicates interpolated and its scalars as ``:name`` binds."""

    sql: str
    params: Mapping[str, Any]
    #: How each declared parameter was bound, for ``kpi_run.reason``.
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ResultColumns:
    """What the compile probe found, judged against the grain."""

    grain_keys: Tuple[str, ...]
    value_column: str
    error: Optional[str] = None


@dataclass(frozen=True)
class KpiRunResult:
    """One KPI's outcome in one run -- exactly the ``gold.kpi_run`` row, as data."""

    kpi_identifier: str
    run_id: str
    as_of: date
    status: str
    grain: str
    grain_keys: Tuple[str, ...] = ()
    rows_written: int = 0
    error: Optional[str] = None
    reason: Optional[str] = None
    statement: Optional[str] = None
    source_snapshot: str = ""
    formula_sha256: str = ""
    source_tables: Tuple[str, ...] = ()
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = field(default=None)

    @property
    def view(self) -> str:
        return f"{GOLD_SCHEMA}.{gold_view(self.kpi_identifier)}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kpi_identifier": self.kpi_identifier,
            "run_id": self.run_id,
            "as_of": self.as_of.isoformat(),
            "status": self.status,
            "grain": self.grain,
            "grain_keys": list(self.grain_keys),
            "rows_written": self.rows_written,
            "error": self.error,
            "reason": self.reason,
            "statement": self.statement,
            "source_snapshot": self.source_snapshot,
            "view": self.view if self.status == MATERIALISED else None,
        }


# ---------------------------------------------------------------------------
# Pure: binding, the probe, the result-column convention, the statements
# ---------------------------------------------------------------------------


def formula_sha256(kpi: KpiDefinition) -> str:
    """The lineage stamp: the formula *template*, before binding, so two nights with different
    scope filters share a lineage and a changed definition does not."""
    return hashlib.sha256(kpi.sql.encode("utf-8")).hexdigest()


def bind_formula(
    kpi: KpiDefinition, *, as_of: date, scope_filter: Optional[str]
) -> BoundFormula:
    """Interpolate the predicates and render the scalars as driver binds.

    Pure, so the whole binding rule is testable without a warehouse. Refuses -- by raising
    :class:`FormulaBindError`, which the pass records as a failed run -- a parameter it has no
    source for. ``{scope}`` gets the concept's accepted ``sql_filter``, wrapped in parentheses
    so a fragment with ``OR`` cannot rebind the formula's own ``WHERE``; blank means ``TRUE``
    and says so. ``{as_of}`` becomes ``:as_of``, which the guard accepts (sqlglot parses
    ``:name`` as a placeholder expression; psycopg's ``%(name)s`` it would not parse) and the
    driver binds.
    """
    sql = kpi.sql
    params: Dict[str, Any] = {}
    notes: List[str] = []
    for parameter in kpi.parameters:
        marker = "{" + parameter.name + "}"
        if parameter.kind is ParameterKind.PREDICATE:
            if parameter.name != RESERVED_SCOPE:
                raise FormulaBindError(
                    f"{kpi.identifier}: predicate parameter {parameter.name!r} has no source at "
                    f"materialisation; only {{{RESERVED_SCOPE}}} (the concept's accepted "
                    "sql_filter) is bound by the nightly run"
                )
            fragment = (scope_filter or "").strip()
            if fragment:
                sql = sql.replace(marker, f"({fragment})")
                notes.append(f"scope bound to concept filter: {fragment}")
            else:
                sql = sql.replace(marker, "TRUE")
                notes.append(
                    "scope bound to TRUE: the concept carries no accepted sql_filter"
                    if kpi.concept
                    else "scope bound to TRUE: the KPI names no concept"
                )
        else:
            if parameter.name != RESERVED_AS_OF:
                raise FormulaBindError(
                    f"{kpi.identifier}: scalar parameter {parameter.name!r} has no value at "
                    f"materialisation; the nightly run binds only {{{RESERVED_AS_OF}}}"
                )
            sql = sql.replace(marker, f":{RESERVED_AS_OF}")
            params[RESERVED_AS_OF] = as_of
            notes.append(f"as_of bound to {as_of.isoformat()}")
    unresolved = bound_parameter_names(sql)
    if unresolved:
        raise FormulaBindError(
            f"{kpi.identifier}: formula still has unresolved placeholder(s) "
            f"{sorted(unresolved)} after binding"
        )
    return BoundFormula(sql=sql, params=params, notes=tuple(notes))


def probe_statement(bound_sql: str) -> str:
    """``LIMIT 0`` over the formula: compiles and describes, returns nothing."""
    return f"SELECT * FROM ({bound_sql}) AS kpi_probe LIMIT 0"


def classify_result_columns(
    columns: Sequence[Tuple[str, Optional[int]]], grain: str
) -> ResultColumns:
    """Apply the result-column convention to what the probe described.

    ``columns`` is ``(name, type_oid)`` per result column, as a DBAPI ``cursor.description``
    gives them. Pure: the convention is the thing under test, not the database.
    """
    if not columns:
        return ResultColumns((), "", "formula returns no columns")
    *keys, (value_name, value_oid) = columns
    if grain == GRAIN_OVERALL and keys:
        return ResultColumns(
            (),
            value_name,
            f"grain {GRAIN_OVERALL!r} returns exactly one column (the value); this formula "
            f"returns {len(columns)}: {[name for name, _ in columns]}",
        )
    if grain != GRAIN_OVERALL and not keys:
        return ResultColumns(
            (),
            value_name,
            f"grain {grain!r} needs at least one grain-key column before the value; this "
            "formula returns only one column",
        )
    key_names = tuple(name for name, _ in keys)
    bad = [name for name in key_names if not name.isidentifier()]
    if bad:
        return ResultColumns(
            key_names,
            value_name,
            f"grain-key column(s) {bad} are not named; alias every key column in the formula",
        )
    if len(set(key_names)) != len(key_names):
        return ResultColumns(
            key_names,
            value_name,
            f"grain-key columns are not distinct: {list(key_names)}",
        )
    if (
        value_oid is not None
        and value_oid not in _NUMERIC_OIDS
        and value_oid in _TYPE_NAMES
    ):
        return ResultColumns(
            key_names,
            value_name,
            f"value column {value_name!r} has type {_TYPE_NAMES[value_oid]}; a KPI value must "
            "be numeric",
        )
    return ResultColumns(key_names, value_name, None)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def insert_statement(
    kpi_identifier: str, bound_sql: str, columns: ResultColumns
) -> str:
    """``INSERT ... SELECT`` from the formula into ``gold.kpi_value``, idempotent per night.

    Grain keys are cast to text inside the jsonb so a dimension is uniform whatever the key's
    column type -- ``dimension->>'k'`` is text on the way out regardless. The value is cast to
    numeric; a value the probe could not type (an unknown OID) fails here with Postgres's own
    cast error, which the pass records.
    """
    if columns.grain_keys:
        pairs = ", ".join(
            f"'{key}', CAST(q.{_quote(key)} AS text)" for key in columns.grain_keys
        )
        dimension = f"jsonb_build_object({pairs})"
    else:
        dimension = "CAST('{}' AS jsonb)"
    return (
        f"INSERT INTO {GOLD_SCHEMA}.kpi_value "
        "(kpi_identifier, as_of, dimension, value, source_snapshot, source_tables, "
        "formula_sha256, computed_at, run_id) "
        f"SELECT :kpi_identifier, :as_of, {dimension}, CAST(q.{_quote(columns.value_column)} AS numeric), "
        ":source_snapshot, CAST(:source_tables AS text[]), :formula_sha256, :computed_at, :run_id "
        f"FROM ({bound_sql}) AS q "
        "ON CONFLICT (kpi_identifier, as_of, dimension) DO UPDATE SET "
        "value = EXCLUDED.value, source_snapshot = EXCLUDED.source_snapshot, "
        "source_tables = EXCLUDED.source_tables, formula_sha256 = EXCLUDED.formula_sha256, "
        "computed_at = EXCLUDED.computed_at, run_id = EXCLUDED.run_id"
    )


def view_statements(kpi_identifier: str, grain_keys: Sequence[str]) -> Tuple[str, str]:
    """``DROP VIEW IF EXISTS`` then ``CREATE VIEW`` -- ``CREATE OR REPLACE`` cannot drop a
    column, and a KPI whose grain keys changed has exactly that shape."""
    name = f"{GOLD_SCHEMA}.{gold_view(kpi_identifier)}"
    projected = "".join(f"dimension->>'{key}' AS {_quote(key)}, " for key in grain_keys)
    create = (
        f"CREATE VIEW {name} AS SELECT as_of, {projected}value, computed_at, "
        f"source_snapshot, run_id FROM {GOLD_SCHEMA}.kpi_value "
        f"WHERE kpi_identifier = '{kpi_identifier}'"
    )
    return (f"DROP VIEW IF EXISTS {name}", create)


def read_gold_statement(kpi_identifier: str) -> str:
    """The contract's read shape, through the per-KPI view, for one ``as_of``.

    Orders by the whole row (Postgres's default record comparison, field by field in select
    order), not just ``as_of``: the ``WHERE as_of = :as_of`` clause already fixes column 1 to
    one value, so ``ORDER BY 1`` alone leaves a KPI with more than one dimension row per
    ``as_of`` in whatever order the query plan happens to produce -- confirmed to actually
    flip between an index scan and a forced sequential scan of the same data. Ordering by the
    row itself is deterministic regardless of grain: it needs no grain-key names or count, so
    this stays the pure, DB-free function it already was.
    """
    view = gold_view(kpi_identifier)
    return f"SELECT * FROM {GOLD_SCHEMA}.{view} WHERE as_of = :as_of ORDER BY {view}"


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _error_text(exc: BaseException) -> str:
    orig = getattr(exc, "orig", None)
    message = str(orig) if orig is not None else str(exc)
    return message.strip().splitlines()[0] if message.strip() else type(exc).__name__


def _write_run(connection: Connection, result: KpiRunResult) -> None:
    connection.execute(
        text(
            f"INSERT INTO {GOLD_SCHEMA}.kpi_run (kpi_identifier, run_id, as_of, status, grain, "
            "grain_keys, rows_written, error, reason, statement, source_snapshot, "
            "formula_sha256, started_at, finished_at) VALUES (:kpi_identifier, :run_id, "
            ":as_of, :status, :grain, CAST(:grain_keys AS text[]), :rows_written, :error, "
            ":reason, :statement, :source_snapshot, :formula_sha256, :started_at, :finished_at) "
            "ON CONFLICT (kpi_identifier, run_id) DO UPDATE SET status = EXCLUDED.status, "
            "grain_keys = EXCLUDED.grain_keys, rows_written = EXCLUDED.rows_written, "
            "error = EXCLUDED.error, reason = EXCLUDED.reason, statement = EXCLUDED.statement, "
            "finished_at = EXCLUDED.finished_at"
        ),
        {
            "kpi_identifier": result.kpi_identifier,
            "run_id": result.run_id,
            "as_of": result.as_of,
            "status": result.status,
            "grain": result.grain,
            "grain_keys": list(result.grain_keys),
            "rows_written": result.rows_written,
            "error": result.error,
            "reason": result.reason,
            "statement": result.statement,
            "source_snapshot": result.source_snapshot,
            "formula_sha256": result.formula_sha256,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
        },
    )


def record_run(engine: Engine, result: KpiRunResult) -> KpiRunResult:
    """Write one run row on its own transaction -- for outcomes decided before any warehouse
    transaction was opened (a bind or guard refusal, a skip)."""
    with engine.begin() as connection:
        _write_run(connection, result)
    return result


def materialise_kpi(
    engine: Engine,
    kpi: KpiDefinition,
    *,
    as_of: date,
    snapshot_version: str,
    run_id: str,
    guard: SQLGuard,
    scope_filter: Optional[str] = None,
    statement_timeout_ms: int = 60_000,
) -> KpiRunResult:
    """Bind, guard, compile, execute and stamp one KPI. Never raises for a KPI's own failure."""
    started = _now()
    base: Dict[str, Any] = dict(
        kpi_identifier=kpi.identifier,
        run_id=run_id,
        as_of=as_of,
        grain=kpi.grain,
        source_snapshot=snapshot_version,
        formula_sha256=formula_sha256(kpi),
        source_tables=tuple(sorted(kpi.source_tables)),
        started_at=started,
    )

    def failed(
        error: str, *, statement: Optional[str] = None, reason: Optional[str] = None
    ) -> KpiRunResult:
        finished = _now()
        return KpiRunResult(
            status=FAILED,
            error=error,
            statement=statement,
            reason=reason,
            finished_at=finished,
            duration_ms=int((finished - started).total_seconds() * 1000),
            **base,
        )

    # 2. bind
    try:
        bound = bind_formula(kpi, as_of=as_of, scope_filter=scope_filter)
    except FormulaBindError as exc:
        return record_run(engine, failed(str(exc)))
    reason = "; ".join(bound.notes) or None

    # 3. guard
    try:
        guard.check(bound.sql)
    except SQLGuardRefusal as exc:
        return record_run(
            engine,
            failed(
                f"guard refused ({exc.check}): {exc.message}",
                statement=bound.sql,
                reason=reason,
            ),
        )

    with engine.begin() as connection:
        # 1. session setup -- outside the savepoints, see the module docstring.
        connection.exec_driver_sql(f"SET LOCAL search_path = {SILVER_SCHEMA}")
        connection.exec_driver_sql(
            f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}"
        )

        # 4. compile
        try:
            with connection.begin_nested():
                probe = connection.execute(
                    text(probe_statement(bound.sql)), dict(bound.params)
                )
                # DBAPI description tuples: (name, type_code, ...) -- indexed, not attribute
                # access, so the same line holds for psycopg3's Column objects and a plain tuple.
                described = [
                    (str(col[0]), col[1] if isinstance(col[1], int) else None)
                    for col in probe.cursor.description
                ]
                probe.close()
        except Exception as exc:  # the database's own refusal, verbatim
            result = failed(_error_text(exc), statement=bound.sql, reason=reason)
            _write_run(connection, result)
            return result
        columns = classify_result_columns(described, kpi.grain)
        if columns.error is not None:
            result = failed(columns.error, statement=bound.sql, reason=reason)
            _write_run(connection, result)
            return result

        # 5. execute, write, project
        computed_at = _now()
        statement = insert_statement(kpi.identifier, bound.sql, columns)
        try:
            with connection.begin_nested():
                written = connection.execute(
                    text(statement),
                    {
                        **bound.params,
                        "kpi_identifier": kpi.identifier,
                        "as_of": as_of,
                        "source_snapshot": snapshot_version,
                        "source_tables": list(base["source_tables"]),
                        "formula_sha256": base["formula_sha256"],
                        "computed_at": computed_at,
                        "run_id": run_id,
                    },
                )
                rows_written = int(written.rowcount or 0)
                for ddl in view_statements(kpi.identifier, columns.grain_keys):
                    connection.exec_driver_sql(ddl)
        except Exception as exc:
            result = failed(_error_text(exc), statement=bound.sql, reason=reason)
            _write_run(connection, result)
            return result

        finished = _now()
        result = KpiRunResult(
            status=MATERIALISED,
            grain_keys=columns.grain_keys,
            rows_written=rows_written,
            reason=reason,
            statement=bound.sql,
            finished_at=finished,
            duration_ms=int((finished - started).total_seconds() * 1000),
            **base,
        )
        _write_run(connection, result)
    return result


async def amaterialise_kpi(
    engine: Engine, kpi: KpiDefinition, **kwargs: Any
) -> KpiRunResult:
    """:func:`materialise_kpi` off the event loop (the engine is synchronous)."""
    return await asyncio.to_thread(materialise_kpi, engine, kpi, **kwargs)


def skip_reason(
    kpi: KpiDefinition, feasibility: Optional[Feasibility]
) -> Optional[str]:
    """Why this KPI is not materialised tonight, or ``None`` when it is. Pure."""
    if feasibility is not None and feasibility.verdict is not FeasibilityVerdict.READY:
        missing = ", ".join(sorted(feasibility.missing_tables))
        return f"{feasibility.verdict.value}: missing from the semantic snapshot: {missing}"
    if kpi.result_shape is not ResultShape.SCALAR:
        return (
            f"result_shape {kpi.result_shape.value!r}: a listing has no single value to "
            "materialise, cannot be alerted on or trended; read it live instead"
        )
    return None


def materialise_registry(
    engine: Engine,
    kpis: Sequence[KpiDefinition],
    *,
    feasibility: Mapping[str, Feasibility],
    scope_filters: Mapping[str, str],
    as_of: date,
    snapshot_version: str,
    run_id: str,
    guard: SQLGuard,
    statement_timeout_ms: int = 60_000,
) -> Tuple[KpiRunResult, ...]:
    """Every KPI in ``kpis``: skipped with a reason, or materialised on its own.

    ``scope_filters`` maps a concept name to its accepted ``sql_filter`` (the caller reads the
    semantic layer once for the whole pass rather than once per KPI).
    """
    results: List[KpiRunResult] = []
    for kpi in kpis:
        reason = skip_reason(kpi, feasibility.get(kpi.identifier))
        if reason is not None:
            started = _now()
            results.append(
                record_run(
                    engine,
                    KpiRunResult(
                        kpi_identifier=kpi.identifier,
                        run_id=run_id,
                        as_of=as_of,
                        status=SKIPPED,
                        grain=kpi.grain,
                        reason=reason,
                        source_snapshot=snapshot_version,
                        formula_sha256=formula_sha256(kpi),
                        source_tables=tuple(sorted(kpi.source_tables)),
                        started_at=started,
                        finished_at=started,
                        duration_ms=0,
                    ),
                )
            )
            continue
        results.append(
            materialise_kpi(
                engine,
                kpi,
                as_of=as_of,
                snapshot_version=snapshot_version,
                run_id=run_id,
                guard=guard,
                scope_filter=scope_filters.get(kpi.concept or ""),
                statement_timeout_ms=statement_timeout_ms,
            )
        )
        log.info(
            "gold %s: %s (%s)",
            kpi.identifier,
            results[-1].status,
            results[-1].error or results[-1].reason,
        )
    return tuple(results)


async def amaterialise_registry(
    engine: Engine, kpis: Sequence[KpiDefinition], **kwargs: Any
) -> Tuple[KpiRunResult, ...]:
    return await asyncio.to_thread(materialise_registry, engine, kpis, **kwargs)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def latest_as_of(engine: Engine, kpi_identifier: str) -> Optional[date]:
    """The most recent business date this KPI materialised for, or ``None``."""
    with engine.begin() as connection:
        row = connection.execute(
            text(
                f"SELECT max(as_of) FROM {GOLD_SCHEMA}.kpi_run "
                "WHERE kpi_identifier = :kpi_identifier AND status = :status"
            ),
            {"kpi_identifier": kpi_identifier, "status": MATERIALISED},
        ).first()
    return row[0] if row and row[0] is not None else None


async def alatest_as_of(engine: Engine, kpi_identifier: str) -> Optional[date]:
    return await asyncio.to_thread(latest_as_of, engine, kpi_identifier)
