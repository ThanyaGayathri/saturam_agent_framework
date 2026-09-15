"""K4 harvest: ``_sample_values`` SQL/param shape (PR #258 round-1 review finding).

``_sample_values`` (and therefore ``harvest_schema(..., sample=True)``, both sync and async --
``aharvest_schema`` is a thin ``asyncio.to_thread`` wrapper around the same call) had zero test
coverage anywhere in the suite before this file, not just gated behind a live warehouse:
``test_semantic_live.py`` only ever calls ``harvest_schema(..., sample=False)``, so even the
live, ``psycopg``-gated tests never exercised this path -- exactly the path that broke in
production (PR #258): a ``% SAMPLE_LIMIT`` interpolation collapsed the ``%%I`` escapes psycopg
needs into a bare ``%I`` before psycopg's own placeholder parser ever saw the string. This file
needs no database and no ``psycopg`` import -- a stand-in cursor is enough to pin the exact SQL
text and parameter shape sent to ``cursor.execute``, which is the invariant that bug broke.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from egpt_retail.semantic.harvest import SAMPLE_LIMIT, _sample_values


class _FakeCursor:
    """Records every ``execute`` call and answers with pre-seeded fetch results, one set per
    call -- mirrors the two-step shape of ``_sample_values``: build the query, then run it."""

    def __init__(
        self,
        fetchone_results: Sequence[Optional[Tuple[Any, ...]]] = (),
        fetchall_results: Sequence[Sequence[Tuple[Any, ...]]] = (),
    ) -> None:
        self._fetchone_results = list(fetchone_results)
        self._fetchall_results = list(fetchall_results)
        self.calls: List[Tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((sql, params))

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        return self._fetchone_results.pop(0)

    def fetchall(self) -> Sequence[Tuple[Any, ...]]:
        return self._fetchall_results.pop(0)


def test_the_built_query_keeps_the_escaped_identifier_placeholders_intact() -> None:
    """The exact regression this PR fixed: a `%` interpolation over the template collapses
    every `%%I` to `%I` before psycopg ever sees it, which psycopg then rejects outright."""
    built_query = "SELECT DISTINCT stage::text FROM silver.crm_deal WHERE stage IS NOT NULL LIMIT 5"
    cursor = _FakeCursor(
        fetchone_results=[(built_query,)], fetchall_results=[[("A",), ("B",)]]
    )

    _sample_values(cursor, "silver", "crm_deal", "stage")

    format_sql, _ = cursor.calls[0]
    assert format_sql.count("%%I") == 4
    assert "%I" not in format_sql.replace("%%I", "")
    assert f"LIMIT {SAMPLE_LIMIT}" in format_sql
    assert "%%I::text" in format_sql


def test_the_identifier_placeholders_resolve_col_schema_table_col_in_order() -> None:
    """The four `%I` slots (`SELECT DISTINCT %I`, `FROM %I.%I`, `WHERE %I`) must line up
    positionally with `col, schema, table, col` -- unchanged by this PR, but worth pinning."""
    cursor = _FakeCursor(fetchone_results=[("irrelevant",)], fetchall_results=[[]])

    _sample_values(cursor, "silver", "crm_contact", "brand")

    format_sql, params = cursor.calls[0]
    placeholder_order = re.findall(r"%\((\w+)\)s", format_sql)
    assert placeholder_order == ["col", "schema", "table", "col"]
    assert params == {"col": "brand", "schema": "silver", "table": "crm_contact"}


def test_the_second_execute_runs_exactly_the_query_the_first_call_built() -> None:
    built_query = "SELECT DISTINCT segment::text FROM silver.crm_account WHERE segment IS NOT NULL LIMIT 5"
    cursor = _FakeCursor(
        fetchone_results=[(built_query,)],
        fetchall_results=[[("Enterprise",), ("SMB",)]],
    )

    values = _sample_values(cursor, "silver", "crm_account", "segment")

    second_sql, second_params = cursor.calls[1]
    assert second_sql == built_query
    assert second_params is None
    assert values == ("Enterprise", "SMB")


def test_no_second_query_runs_when_format_returns_no_row() -> None:
    cursor = _FakeCursor(fetchone_results=[None])

    values = _sample_values(cursor, "silver", "crm_deal", "stage")

    assert values == ()
    assert len(cursor.calls) == 1
