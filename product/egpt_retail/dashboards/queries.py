"""The guarded runner behind ``query`` tiles (contract 1.2.0): check, rewrite, run, cache.

Every statement a dashboard chart carries -- composed by the suggestion agent or typed into
the chart editor -- passes through here before it touches the warehouse, and it passes
through the *same* guard every other retail read does (``app/sql_guard.py``'s
``build_warehouse_read_guard``: one read statement, tables restricted to the semantic layer,
schemas limited to silver and gold). This module adds the three checks a person-facing chart
needs on top of that guard, and makes each of them visible as a verdict rather than silent:

* **Scope**, when the caller narrows to one (``context_tables``, optional): the statement may
  only read tables in that set. Outside it is ``refused`` even when the semantic layer would
  allow it. Meaningful when someone has committed to a set in advance -- the compose agent's
  selected metrics and tables, or a chart editor's schema pane -- not a blanket restriction
  every caller opts into; see ``api/routers/dashboards.py``'s ``_check_query_tiles`` for which
  write paths do and why.
* **Personal data.** A column the semantic layer marks ``is_personal_data`` is dropped from
  the outermost projection (``rewritten``, the detail names it) and refused anywhere else --
  a filter, a join key, a subquery -- because a value that never reaches the result can still
  leak through what it selects. ``SELECT *`` over a table that has such a column is refused
  outright: there is no projection to drop from.
* **Row cap.** A missing ``LIMIT`` is added at the guard's ``row_cap``; one above it is
  lowered (``rewritten``). The guard's ``effective_limit`` bounds the fetch too, so the cap
  holds even for a statement the rewrite could not touch.

Then the statement runs on the read-only warehouse connection with the guard's
``statement_timeout``, and the result is serialised the way ``read_gold`` serialises a gold
row (dates as ISO strings) except that numerics become ``float``: a chart plots them, and the
exactness ``gold.kpi_value``'s ``numeric`` preserves matters for a KPI a customer reads as a
figure, not for a bar's height.

A ``query`` tile's result is cached in ``dashboard_query_result``
(:mod:`egpt_retail.dashboards.query_results`) by :func:`arefresh_query_tiles`, once when the
tile is written and again on ``POST /dashboards/{id}/refresh``; ``GET /dashboards/{id}/data``
reads the cache and never calls :func:`run_query`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import json
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    cast,
)

import sqlglot
from egpt.exceptions import SQLGuardRefusal
from egpt.guardrails.sql_guard import SQLGuard
from egpt.utils.log import log_warning
from sqlalchemy.engine import Engine
from sqlglot import expressions as exp

from egpt_retail.api import models as wire
from egpt_retail.app.db import build_warehouse_engine, build_warehouse_readonly_engine
from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.app.sql_guard import build_warehouse_read_guard
from egpt_retail.contracts.dashboards import (
    Dashboard,
    DashboardFilterColumn,
    Tile,
    TileSource,
)
from egpt_retail.contracts.semantic import SemanticRead
from egpt_retail.dashboards.query_results import (
    DashboardQueryResultStore,
    QueryResultRecord,
)
from egpt_retail.warehouse.schema import SILVER_SCHEMA

__all__ = [
    "DISTINCT_VALUES_LIMIT",
    "PREVIEW_ROW_LIMIT",
    "QueryVerdict",
    "acheck_query",
    "adistinct_column_values",
    "aavailable_filter_columns",
    "apersonal_data_columns",
    "apreview_query",
    "arefresh_query_tiles",
    "arun_filtered_query_tiles",
    "arun_query",
    "available_filter_columns",
    "build_query_engine",
    "check_query",
    "distinct_column_values",
    "personal_data_columns",
    "preview_query",
    "run_query",
    "verdict_out",
]

#: What the chart editor's "Run preview" gets back at most. The tile's real result, cached on
#: refresh, is bounded by the guard's ``row_cap`` instead.
PREVIEW_ROW_LIMIT = 50

#: What a filter dropdown's value list gets back at most -- sized for a real dropdown, unlike
#: the harvest's own 5-value metadata sample (``semantic/harvest.py``'s ``SAMPLE_LIMIT``).
DISTINCT_VALUES_LIMIT = 200

#: Text-ish semantic data types a dropdown-of-values filter makes sense for. Numeric and date
#: columns are excluded here, not because they cannot be filtered in principle, but because
#: "every distinct value as a dropdown row" is the wrong UI for unbounded cardinality -- a
#: range picker is a different feature.
_FILTERABLE_TEXT_TYPES = frozenset(
    {
        "text",
        "varchar",
        "character varying",
        "char",
        "character",
        "citext",
        "string",
        "enum",
    }
)

PersonalDataLookup = Callable[[Sequence[str]], Mapping[str, FrozenSet[str]]]


@dataclass(frozen=True)
class QueryVerdict:
    """What the checks made of one statement. ``sql`` is the statement as it would run --
    the caller's own on ``ok``, the rewrite on ``rewritten``, the original on ``refused``."""

    status: str  # "ok" | "rewritten" | "refused"
    detail: str
    sql: str
    tables: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def runnable(self) -> bool:
        return self.status != "refused"


