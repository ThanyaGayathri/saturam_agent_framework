"""The shared advisory-lock-then-DDL-then-commit helper (R15, id=3988596598) -- no database.

``RehearsalReviewStore.ensure_schema`` is the first caller (see ``test_r15_rehearsal_live.py``
for that against real Postgres). What is worth testing without one is the sequence itself: the
lock is taken before any DDL runs, every statement runs in order, and the whole thing commits
exactly once.
"""

from __future__ import annotations

import asyncio
from typing import Any, List

from egpt_retail.db.schema_utils import aensure_schema, ensure_schema


class _FakeCursor:
    def __init__(self, calls: List[Any]) -> None:
        self._calls = calls

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None

    def execute(self, statement: str, params: Any = None) -> None:
        self._calls.append((statement, params))


class _FakeConnection:
    def __init__(self) -> None:
        self.calls: List[Any] = []
        self.committed = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.calls)

    def commit(self) -> None:
        self.committed += 1


def test_lock_is_taken_before_any_ddl_statement() -> None:
    connection = _FakeConnection()

    ensure_schema(
        connection, ("CREATE SCHEMA IF NOT EXISTS x", "CREATE TABLE x.y ()"), "k"
    )

    lock_call, first_ddl, second_ddl = connection.calls
    assert "pg_advisory_xact_lock" in lock_call[0]
    assert lock_call[1] == ("k",)
    assert first_ddl == ("CREATE SCHEMA IF NOT EXISTS x", None)
    assert second_ddl == ("CREATE TABLE x.y ()", None)


def test_commits_exactly_once() -> None:
    connection = _FakeConnection()

    ensure_schema(connection, ("CREATE TABLE x ()",), "k")

    assert connection.committed == 1


def test_lock_key_is_passed_through_verbatim() -> None:
    connection = _FakeConnection()

    ensure_schema(connection, (), "egpt_retail.evals.ddl")

    assert connection.calls[0][1] == ("egpt_retail.evals.ddl",)


def test_async_variant_runs_the_same_sequence() -> None:
    connection = _FakeConnection()

    asyncio.run(aensure_schema(connection, ("CREATE TABLE x ()",), "k"))

    assert connection.committed == 1
    assert len(connection.calls) == 2  # lock + one DDL statement
