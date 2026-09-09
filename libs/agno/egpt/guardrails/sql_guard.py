"""R6's SQL guard (G3): a deterministic, read-only allow-list over SQL text.

``SQLTools.run_sql`` executes whatever it is handed, with no read-only check, no statement
timeout, and a row limit the calling model controls. ``SQLGuard`` sits above that read path
(wired in ``egpt.integrations.tools.sql.sql`` and ``egpt.integrations.context.database``) and
refuses a statement before it reaches the database, or caps what comes back, on four grounds:
it is not a read, it names a table outside the allow-list, it bundles more than one statement,
or it embeds a table-valued function call disguised as a table.

The allow-list itself is never known to this module. It is the retail product's semantic
layer (``product/egpt_retail/contracts/semantic.py``), which the framework must not import --
the import-linter contract ``core, orchestration, services, and api must not import
integrations`` in ``libs/agno/pyproject.toml`` lists ``egpt.guardrails`` as a source module
forbidden from reaching ``egpt.integrations``, and the product/framework boundary in
``CLAUDE.md`` forbids the reverse. So ``TableAllowlistLike`` is the seam: a caller (in
practice, ``product/egpt_retail/app/sql_guard.py``) supplies whatever satisfies it.

``product/egpt_retail/contracts/kpi.py`` already has a ``referenced_tables()`` -- a regex
scan used to catch drift between a KPI formula and its declared ``source_tables`` at
authoring time. That function is *allowed* to under-report (its own docstring: "it can
under-report... but it cannot invent one"), which is the right trade-off for an authoring aid
and the wrong one for a runtime security boundary. This module parses with ``sqlglot``
instead, specifically so a table referenced only inside a CTE body, a join, or a correlated
subquery is never missed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, NoReturn, Optional, Protocol, Tuple, runtime_checkable

from egpt.exceptions import SQLGuardRefusal
from egpt.utils.log import log_debug, log_warning

try:
    import sqlglot
    from sqlglot import expressions as exp
except ImportError:
    raise ImportError("`sqlglot` not installed. Run `pip install sqlglot` or `pip install egpt[sql]`.")

__all__ = [
    "SQL_GUARD_LOG_FIELD",
    "SQLGuardEvent",
    "TableAllowlistLike",
    "SQLGuard",
    "log_sql_guard_event",
]

#: Key under which the event dict is attached to the ``LogRecord`` -- mirrors
#: ``egpt.providers.events.ESCALATION_LOG_FIELD``'s convention for the same reason: a
#: downstream structured handler and ``caplog`` both read ``getattr(record, ...)``.
SQL_GUARD_LOG_FIELD = "egpt_sql_guard"

#: A KPI formula's ``{name}`` placeholder (see ``kpi.py``'s own ``_PLACEHOLDER_RE``) survives
#: sqlglot's parser completely undetected: ``{predicate}`` parses cleanly as a BigQuery-style
#: STRUCT literal rather than raising, so a leftover, unassembled template would otherwise
#: reach the allow-list check looking like ordinary, harmless SQL (verified empirically --
#: this is not a hypothetical). Refused here, before parsing, so "the guard parses the
#: assembled statement, not the template" (``contracts/CHANGELOG.md``) is enforced rather than
#: merely documented.
_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

#: A single-quoted string literal, doubled-quote escape included (``'it''s'``). Blanked out
#: before the placeholder scan above, so a legitimate Postgres array/text literal like
#: ``'{electronics}'`` is never mistaken for an unresolved template placeholder -- the
#: placeholder syntax and this literal syntax are visually identical and only quoting tells
#: them apart.
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")

#: Logged statements are truncated so a pathological query can't bloat the log.
_STATEMENT_TRUNCATE_LENGTH = 2000


@runtime_checkable
class TableAllowlistLike(Protocol):
    """The set of tables a statement may read. Satisfied by wrapping the semantic layer's
    ``SemanticRead.list_tables()`` in ``product/egpt_retail``; this module never imports that
    directly."""

    def allowed_tables(self) -> FrozenSet[str]: ...

    async def aallowed_tables(self) -> FrozenSet[str]: ...


@dataclass(frozen=True)
class SQLGuardEvent:
    """One guard decision. This *is* the audit trail R6's evidence bar asks for: no DB table
    backs it, because the guard has no DB handle at this layer (only ``egpt.integrations`` does)
    and a refusal must be recordable without one. The run's own persisted tool-call/result is
    the complementary record; this is the structured, greppable one."""

    decision: str  # "allowed" | "refused"
    check: Optional[str] = None
    reason: Optional[str] = None
    statement: str = ""
    tables: Tuple[str, ...] = field(default_factory=tuple)
    row_cap_applied: bool = False

    def __post_init__(self) -> None:
        # A capped statement is, by definition, one that executed -- "refused and capped" is
        # not a real outcome and must not be constructible, or a consumer grouping by
        # `decision` alone could bucket a capped-but-allowed query as an ordinary refusal (or
        # vice versa) and silently misreport the one signal this event exists to carry.
        if self.decision == "refused" and self.row_cap_applied:
            raise ValueError("a refused statement cannot also be row-cap-applied")

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe payload. Every value is a str, bool, list or None."""
        return {
            "event": "sql_guard_decision",
            "decision": self.decision,
            "check": self.check,
            "reason": self.reason,
            "statement": self.statement,
            "tables": list(self.tables),
            "row_cap_applied": self.row_cap_applied,
        }