def verdict_out(verdict: QueryVerdict) -> wire.QueryGuardVerdict:
    return wire.QueryGuardVerdict(
        status=wire.QueryGuardStatus(verdict.status),
        detail=verdict.detail,
        tables=list(verdict.tables),
    )


def _no_personal_data(tables: Sequence[str]) -> Mapping[str, FrozenSet[str]]:
    return {}


# --- checking ------------------------------------------------------------------------------


def _alias_map(statement: exp.Expression) -> Dict[str, str]:
    """``alias_or_name -> bare table name`` for every real table the statement references,
    so a column qualified by an alias can be attributed to its table."""
    aliases: Dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        if isinstance(table.this, exp.Func) or not table.name:
            continue
        aliases[table.alias_or_name.lower()] = table.name.lower()
        aliases[table.name.lower()] = table.name.lower()
    return aliases


def _personal_hits(
    columns: Iterable[exp.Column],
    aliases: Mapping[str, str],
    personal: Mapping[str, FrozenSet[str]],
) -> List[str]:
    """``table.column`` for every column reference that names personal data. An unqualified
    reference is checked against every referenced table -- conservative on purpose."""
    hits: List[str] = []
    for column in columns:
        name = column.name.lower()
        qualifier = column.table.lower() if column.table else ""
        if qualifier:
            table = aliases.get(qualifier)
            if table is not None and name in personal.get(table, frozenset()):
                hits.append(f"{table}.{name}")
            continue
        for table, names in personal.items():
            if name in names:
                hits.append(f"{table}.{name}")
    return sorted(set(hits))


def _has_any_star(statement: exp.Expression) -> bool:
    """Whether the statement selects ``*`` anywhere -- the outer projection, a CTE body, or a
    derived table's own ``SELECT``.

    ``exp.Select.expressions`` only holds that one node's own projection list, so a check that
    looks only at the outer statement's ``expressions`` is blind to a star nested inside a
    ``WITH ... AS (...)`` or a subquery in ``FROM`` -- exactly the gap
    ``WITH s AS (SELECT * FROM silver.customers) SELECT s.email FROM s`` exploits: the alias
    ``s`` is never resolved back to ``customers``, so the outer ``s.email`` reference is
    invisible to the per-column checks below, and the inner ``SELECT *`` produces no ``Column``
    node for the "outside the projection" walk to catch either. This looks at every nested
    ``SELECT`` in the tree instead.
    """
    return any(
        isinstance(e, exp.Star)
        or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
        for select in statement.find_all(exp.Select)
        for e in select.expressions
    )


def _apply_personal_data_rule(
    statement: exp.Expression, personal: Mapping[str, FrozenSet[str]]
) -> Tuple[Optional[str], List[str]]:
    """Drop personal-data columns from the outermost projection; refuse them anywhere else.
    Returns ``(refusal_reason, dropped)`` -- exactly one of them is non-empty when the rule
    fired, both empty when it did not."""
    if not any(personal.values()):
        return None, []
    aliases = _alias_map(statement)

    # A `*` anywhere in the statement -- not just the outer projection -- makes column-level
    # attribution impossible to verify: it could expand to a personal-data column through a
    # CTE or subquery alias this module cannot resolve back to the real table (see
    # `_has_any_star`'s docstring). Refused outright and conservatively whenever *any* table
    # this statement reads carries personal data, not narrowed to which nested SELECT the star
    # belongs to -- pinning it down precisely has the same alias-resolution problem this check
    # exists to route around, and a false refusal here is recoverable; a missed leak is not.
    if _has_any_star(statement):
        withheld = sorted(
            f"{table}.{name}" for table, names in personal.items() for name in names
        )
        return (
            "This statement selects `*` (in the outer query, a CTE, or a subquery) while "
            f"reading table(s) with personal-data column(s) ({', '.join(withheld)}); `*` "
            "makes column-level attribution impossible to verify, so it is refused rather "
            "than guessed. Name the columns you need instead.",
            [],
        )

    if not isinstance(statement, exp.Select):
        # A UNION or a bare VALUES has no single projection to edit; refuse on any hit.
        hits = _personal_hits(statement.find_all(exp.Column), aliases, personal)
        if hits:
            return (
                f"References personal-data column(s) {', '.join(hits)}; remove them.",
                [],
            )
        return None, []

    kept: List[exp.Expression] = []
    dropped: List[str] = []
    for projection in statement.expressions:
        hits = _personal_hits(projection.find_all(exp.Column), aliases, personal)
        if hits:
            dropped.extend(hits)
            continue
        kept.append(projection)
    if not kept:
        return (
            "Every selected column is personal data "
            f"({', '.join(sorted(set(dropped)))}); nothing would be left to chart.",
            [],
        )

    # Anything outside the projection -- WHERE, JOIN ... ON, GROUP BY, a CTE body, a
    # subquery -- is refused, not rewritten: a predicate on an email address still reveals
    # it through which rows come back.
    outside: List[str] = []
    for node in statement.walk():
        if node is statement:
            continue
        if isinstance(node, exp.Column):
            in_projection = any(
                node is p or any(node is c for c in p.find_all(exp.Column))
                for p in statement.expressions
            )
            if not in_projection:
                outside.extend(_personal_hits([node], aliases, personal))
    if outside:
        return (
            "References personal-data column(s) "
            f"{', '.join(sorted(set(outside)))} outside the SELECT list; remove them.",
            [],
        )

    if dropped:
        statement.set("expressions", kept)
    return None, sorted(set(dropped))


