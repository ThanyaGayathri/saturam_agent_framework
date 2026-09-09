"""Unit tests for SQLTools, with and without R6's SQL guard (G3) attached.

Uses an in-memory SQLite engine throughout -- fast, no external service, and (deliberately)
the same backend the standing "PostgreSQL in production, SQLite for dev only" rule reserves
for tests. The guard's statement-timeout mechanism is Postgres-only and is asserted to be a
no-op here, never a failure, on exactly that basis.
"""

from __future__ import annotations

from typing import FrozenSet
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

pytest.importorskip("sqlglot", reason="SQLGuard parses SQL with sqlglot's dialect-aware AST")

from egpt.exceptions import SQLGuardRefusal  # noqa: E402
from egpt.guardrails.sql_guard import SQLGuard  # noqa: E402
from egpt.integrations.tools.sql.sql import SQLTools  # noqa: E402


class _Allowlist:
    def __init__(self, tables: FrozenSet[str]) -> None:
        self._tables = tables

    def allowed_tables(self) -> FrozenSet[str]:
        return self._tables

    async def aallowed_tables(self) -> FrozenSet[str]:
        return self._tables


@pytest.fixture
def sqlite_engine():
    # StaticPool + check_same_thread=False: the arun_* tests below dispatch to a worker
    # thread via asyncio.to_thread, and a plain `sqlite:///:memory:` engine hands a *different*,
    # empty in-memory database to a connection opened from a different thread. This is the
    # standard recipe for one shared in-memory SQLite database across threads in tests.
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT)"))
        conn.execute(text("INSERT INTO orders (id, status) VALUES (1, 'open'), (2, 'closed')"))
    return engine


# ---------------------------------------------------------------------------
# Unguarded SQLTools -- regression coverage for behaviour that predates R6
# ---------------------------------------------------------------------------


def test_unguarded_select_returns_rows(sqlite_engine):
    tools = SQLTools(db_engine=sqlite_engine)
    rows = tools.run_sql("SELECT * FROM orders ORDER BY id")
    assert rows == [{"id": 1, "status": "open"}, {"id": 2, "status": "closed"}]


def test_unguarded_delete_executes_and_commits(sqlite_engine):
    """The gap R6 closes, pinned as a regression test: unguarded, a DELETE runs and commits."""
    tools = SQLTools(db_engine=sqlite_engine)
    tools.run_sql("DELETE FROM orders WHERE id = 1")
    with sqlite_engine.connect() as conn:
        remaining = conn.execute(text("SELECT id FROM orders")).fetchall()
    assert remaining == [(2,)]


def test_unguarded_limit_none_is_unbounded(sqlite_engine):
    tools = SQLTools(db_engine=sqlite_engine)
    rows = tools.run_sql("SELECT * FROM orders", limit=None)
    assert len(rows) == 2


def test_explicit_limit_zero_returns_zero_rows_not_every_row(sqlite_engine):
    """`if limit:` treated an explicit 0 the same as unset/None and fetched everything --
    wrong even unguarded, and a real break of the guard's "always <= row_cap" promise once a
    guard is attached (effective_limit(0) returns 0 unchanged, since 0 is not None and not
    greater than any positive row_cap)."""
    tools = SQLTools(db_engine=sqlite_engine)
    rows = tools.run_sql("SELECT * FROM orders", limit=0)
    assert rows == []


# ---------------------------------------------------------------------------
# Guarded SQLTools
# ---------------------------------------------------------------------------


def _guarded_tools(engine, *allowed: str, row_cap: int = 1000) -> SQLTools:
    guard = SQLGuard(allowlist=_Allowlist(frozenset(allowed)), row_cap=row_cap)
    return SQLTools(db_engine=engine, guard=guard)


def test_guarded_select_on_allowed_table_passes(sqlite_engine):
    tools = _guarded_tools(sqlite_engine, "orders")
    rows = tools.run_sql("SELECT * FROM orders ORDER BY id")
    assert len(rows) == 2


def test_guarded_delete_never_reaches_the_database(sqlite_engine):
    """The property "no statement reached the database" (delivery-plan.md:311), pinned as a
    check rather than an assumption: Session.execute is never called for a refused statement."""
    tools = _guarded_tools(sqlite_engine, "orders")
    with patch.object(Session, "execute") as mock_execute:
        with pytest.raises(SQLGuardRefusal):
            tools.run_sql("DELETE FROM orders WHERE id = 1")
    mock_execute.assert_not_called()

    # And, independent of the mock: the row genuinely still exists.
    with sqlite_engine.connect() as conn:
        remaining = conn.execute(text("SELECT id FROM orders")).fetchall()
    assert remaining == [(1,), (2,)]


def test_guarded_read_outside_allowlist_never_reaches_the_database(sqlite_engine):
    with sqlite_engine.begin() as conn:
        conn.execute(text("CREATE TABLE customers (id INTEGER PRIMARY KEY)"))
    tools = _guarded_tools(sqlite_engine, "orders")  # customers not allowed
    result = tools.run_sql_query("SELECT * FROM customers")
    assert result.startswith("Refused:")
    assert "customers" in result