def log_sql_guard_event(event: SQLGuardEvent) -> None:
    """Emit exactly one log record for a guard decision.

    Refusals log at ``log_warning``: an operator should see a refused DELETE without turning
    debug on, the same reasoning ``log_escalation_event`` gives for escalations, and a refusal
    is more consequential than an escalation. Allowed executions log at ``log_debug`` --
    routine, not noise -- except a row-cap clamp, which is still worth a warning: the model
    asked for more than it was given, and that gap belongs in the audit trail even though the
    statement itself was permitted.
    """
    payload = event.to_dict()
    if event.decision == "refused":
        log_warning(f"SQL guard refused ({event.check}): {event.reason}", extra={SQL_GUARD_LOG_FIELD: payload})
    elif event.row_cap_applied:
        log_warning(f"SQL guard capped the row limit: {event.reason}", extra={SQL_GUARD_LOG_FIELD: payload})
    else:
        tables = ", ".join(event.tables) if event.tables else "no tables"
        log_debug(f"SQL guard allowed a statement over {tables}", extra={SQL_GUARD_LOG_FIELD: payload})


def _truncate(statement: str) -> str:
    if len(statement) <= _STATEMENT_TRUNCATE_LENGTH:
        return statement
    return statement[:_STATEMENT_TRUNCATE_LENGTH] + "... (truncated)"