def _apply_row_cap(statement: exp.Expression, row_cap: int) -> Optional[str]:
    """Add a missing ``LIMIT`` or lower one above ``row_cap``. Returns what changed, if
    anything."""
    limit = statement.args.get("limit")
    if limit is None:
        statement.set("limit", exp.Limit(expression=exp.Literal.number(row_cap)))
        return f"Added LIMIT {row_cap} (the row cap)."
    current = limit.expression
    if isinstance(current, exp.Literal) and current.is_int:
        requested = int(current.this)
        if requested > row_cap:
            limit.set("expression", exp.Literal.number(row_cap))
            return f"Lowered LIMIT {requested} to {row_cap} (the row cap)."
    return None


def _apply_filter_predicates(
    statement: exp.Expression,
    predicates: Mapping[Tuple[str, str], Sequence[str]],
    personal: Mapping[str, FrozenSet[str]],
) -> Optional[str]:
    """AND-combine an ``IN (...)`` predicate onto ``statement`` for each ``(table, column)
    -> values`` entry whose table this statement actually reads. A filter column absent from
    a tile's own SQL is silently skipped -- that tile simply is not affected by it, the same
    way a dashboard mixing tiles over different tables works today. A column flagged
    personal-data is never applied (defense in depth: the candidate list a filter was chosen
    from should already exclude it -- see :func:`available_filter_columns`). Values are
    ``sqlglot`` string literals, never concatenated, so quoting/escaping is the library's job,
    the same trust boundary :func:`_apply_row_cap` already relies on. Returns what changed,
    the same shape :func:`_apply_row_cap` does.
    """
    if not predicates:
        return None
    aliases = _alias_map(statement)
    # table -> the alias/name actually used to reference it in this statement (first match
    # wins; a self-join naming the same table twice is rare enough that picking one
    # deterministically beats refusing the whole feature over it).
    reference_for: Dict[str, str] = {}
    for alias, table in aliases.items():
        reference_for.setdefault(table, alias)

    conditions: List[exp.Expression] = []
    changed: List[str] = []
    for (table, column), values in predicates.items():
        if not values or column in personal.get(table, frozenset()):
            continue
        reference = reference_for.get(table)
        if reference is None:
            continue
        conditions.append(
            exp.column(column, table=reference).isin(
                *(exp.Literal.string(v) for v in values)
            )
        )
        changed.append(f"Filtered {table}.{column} to {len(values)} value(s).")

    if not conditions:
        return None
    combined = exp.and_(*conditions)
    where = statement.args.get("where")
    statement.set(
        "where", exp.Where(this=exp.and_(where.this, combined) if where else combined)
    )
    return " ".join(changed)