def test_guarded_run_sql_query_reports_a_refusal_distinctly_from_a_sql_error(sqlite_engine):
    tools = _guarded_tools(sqlite_engine, "orders")
    refused = tools.run_sql_query("DELETE FROM orders")
    # `orders` is allowed -- the guard only checks tables, not columns -- so this clears the
    # guard and fails at the database instead, exercising the *other* except branch.
    ordinary_error = tools.run_sql_query("SELECT no_such_column FROM orders")
    assert refused.startswith("Refused:")
    assert ordinary_error.startswith("Error running query:")


def test_guarded_row_cap_overrides_an_unbounded_request(sqlite_engine):
    tools = _guarded_tools(sqlite_engine, "orders", row_cap=1)
    rows = tools.run_sql("SELECT * FROM orders ORDER BY id", limit=None)
    assert len(rows) == 1


def test_guarded_row_cap_overrides_a_requested_limit_above_the_cap(sqlite_engine):
    tools = _guarded_tools(sqlite_engine, "orders", row_cap=1)
    rows = tools.run_sql("SELECT * FROM orders ORDER BY id", limit=1_000_000)
    assert len(rows) == 1


def test_guarded_statement_timeout_is_a_no_op_on_sqlite(sqlite_engine):
    """The guard's SET LOCAL statement_timeout is Postgres-only (CLAUDE.md: PostgreSQL in
    production, SQLite for dev only). This pins that the dialect check makes it a no-op on
    SQLite rather than a crash."""
    tools = _guarded_tools(sqlite_engine, "orders")
    rows = tools.run_sql("SELECT * FROM orders ORDER BY id")
    assert len(rows) == 2


def test_guarded_statement_timeout_interpolates_the_configured_int_on_postgres(sqlite_engine):
    """Forces the Postgres branch on a faked dialect and asserts the *exact* SQL text sent --
    the PR's own description says a bind-parameter version of this line is a real bug only a
    live Postgres server catches (`SET LOCAL` doesn't accept `$1`), and the rest of this suite
    runs entirely on SQLite, where the branch is always skipped. Without a test like this,
    reverting the interpolation to a bind param for looking "safer" would pass every other
    test in this file."""

    class _FakeResult:
        returns_rows = False

    tools = _guarded_tools(sqlite_engine, "orders")
    tools.guard.statement_timeout_ms = 4242
    executed: list[str] = []

    def fake_execute(self, statement, *args, **kwargs):
        executed.append(str(statement))
        return _FakeResult()

    with (
        patch.object(type(sqlite_engine.dialect), "name", "postgresql"),
        patch.object(Session, "execute", fake_execute),
    ):
        tools.run_sql("SELECT * FROM orders")

    assert "SET LOCAL statement_timeout = 4242" in executed


def test_guarded_statement_timeout_rejects_a_non_int_from_the_guard(sqlite_engine):
    """`self.guard` is typed against SQLGuardLike, a structural protocol -- nothing guarantees
    a *different* implementation validated statement_timeout_ms the way SQLGuard.__init__
    does, so the interpolation site itself must refuse a non-int rather than trust it."""
    tools = _guarded_tools(sqlite_engine, "orders")
    tools.guard.statement_timeout_ms = "5000; DROP TABLE orders"  # type: ignore[assignment]

    with patch.object(type(sqlite_engine.dialect), "name", "postgresql"):
        with pytest.raises(TypeError):
            tools.run_sql("SELECT * FROM orders")


# ---------------------------------------------------------------------------
# Async twins
# ---------------------------------------------------------------------------


def test_run_sql_query_is_reachable_through_the_async_tool_call_path(sqlite_engine):
    """Toolkit.get_async_functions() merges async_functions over functions by *name* -- adding
    arun_sql_query as a plain method is not enough for an agent's async run to ever call it;
    it must be registered via async_tools=[(callable, "run_sql_query")] so the merge actually
    prefers it over the sync run_sql_query under the same tool name."""
    import inspect

    tools = SQLTools(db_engine=sqlite_engine)
    async_fn = tools.get_async_functions()["run_sql_query"]
    assert inspect.iscoroutinefunction(async_fn.entrypoint)


async def test_arun_sql_round_trips(sqlite_engine):
    tools = SQLTools(db_engine=sqlite_engine)
    rows = await tools.arun_sql("SELECT * FROM orders ORDER BY id")
    assert len(rows) == 2


async def test_arun_sql_query_round_trips(sqlite_engine):
    tools = SQLTools(db_engine=sqlite_engine)
    result = await tools.arun_sql_query("SELECT * FROM orders ORDER BY id")
    assert "open" in result


async def test_arun_sql_query_refuses_the_same_way(sqlite_engine):
    tools = _guarded_tools(sqlite_engine, "orders")
    result = await tools.arun_sql_query("DELETE FROM orders")
    assert result.startswith("Refused:")