class SQLGuard:
    """Parses one SQL statement, refuses it on sight, or clears it for execution.

    Never touches a database connection -- ``check()``/``acheck()`` take the SQL text alone
    and either raise :class:`SQLGuardRefusal` or return the tables the statement reads.
    ``effective_limit()`` is a separate, non-raising call: the row cap does not refuse a
    statement, it silently overrides how many rows the caller may fetch back, because the
    delivery plan's own evidence bar draws that line -- a request for a million rows still
    executes, only the fetch is capped.
    """

    def __init__(
        self,
        *,
        allowlist: TableAllowlistLike,
        row_cap: int = 1000,
        statement_timeout_ms: int = 5000,
        dialect: str = "postgres",
        schema: Optional[str] = None,
    ) -> None:
        if not isinstance(row_cap, int) or isinstance(row_cap, bool) or row_cap < 0:
            raise ValueError(f"row_cap must be a non-negative int, got {row_cap!r}")
        if (
            not isinstance(statement_timeout_ms, int)
            or isinstance(statement_timeout_ms, bool)
            or statement_timeout_ms < 0
        ):
            raise ValueError(f"statement_timeout_ms must be a non-negative int, got {statement_timeout_ms!r}")
        self.allowlist = allowlist
        self.row_cap = row_cap
        self.statement_timeout_ms = statement_timeout_ms
        self.dialect = dialect
        # The allow-list itself is bare-name-only (it mirrors
        # product/egpt_retail/contracts/semantic.py's available_tables(), which drops schema
        # too), so a bare `name` match alone cannot tell `silver.orders` apart from
        # `landing.orders` or a future `gold.orders`. When `schema` is set, any table
        # reference that carries an explicit schema/catalog qualifier must match it exactly;
        # an unqualified reference is unaffected (its enforcement is the DB role's grants and
        # the connection's search_path, not this guard). Left optional, defaulting to no
        # restriction, because the framework has no schema of its own to assume -- the retail
        # adapter (product/egpt_retail/app/sql_guard.py) is what actually sets it to "silver".
        self.schema = schema

    def check(self, sql: str) -> Tuple[str, ...]:
        """Classify and validate ``sql``. Returns the tables it reads. Raises
        :class:`SQLGuardRefusal` -- and logs a refused :class:`SQLGuardEvent` -- on any
        violation; logs an allowed event and returns normally otherwise.

        ``sql`` must be the final, fully assembled statement -- never a template with an
        unresolved ``{placeholder}`` still in it (see the module docstring).
        """
        return self._check(sql, self.allowlist.allowed_tables())

    async def acheck(self, sql: str) -> Tuple[str, ...]:
        """Async twin of :meth:`check`, for an allow-list whose source is itself async."""
        return self._check(sql, await self.allowlist.aallowed_tables())

    def _check(self, sql: str, allowed: FrozenSet[str]) -> Tuple[str, ...]:
        truncated = _truncate(sql)

        placeholder = _UNRESOLVED_PLACEHOLDER_RE.search(_STRING_LITERAL_RE.sub("''", sql))
        if placeholder is not None:
            self._refuse(
                "unresolved_placeholder",
                f"Statement still has an unresolved `{{{placeholder.group(1)}}}` placeholder; "
                "the guard only ever parses a fully assembled statement, never a template.",
                truncated,
            )

        try:
            statements = [s for s in sqlglot.parse(sql, read=self.dialect) if s is not None]
        except Exception as exc:
            self._refuse("unparseable", f"Statement could not be parsed: {exc}", truncated)

        if len(statements) != 1:
            self._refuse(
                "multi_statement",
                f"Only a single SQL statement is allowed per call; this looks like {len(statements)} statements.",
                truncated,
            )

        statement = statements[0]
        if not isinstance(statement, exp.Query):
            # "TruncateTable" -> "TRUNCATE TABLE": the reason a refusal names should read as a
            # SQL keyword, not a sqlglot class name.
            verb = re.sub(r"(?<!^)(?=[A-Z])", " ", type(statement).__name__).upper()
            self._refuse(
                "write_statement",
                f"Only read statements are permitted; this is a {verb}.",
                truncated,
            )

        if isinstance(statement, exp.Select) and statement.args.get("into") is not None:
            # `SELECT ... INTO new_table FROM ...` parses as exp.Select (a Query) -- it is a
            # write (it creates and populates a table) wearing a read's syntax. Today this
            # would still be refused as an accident of the allow-list check below (the new
            # table's name is essentially never already on it), which is the wrong thing to
            # rely on: an allow-list that happens to contain the target name would let a real
            # write through as if it were an ordinary SELECT.
            self._refuse(
                "write_statement",
                "Only read statements are permitted; this is a SELECT INTO, which creates a table.",
                truncated,
            )

        # `isinstance(statement, exp.Query)` alone passes a data-modifying CTE straight
        # through: `WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x` is an outer
        # exp.Select wrapping an exp.Delete, and the outer shape is all the check above looks
        # at. A write hidden in a CTE body is still a write, so every statement is walked for
        # an embedded DML node regardless of how deep it is nested.
        embedded_write = next(statement.find_all(exp.Delete, exp.Update, exp.Insert), None)
        if embedded_write is not None:
            self._refuse(
                "write_statement",
                f"Only read statements are permitted; this statement contains an embedded "
                f"{type(embedded_write).__name__.upper()} (e.g. inside a CTE body).",
                truncated,
            )

        # CTE aliases are names introduced by the statement itself, not tables -- excluding
        # them is what lets `WITH recent AS (SELECT * FROM orders) SELECT * FROM recent` check
        # `orders` (the real table inside the CTE body, still found by the recursive
        # find_all below) without also refusing on `recent` (the synthetic alias). Only an
        # *unqualified* reference can be a CTE, though -- `WITH secret AS (...) SELECT * FROM
        # public.secret` names a real, schema-qualified table that merely shares the CTE's bare
        # name, and must still be checked; a CTE can never be referenced with a schema prefix.
        cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}

        tables = set()
        for table in statement.find_all(exp.Table):
            if isinstance(table.this, exp.Func):
                # A function call in table position (`FROM pg_read_file(...)`,
                # `FROM generate_series(...)`) is not a table at all -- the allow-list has
                # nothing to check it against, and letting it through on that technicality
                # would defeat "tables restricted to those the semantic layer exposes".
                self._refuse(
                    "table_function_not_allowed",
                    f"`{table.this.sql(dialect=self.dialect)}` is a function call in table "
                    "position, not a table; the semantic layer's allow-list only names tables.",
                    truncated,
                )
            name = table.name.lower()
            is_unqualified_cte_reference = not table.db and name in cte_names
            if is_unqualified_cte_reference:
                continue
            if self.schema is not None and table.db and table.db.lower() != self.schema.lower():
                self._refuse(
                    "table_not_allowed",
                    f"Not permitted to read {table.db}.{name}: outside the semantic layer's "
                    f"allow-list (expected schema '{self.schema}').",
                    truncated,
                )
            if name:
                tables.add(name)
        referenced = tuple(sorted(tables))

        missing = tuple(sorted(set(referenced) - allowed))
        if missing:
            self._refuse(
                "table_not_allowed",
                f"Not permitted to read {', '.join(missing)}: outside the semantic layer's allow-list.",
                truncated,
                tables=referenced,
            )

        log_sql_guard_event(SQLGuardEvent(decision="allowed", statement=truncated, tables=referenced))
        return referenced

    def _refuse(self, check: str, reason: str, statement: str, *, tables: Tuple[str, ...] = ()) -> NoReturn:
        log_sql_guard_event(
            SQLGuardEvent(decision="refused", check=check, reason=reason, statement=statement, tables=tables)
        )
        raise SQLGuardRefusal(reason, check=check, statement=statement, tables=tables)

    def effective_limit(self, requested: Optional[int], *, sql: str = "") -> int:
        """The fetch limit to actually use -- always ``<= row_cap``, regardless of what
        ``requested`` asks for.

        ``requested is None`` (today's "unlimited") becomes the cap silently: a model that
        didn't think about limits at all isn't making a deliberate ask worth logging.
        ``requested > row_cap`` is a deliberate ask that gets overridden, and that override is
        logged -- this is the audit-trail entry for a "return a million rows" request: the
        statement still runs, only what comes back is capped. ``sql``, when given, rides along
        on that log entry so a clamp can be correlated back to the query that triggered it,
        the same way every other guard event carries its statement.
        """
        if requested is None:
            return self.row_cap
        if requested > self.row_cap:
            log_sql_guard_event(
                SQLGuardEvent(
                    decision="allowed",
                    reason=f"Requested limit {requested} exceeds the row cap {self.row_cap}; capped.",
                    statement=_truncate(sql),
                    row_cap_applied=True,
                )
            )
            return self.row_cap
        return requested