def _check(
    sql: str,
    *,
    tables: Tuple[str, ...],
    guard: SQLGuard,
    context_tables: Optional[FrozenSet[str]],
    personal_data: PersonalDataLookup,
) -> QueryVerdict:
    """The checks after the guard has parsed and allow-listed the statement."""
    if context_tables is not None:
        outside = sorted(set(tables) - context_tables)
        if outside:
            return QueryVerdict(
                status="refused",
                detail=(
                    f"Reads {', '.join(outside)}, which is not among the tables selected "
                    "for this dashboard; add the table to the context or remove it from the "
                    "query."
                ),
                sql=sql,
                tables=tables,
            )

    statement = cast(exp.Expression, sqlglot.parse_one(sql, read=guard.dialect))
    # Only the tables the statement reads can leak through it; a lookup that answers for
    # more (a caller handing over the whole context) must not refuse a `SELECT *` over a
    # table with no personal data because a different table has some.
    personal = {
        table: columns
        for table, columns in personal_data(tables).items()
        if table in tables
    }
    refusal, dropped = _apply_personal_data_rule(statement, personal)
    if refusal is not None:
        return QueryVerdict(status="refused", detail=refusal, sql=sql, tables=tables)

    changes: List[str] = []
    if dropped:
        changes.append(
            f"Dropped personal-data column(s) {', '.join(dropped)} from the SELECT list."
        )
    capped = _apply_row_cap(statement, guard.row_cap)
    if capped:
        changes.append(capped)

    if not changes:
        return QueryVerdict(status="ok", detail="", sql=sql, tables=tables)
    return QueryVerdict(
        status="rewritten",
        detail=" ".join(changes),
        sql=statement.sql(dialect=guard.dialect),
        tables=tables,
    )


def check_query(
    sql: str,
    *,
    guard: SQLGuard,
    context_tables: Optional[FrozenSet[str]] = None,
    personal_data: PersonalDataLookup = _no_personal_data,
) -> QueryVerdict:
    """The guard, then scope, personal data and the row cap -- see the module docstring.

    ``context_tables`` narrows what the statement may read below the guard's own allow-list
    (``None`` means the whole allow-list). ``personal_data`` answers, for the tables the
    statement was found to read, which of their columns are personal data; the default says
    none are, for a caller with no semantic reader in hand (a unit test, a cookbook section
    over a fixture).
    """
    try:
        tables = guard.check(sql)
    except SQLGuardRefusal as exc:
        return QueryVerdict(status="refused", detail=exc.message, sql=sql)
    return _check(
        sql,
        tables=tables,
        guard=guard,
        context_tables=context_tables,
        personal_data=personal_data,
    )


async def acheck_query(
    sql: str,
    *,
    guard: SQLGuard,
    context_tables: Optional[FrozenSet[str]] = None,
    personal_data: PersonalDataLookup = _no_personal_data,
) -> QueryVerdict:
    """Async variant -- the guard's allow-list source may itself be async."""
    try:
        tables = await guard.acheck(sql)
    except SQLGuardRefusal as exc:
        return QueryVerdict(status="refused", detail=exc.message, sql=sql)
    return await asyncio.to_thread(
        _check,
        sql,
        tables=tables,
        guard=guard,
        context_tables=context_tables,
        personal_data=personal_data,
    )


# --- semantic lookups ----------------------------------------------------------------------


def personal_data_columns(
    reader: SemanticRead, tables: Sequence[str]
) -> Dict[str, FrozenSet[str]]:
    """``table -> {column, ...}`` of the personal-data columns of each named table, as the
    semantic layer marks them. A table the layer does not expose contributes nothing -- the
    guard already refused it if the statement reads it."""
    found: Dict[str, FrozenSet[str]] = {}
    for name in tables:
        table = reader.get_table(name)
        if table is None:
            continue
        found[table.ref.name.lower()] = frozenset(
            c.name.lower() for c in table.personal_data_columns
        )
    return found


async def apersonal_data_columns(
    reader: SemanticRead, tables: Sequence[str]
) -> Dict[str, FrozenSet[str]]:
    """Async variant."""
    found: Dict[str, FrozenSet[str]] = {}
    for name in tables:
        table = await reader.aget_table(name)
        if table is None:
            continue
        found[table.ref.name.lower()] = frozenset(
            c.name.lower() for c in table.personal_data_columns
        )
    return found


def _is_filterable_type(data_type: str) -> bool:
    normalized = data_type.strip().lower()
    return any(
        normalized == kind or normalized.startswith(f"{kind}(")
        for kind in _FILTERABLE_TEXT_TYPES
    )


def available_filter_columns(
    reader: SemanticRead, tables: Sequence[str]
) -> Tuple[DashboardFilterColumn, ...]:
    """Candidate ``(table, column)`` pairs a dashboard could expose as an end-user filter:
    text-typed, non-personal-data columns of the given tables. Sourced from the same semantic
    layer the SQL guard's allow-list comes from, so a column offered here is always one
    :func:`distinct_column_values` can actually read."""
    found: List[DashboardFilterColumn] = []
    for name in tables:
        table = reader.get_table(name)
        if table is None:
            continue
        for column in table.columns:
            if column.is_personal_data or not _is_filterable_type(column.data_type):
                continue
            found.append(
                DashboardFilterColumn(table=table.ref.name, column=column.name)
            )
    return tuple(sorted(set(found), key=lambda f: (f.table, f.column)))


async def aavailable_filter_columns(
    reader: SemanticRead, tables: Sequence[str]
) -> Tuple[DashboardFilterColumn, ...]:
    """Async variant."""
    found: List[DashboardFilterColumn] = []
    for name in tables:
        table = await reader.aget_table(name)
        if table is None:
            continue
        for column in table.columns:
            if column.is_personal_data or not _is_filterable_type(column.data_type):
                continue
            found.append(
                DashboardFilterColumn(table=table.ref.name, column=column.name)
            )
    return tuple(sorted(set(found), key=lambda f: (f.table, f.column)))


# --- running -------------------------------------------------------------------------------


def build_query_engine(settings: RetailSettings) -> Engine:
    """The read-only warehouse engine, or the warehouse role with a warning when no read-only
    DSN is configured -- the same fallback ``read_gold`` makes, for the same reason: the guard
    runs either way, and a dev stack without the reader role should still render a chart."""
    if settings.retail_warehouse_readonly_db_url:
        return build_warehouse_readonly_engine(settings)
    log_warning(
        "dashboard query: EGPT_RETAIL_WAREHOUSE_READONLY_DB_URL unset; reading through the "
        "warehouse role"
    )
    return build_warehouse_engine(settings)


def _serialise(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


def run_query(
    engine: Engine,
    sql: str,
    *,
    limit: int,
    statement_timeout_ms: int,
) -> Tuple[List[str], List[Dict[str, Any]], int]:
    """Execute one already-checked statement and fetch at most ``limit`` rows.

    Uses the DBAPI cursor directly rather than SQLAlchemy's ``text()``: ``text()`` reads
    ``:name`` as a bind parameter, and a chart's SQL can legitimately contain one inside a
    string literal (``'10:30'``). ``SET LOCAL statement_timeout`` scopes the bound to this
    transaction, which is rolled back on the way out -- it never wrote, and must never look
    as though it could have.

    ``SET LOCAL search_path`` precedes it for the same reason ``gold.py``'s materialiser sets
    it before a formula's compile probe: the ``egpt_reader`` role has no search_path of its
    own (R6's live finding), so a chart's bare ``FROM crm_contact`` -- the only form the
    semantic layer's table names come in, see ``TableRef.name`` vs. ``TableRef.qualified`` --
    does not resolve without it, and fails as "relation ... does not exist" against a table
    that is right there in silver.

    The timeout is inlined into the ``SET`` text, never bound: psycopg 3 sends parameters
    server-side (``$1``), and PostgreSQL does not accept a parameter in a ``SET`` statement --
    ``SET LOCAL statement_timeout = %s`` fails with ``syntax error at or near "$1"`` before
    the chart's own SQL ever runs, which surfaced as every composed chart's preview being
    "refused by the warehouse". Same shape ``gold.py``'s ``_run_one`` already uses; the value
    is an ``int`` cast, so nothing from the request reaches the statement text.
    """
    started = dt.datetime.now(dt.timezone.utc)
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        try:
            cursor.execute(f"SET LOCAL search_path = {SILVER_SCHEMA}")
            cursor.execute(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}")
            cursor.execute(sql)
            columns = [d[0] for d in (cursor.description or ())]
            fetched = cursor.fetchmany(int(limit)) if columns else []
        finally:
            cursor.close()
        raw.rollback()
    finally:
        raw.close()
    rows = [
        {name: _serialise(value) for name, value in zip(columns, record)}
        for record in fetched
    ]
    duration = int((dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000)
    return columns, rows, duration


async def arun_query(
    engine: Engine,
    sql: str,
    *,
    limit: int,
    statement_timeout_ms: int,
) -> Tuple[List[str], List[Dict[str, Any]], int]:
    """Async variant: the round trip on a worker thread."""
    return await asyncio.to_thread(
        run_query,
        engine,
        sql,
        limit=limit,
        statement_timeout_ms=statement_timeout_ms,
    )


def _quoted_identifier(name: str, dialect: str) -> str:
    return exp.to_identifier(name, quoted=True).sql(dialect=dialect)


def distinct_column_values(
    engine: Engine,
    guard: SQLGuard,
    table: str,
    column: str,
    *,
    limit: int = DISTINCT_VALUES_LIMIT,
) -> Tuple[str, ...]:
    """Distinct, non-null values of one guard-checked column, for a filter dropdown.

    ``table``/``column`` are rendered as quoted identifiers (``exp.to_identifier``), never
    concatenated as raw text, and the assembled statement still passes through
    :func:`check_query` -- the same allow-list, schema and row-cap checks every other read
    goes through -- before it ever reaches the warehouse.
    """
    qt = _quoted_identifier(table, guard.dialect)
    qc = _quoted_identifier(column, guard.dialect)
    sql = f"SELECT DISTINCT {qc} FROM {qt} WHERE {qc} IS NOT NULL ORDER BY {qc} LIMIT {int(limit)}"
    verdict = check_query(sql, guard=guard, context_tables=frozenset({table.lower()}))
    if not verdict.runnable:
        return ()
    columns, rows, _duration = run_query(
        engine,
        verdict.sql,
        limit=limit,
        statement_timeout_ms=guard.statement_timeout_ms,
    )
    if not columns:
        return ()
    key = columns[0]
    return tuple(str(row[key]) for row in rows if row.get(key) is not None)


async def adistinct_column_values(
    engine: Engine,
    guard: SQLGuard,
    table: str,
    column: str,
    *,
    limit: int = DISTINCT_VALUES_LIMIT,
) -> Tuple[str, ...]:
    """Async variant: the round trip on a worker thread."""
    return await asyncio.to_thread(
        distinct_column_values, engine, guard, table, column, limit=limit
    )


def _first_line(exc: BaseException) -> str:
    return (
        str(getattr(exc, "orig", exc)).splitlines()[0]
        if str(exc)
        else type(exc).__name__
    )


# --- preview -------------------------------------------------------------------------------


def _preview_out(
    verdict: QueryVerdict,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    duration_ms: int,
    error: Optional[str],
) -> wire.QueryPreview:
    return wire.QueryPreview(
        guard=verdict_out(verdict),
        sql=verdict.sql,
        columns=list(columns),
        rows=[dict(r) for r in rows],
        duration_ms=duration_ms,
        error=error,
    )


def preview_query(
    settings: RetailSettings,
    sql: str,
    *,
    table_names: Optional[Sequence[str]] = None,
    limit: int = PREVIEW_ROW_LIMIT,
    reader: Optional[SemanticRead] = None,
    engine: Optional[Engine] = None,
) -> wire.QueryPreview:
    """Check ``sql`` and, when it passes, run it for at most ``limit`` rows. Nothing is
    stored. ``reader``/``engine`` are seams for a caller that already holds them (the
    refresh loop, a cookbook); a bare call opens and closes its own."""
    context = (
        frozenset(t.lower() for t in table_names) if table_names is not None else None
    )
    if reader is None:
        with open_semantic_reader(settings) as opened:
            return preview_query(
                settings,
                sql,
                table_names=table_names,
                limit=limit,
                reader=opened,
                engine=engine,
            )
    guard = build_warehouse_read_guard(reader)
    verdict = check_query(
        sql,
        guard=guard,
        context_tables=context,
        personal_data=lambda tables: personal_data_columns(reader, tables),
    )
    if not verdict.runnable:
        return _preview_out(verdict, [], [], 0, None)

    owned_engine = engine is None
    handle = engine if engine is not None else build_query_engine(settings)
    try:
        columns, rows, duration = run_query(
            handle,
            verdict.sql,
            limit=guard.effective_limit(limit, sql=verdict.sql),
            statement_timeout_ms=guard.statement_timeout_ms,
        )
    except Exception as exc:
        return _preview_out(verdict, [], [], 0, _first_line(exc))
    finally:
        if owned_engine:
            handle.dispose()
    return _preview_out(verdict, columns, rows, duration, None)


async def apreview_query(
    settings: RetailSettings,
    sql: str,
    *,
    table_names: Optional[Sequence[str]] = None,
    limit: int = PREVIEW_ROW_LIMIT,
    reader: Optional[SemanticRead] = None,
    engine: Optional[Engine] = None,
) -> wire.QueryPreview:
    """Async variant: the whole preview on a worker thread, since it holds a Neo4j session
    and a warehouse connection in turn."""
    return await asyncio.to_thread(
        preview_query,
        settings,
        sql,
        table_names=table_names,
        limit=limit,
        reader=reader,
        engine=engine,
    )


# --- refresh (fill the cache) --------------------------------------------------------------
#
# Round 1 replaced a strictly-serial sync `refresh_query_tiles`/`_refresh_one` pair with the
# concurrent `arefresh_query_tiles`/`_arun_one` below (finding #12); round 3's review confirmed
# by a repo-wide grep that the sync pair had zero remaining callers once both router call sites
# moved to the async version, and it was deleted outright here rather than left as unreachable
# code with its own unguarded-exception copy of round 2's finding #2 sitting unpatched inside it.


async def _acheck_and_run(
    sql: str,
    *,
    reader: SemanticRead,
    guard: SQLGuard,
    engine: Engine,
    context_tables: FrozenSet[str],
) -> Tuple[List[str], List[Dict[str, Any]], int, Optional[str], str]:
    """``acheck_query`` then, if it passes, ``arun_query`` -- no write, no cache. Shaped as one
    tile's raw result, the same five-tuple :func:`_arun_one` and :func:`arun_filtered_tile`
    both return: theirs written to the cache, the latter's not.

    Every exception this coroutine could raise is caught and turned into this call's own
    ``error`` string instead (round-2 review, finding #2, on the original ``_arun_one``):
    ``asyncio.gather`` has no ``return_exceptions=True``, so one tile's unguarded exception
    would otherwise propagate out of the gather before anything after it ever ran --
    discarding every other tile's already-computed result along with it.
    """
    try:
        verdict = await acheck_query(
            sql,
            guard=guard,
            context_tables=context_tables,
            personal_data=lambda tables: personal_data_columns(reader, tables),
        )
    except Exception as exc:
        return [], [], 0, _first_line(exc), sql
    if not verdict.runnable:
        return [], [], 0, f"guard refused: {verdict.detail}", verdict.sql
    try:
        columns, rows, duration = await arun_query(
            engine,
            verdict.sql,
            limit=guard.row_cap,
            statement_timeout_ms=guard.statement_timeout_ms,
        )
    except Exception as exc:
        return [], [], 0, _first_line(exc), verdict.sql
    return columns, rows, duration, None, verdict.sql


async def _arun_one(
    tile: Tile, reader: SemanticRead, guard: SQLGuard, engine: Engine
) -> Tuple[List[str], List[Dict[str, Any]], int, Optional[str], str]:
    """One tile's guard check plus, if it passes, its warehouse round trip. The concurrent
    half of :func:`arefresh_query_tiles`: every tile's check and query run at once through
    this, and the caller serialises only the cheap part (the cache write) afterward. The
    serial version this replaced wrote each tile's result as it completed, so one failure
    could never erase an earlier success; :func:`_acheck_and_run`'s own exception handling
    keeps that guarantee under real concurrency.
    """
    assert tile.query is not None  # a query tile, by construction
    return await _acheck_and_run(
        tile.query.sql,
        reader=reader,
        guard=guard,
        engine=engine,
        context_tables=tile.query.source_tables,
    )


async def arun_filtered_tile(
    tile: Tile,
    reader: SemanticRead,
    guard: SQLGuard,
    engine: Engine,
    predicates: Mapping[Tuple[str, str], Sequence[str]],
) -> Tuple[List[str], List[Dict[str, Any]], int, Optional[str], str]:
    """One ``query`` tile's *live* result with the dashboard's active filters AND-combined
    into its ``WHERE`` clause -- run once per request, never written to
    ``dashboard_query_result`` (contrast :func:`_arun_one`). Only called when at least one
    filter has values selected; a filter column absent from this tile's own SQL is silently
    skipped by :func:`_apply_filter_predicates`, so a tile simply unaffected by a given filter
    still renders its normal, cached-equivalent numbers.

    Checks the tile's own SQL first (to get the guard's own rewrite -- row cap, dropped
    personal-data columns -- applied exactly as it would be without any filter), then, only
    when a filter actually applies, mutates and re-checks *that* statement before running it:
    a filter predicate is never trusted without going through the same guard a filter-free
    tile does. This means a filtered read pays for ``acheck_query`` twice (once for the base
    rewrite, once more for the filtered statement) -- deliberate defense-in-depth, not an
    oversight: the second check is what actually verifies the mutated SQL is still safe to
    run, since :func:`_apply_filter_predicates` builds new AST nodes ``check_query`` has never
    seen.
    """
    assert tile.query is not None
    try:
        verdict = await acheck_query(
            tile.query.sql,
            guard=guard,
            context_tables=tile.query.source_tables,
            personal_data=lambda tables: personal_data_columns(reader, tables),
        )
    except Exception as exc:
        return [], [], 0, _first_line(exc), tile.query.sql
    if not verdict.runnable:
        return [], [], 0, f"guard refused: {verdict.detail}", verdict.sql

    sql = verdict.sql
    if predicates:
        # Caught the same way the check/run steps above and below are (round-2 review on the
        # original `_arun_one`, restated in this PR's own review): an unguarded exception here
        # -- a malformed statement `sqlglot.parse_one` cannot re-parse, a semantic-layer hiccup
        # inside `apersonal_data_columns` -- would otherwise propagate out of
        # `arun_filtered_query_tiles`'s `asyncio.gather` and 503 the whole dashboard instead of
        # failing just this one tile.
        try:
            statement = cast(exp.Expression, sqlglot.parse_one(sql, read=guard.dialect))
            personal = await apersonal_data_columns(
                reader, tuple(tile.query.source_tables)
            )
            if _apply_filter_predicates(statement, predicates, personal) is not None:
                sql = statement.sql(dialect=guard.dialect)
        except Exception as exc:
            return [], [], 0, _first_line(exc), sql

    return await _acheck_and_run(
        sql,
        reader=reader,
        guard=guard,
        engine=engine,
        context_tables=tile.query.source_tables,
    )


async def arefresh_query_tiles(
    dashboard: Dashboard,
    settings: RetailSettings,
    results: DashboardQueryResultStore,
    *,
    only: Optional[Iterable[str]] = None,
) -> Dict[str, QueryResultRecord]:
    """Every ``query`` tile's guard check and warehouse round trip, concurrently
    (:func:`asyncio.gather` over :func:`_arun_one`) -- wall-clock cost is ``max(duration)``
    across tiles rather than a strictly serial loop's ``sum(duration)`` (round 1 had one; it
    was deleted in round 3 once nothing called it any more). Writing
    each result to ``results`` stays serial regardless of the gather above: a
    ``DashboardQueryResultStore`` holds one Postgres connection, which is not safe to use from
    more than one coroutine/thread at once, and a write is cheap next to a warehouse round
    trip, so serialising it costs nothing worth gathering for.

    **Intentionally async-only** -- CLAUDE.md's usual "both sync and async" rule does not
    apply here. Concurrency *is* the point of this function; a synchronous twin could only
    offer it back by running its own event loop internally (what both call sites in
    ``api/routers/dashboards.py`` already do via ``asyncio.run(...)``, since neither has one of
    its own to await this on) or by degrading to the strictly serial loop round 3 deleted --
    which would silently reintroduce that loop's own copy of round 2's fault-isolation finding
    alongside it. No caller needs a sync entrypoint: ``_compute_query_tiles`` (called from the
    four plain ``def`` write routes) and ``refresh_dashboard``'s nested ``_refresh`` (a sync
    helper thread-offloaded from that one *async* route, not a route itself) already call this
    via ``asyncio.run``; please don't add a sync twin just to satisfy the general rule.
    """
    wanted = set(only) if only is not None else None
    tiles = [
        t
        for t in dashboard.tiles
        if t.source is TileSource.QUERY and (wanted is None or t.id in wanted)
    ]
    written: Dict[str, QueryResultRecord] = {}
    if not tiles:
        return written

    with open_semantic_reader(settings) as reader:
        guard = build_warehouse_read_guard(reader)
        engine = build_query_engine(settings)
        try:
            outcomes = await asyncio.gather(
                *(_arun_one(tile, reader, guard, engine) for tile in tiles)
            )
        finally:
            engine.dispose()

    for tile, (columns, rows, duration, error, sql) in zip(tiles, outcomes):
        written[tile.id] = await results.aput(
            dashboard.id,
            tile.id,
            columns=columns,
            rows=rows,
            duration_ms=duration,
            error=error,
            sql=sql,
        )
    return written


# --- filtered read (never cached) -----------------------------------------------------------


async def arun_filtered_query_tiles(
    dashboard: Dashboard,
    settings: RetailSettings,
    predicates: Mapping[Tuple[str, str], Sequence[str]],
    *,
    only: Optional[Iterable[str]] = None,
) -> Dict[str, QueryResultRecord]:
    """Every ``query`` tile's live result with the dashboard's active filters applied,
    concurrently, the same shape as :func:`arefresh_query_tiles` (including its ``only``
    scoping) -- except nothing is written to ``dashboard_query_result``: a filter selection is
    per-viewer, transient view-time state, not the dashboard's saved data (R13.1).

    ``only``, when given, is the tile ids actually reachable by ``predicates`` -- the caller
    (``GET /dashboards/{id}/data``) computes this by intersecting each tile's
    ``source_tables`` with the predicates' tables, so a tile no active filter touches keeps
    reading from the cache instead of paying for a warehouse round trip whose result
    :func:`arun_filtered_tile` would have left unfiltered anyway."""
    wanted = set(only) if only is not None else None
    tiles = [
        t
        for t in dashboard.tiles
        if t.source is TileSource.QUERY and (wanted is None or t.id in wanted)
    ]
    if not tiles:
        return {}

    with open_semantic_reader(settings) as reader:
        guard = build_warehouse_read_guard(reader)
        engine = build_query_engine(settings)
        try:
            outcomes = await asyncio.gather(
                *(
                    arun_filtered_tile(tile, reader, guard, engine, predicates)
                    for tile in tiles
                )
            )
        finally:
            engine.dispose()

    computed_at = dt.datetime.now(dt.timezone.utc)
    return {
        tile.id: QueryResultRecord(
            dashboard_id=dashboard.id,
            tile_id=tile.id,
            computed_at=computed_at,
            columns=tuple(columns),
            rows=tuple(rows),
            duration_ms=duration,
            error=error,
            sql=sql,
        )
        for tile, (columns, rows, duration, error, sql) in zip(tiles, outcomes)
    }
