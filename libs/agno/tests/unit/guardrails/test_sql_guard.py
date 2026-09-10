"""Unit tests for R6's SQL guard (G3): egpt.guardrails.sql_guard.

Deterministic, no database -- the guard only ever sees SQL text.
"""

from __future__ import annotations

from typing import FrozenSet

import pytest

pytest.importorskip("sqlglot", reason="SQLGuard parses SQL with sqlglot's dialect-aware AST")

from egpt.exceptions import SQLGuardRefusal  # noqa: E402
from egpt.guardrails.sql_guard import SQL_GUARD_LOG_FIELD, SQLGuard  # noqa: E402


class _Allowlist:
    """A frozen, in-test double for TableAllowlistLike -- no product code needed here."""

    def __init__(self, tables: FrozenSet[str]) -> None:
        self._tables = tables

    def allowed_tables(self) -> FrozenSet[str]:
        return self._tables

    async def aallowed_tables(self) -> FrozenSet[str]:
        return self._tables


def _guard(*allowed: str, row_cap: int = 1000, schema: str | set[str] | None = None) -> SQLGuard:
    return SQLGuard(allowlist=_Allowlist(frozenset(allowed)), row_cap=row_cap, schema=schema)


# ---------------------------------------------------------------------------
# Reads pass, writes refuse
# ---------------------------------------------------------------------------


def test_plain_select_on_allowed_table_passes():
    guard = _guard("orders")
    assert guard.check("SELECT * FROM orders") == ("orders",)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE orders SET status = 'x'",
        "INSERT INTO orders VALUES (1)",
        "DROP TABLE orders",
        "CREATE TABLE foo AS SELECT * FROM orders",
        "TRUNCATE orders",
    ],
)
def test_write_statements_refuse(sql):
    guard = _guard("orders", "foo")
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check(sql)
    assert excinfo.value.check == "write_statement"


def test_union_of_selects_is_not_misclassified_as_a_write():
    """exp.Select is too narrow a check -- a UNION parses as exp.Union, which is still a
    read. Refusing it would be a false positive on a legitimate query."""
    guard = _guard("orders", "order_items")
    tables = _guard("orders", "order_items").check("SELECT id FROM orders UNION SELECT id FROM order_items")
    assert set(tables) == {"orders", "order_items"}
    assert tables == guard.check("SELECT id FROM orders UNION SELECT id FROM order_items")


def test_select_into_refuses_as_a_write():
    """`SELECT ... INTO new_table FROM ...` parses as exp.Select (a Query) -- it is a write
    (creates and populates a table) wearing a read's syntax, and must be refused explicitly
    rather than relying on the allow-list accidentally catching the new table's name."""
    guard = _guard("orders", "new_table")  # even with the target name allow-listed
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT * INTO new_table FROM orders")
    assert excinfo.value.check == "write_statement"


@pytest.mark.parametrize(
    "sql",
    [
        "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x",
        "WITH x AS (UPDATE orders SET status = 'x' RETURNING *) SELECT * FROM x",
        "WITH x AS (INSERT INTO orders VALUES (1) RETURNING *) SELECT * FROM x",
    ],
)
def test_data_modifying_cte_refuses_even_though_the_outer_statement_is_a_select(sql):
    """`isinstance(statement, exp.Query)` alone is not enough: the outer shape of
    `WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x` is an exp.Select, so that
    check alone passes a write hidden in a CTE body straight through. Every statement is
    walked for an embedded DML node regardless of nesting depth."""
    guard = _guard("orders")
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check(sql)
    assert excinfo.value.check == "write_statement"


# ---------------------------------------------------------------------------
# The table allow-list
# ---------------------------------------------------------------------------


def test_select_on_disallowed_table_refuses_and_names_it():
    guard = _guard("orders")
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT * FROM customers")
    assert excinfo.value.check == "table_not_allowed"
    assert "customers" in excinfo.value.message


def test_cte_alias_is_not_misidentified_as_a_table():
    """The case a naive table-name scan gets wrong: `recent` is a CTE alias, not a table, and
    must not be checked against the allow-list -- but the real table inside the CTE body
    (`orders`) still must be."""
    guard = _guard("orders")
    tables = guard.check("WITH recent AS (SELECT * FROM orders) SELECT * FROM recent")
    assert tables == ("orders",)


def test_schema_qualified_table_matching_a_cte_alias_is_still_checked():
    """A CTE alias is never schema-qualified -- `WITH secret AS (...) SELECT * FROM
    public.secret` names a real table that only happens to share the CTE's bare name, and the
    CTE-alias exclusion must not swallow it. Only an *unqualified* reference can be a CTE."""
    guard = _guard()  # nothing allowed, including "secret"
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("WITH secret AS (SELECT 1) SELECT * FROM public.secret")
    assert excinfo.value.check == "table_not_allowed"
    assert "secret" in excinfo.value.message


def test_cte_body_table_outside_allowlist_still_refuses():
    guard = _guard()  # nothing allowed
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("WITH recent AS (SELECT * FROM orders) SELECT * FROM recent")
    assert excinfo.value.check == "table_not_allowed"
    assert "orders" in excinfo.value.message


def test_join_across_two_allowed_tables_passes():
    guard = _guard("orders", "order_items")
    tables = guard.check("SELECT o.id, i.id FROM orders o JOIN order_items i ON o.id = i.order_id")
    assert set(tables) == {"orders", "order_items"}


def test_join_names_the_specific_missing_table():
    guard = _guard("orders")  # order_items not allowed
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT o.id, i.id FROM orders o JOIN order_items i ON o.id = i.order_id")
    assert excinfo.value.check == "table_not_allowed"
    assert "order_items" in excinfo.value.message
    assert "orders" not in excinfo.value.message.split("Not permitted to read")[1].split(":")[0]


def test_correlated_subquery_table_is_caught():
    guard = _guard("orders")  # customers not allowed
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check(
            "SELECT * FROM orders WHERE customer_id IN (SELECT customer_id FROM customers WHERE segment = 'vip')"
        )
    assert excinfo.value.check == "table_not_allowed"
    assert "customers" in excinfo.value.message


def test_allowlist_match_is_exact_not_a_substring():
    """`orders` and `customer_orders` are different tables that happen to share a substring
    -- the comparison is a set membership test, not `in`/`startswith`/`endswith`, and this
    pins that a switch to substring matching would be caught."""
    guard = _guard("orders")  # customer_orders is NOT allowed, despite containing "orders"
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT * FROM customer_orders")
    assert excinfo.value.check == "table_not_allowed"
    assert "customer_orders" in excinfo.value.message


# ---------------------------------------------------------------------------
# Schema qualification
# ---------------------------------------------------------------------------


def test_unqualified_reference_is_unaffected_by_a_configured_schema():
    guard = _guard("orders", schema="silver")
    assert guard.check("SELECT * FROM orders") == ("orders",)


def test_schema_qualified_reference_matching_the_configured_schema_passes():
    guard = _guard("orders", schema="silver")
    assert guard.check("SELECT * FROM silver.orders") == ("orders",)


def test_schema_qualified_reference_outside_the_configured_schema_refuses():
    """The allow-list is bare-name-only (it mirrors the semantic contract it reads), so
    without a configured schema, `landing.orders` and `silver.orders` are indistinguishable
    to the guard -- `orders` alone being allowed would let either through. With
    schema="silver" configured, a reference qualified with any other schema refuses, even
    though its bare name is on the allow-list."""
    guard = _guard("orders", schema="silver")
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT * FROM landing.orders")
    assert excinfo.value.check == "table_not_allowed"
    assert "landing.orders" in excinfo.value.message


def test_a_set_of_schemas_admits_each_and_refuses_the_rest():
    """R5: the retail read surface spans silver and gold, so the adapter names both. A
    reference qualified with either passes; one qualified with a third schema refuses and the
    reason lists what was expected."""
    guard = _guard("orders", "kpi_value", schema={"silver", "gold"})
    assert guard.check("SELECT * FROM silver.orders") == ("orders",)
    assert guard.check("SELECT * FROM gold.kpi_value") == ("kpi_value",)
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT * FROM landing.orders")
    assert excinfo.value.check == "table_not_allowed"
    assert "gold" in excinfo.value.message and "silver" in excinfo.value.message


def test_schema_matching_is_case_insensitive_for_a_set_too():
    guard = _guard("orders", schema={"Silver"})
    assert guard.check("SELECT * FROM SILVER.orders") == ("orders",)


def test_an_empty_schema_set_is_refused_at_construction():
    with pytest.raises(ValueError):
        _guard("orders", schema=set())


def test_no_configured_schema_leaves_schema_qualified_references_unchecked():
    """Documents the trade-off explicitly rather than leaving it implicit: with no schema
    configured (the framework's own default -- it has no schema of its own to assume), a
    schema-qualified reference is checked by bare name only, same as before this fix."""
    guard = _guard("orders")  # no schema=
    assert guard.check("SELECT * FROM landing.orders") == ("orders",)


# ---------------------------------------------------------------------------
# Table-valued function calls disguised as tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pg_read_file('/etc/passwd')",
        "SELECT * FROM generate_series(1, 1000000000)",
    ],
)
def test_table_valued_function_call_refuses(sql):
    guard = _guard("orders")
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check(sql)
    assert excinfo.value.check == "table_function_not_allowed"


def test_table_valued_function_call_refuses_even_alongside_an_allowed_table():
    guard = _guard("orders")
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT * FROM orders, generate_series(1, 10)")
    assert excinfo.value.check == "table_function_not_allowed"


# ---------------------------------------------------------------------------
# Multi-statement (stacked-query) refusal
# ---------------------------------------------------------------------------


def test_stacked_statements_refuse_before_either_is_inspected():
    guard = _guard("orders")
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT * FROM orders; DROP TABLE orders;")
    assert excinfo.value.check == "multi_statement"


def test_single_statement_with_trailing_semicolon_is_fine():
    guard = _guard("orders")
    assert guard.check("SELECT * FROM orders;") == ("orders",)


# ---------------------------------------------------------------------------
# The assembled-statement rule: a leftover {placeholder} is refused explicitly
# ---------------------------------------------------------------------------


def test_unresolved_placeholder_refuses():
    """sqlglot alone does not catch this: `{predicate}` parses cleanly as a BigQuery-style
    STRUCT literal rather than raising, so an unassembled template would otherwise sail
    through looking like ordinary SQL. The guard checks for it explicitly instead."""
    guard = _guard("orders")
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT * FROM orders WHERE {predicate}")
    assert excinfo.value.check == "unresolved_placeholder"


def test_assembled_statement_with_the_predicate_filled_in_passes():
    guard = _guard("orders")
    template = "SELECT * FROM orders WHERE {predicate}"
    assembled = template.format(predicate="age BETWEEN 18 AND 25")
    assert guard.check(assembled) == ("orders",)


def test_curly_brace_string_literal_is_not_mistaken_for_a_placeholder():
    """A Postgres array/text literal like '{electronics}' is visually identical to an
    unresolved {name} placeholder, but it is inside quotes -- a fully assembled, legitimate
    statement, not a leftover template. Only an unquoted {name} should refuse."""
    guard = _guard("orders")
    assert guard.check("SELECT * FROM orders WHERE tags = '{electronics}'") == ("orders",)


# ---------------------------------------------------------------------------
# Unparseable input refuses rather than raising an uncaught exception
# ---------------------------------------------------------------------------


def test_garbage_input_refuses_rather_than_raising_a_raw_parse_error():
    guard = _guard("orders")
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT FROM WHERE")
    assert excinfo.value.check == "unparseable"


# ---------------------------------------------------------------------------
# effective_limit: the row cap
# ---------------------------------------------------------------------------


def test_effective_limit_none_becomes_the_cap():
    guard = _guard(row_cap=1000)
    assert guard.effective_limit(None) == 1000


def test_effective_limit_under_cap_passes_through():
    guard = _guard(row_cap=1000)
    assert guard.effective_limit(10) == 10


def test_effective_limit_over_cap_clamps_and_logs(caplog):
    guard = _guard(row_cap=1000)
    with caplog.at_level("WARNING"):
        assert guard.effective_limit(10_000_000) == 1000
    events = [r for r in caplog.records if getattr(r, SQL_GUARD_LOG_FIELD, None) is not None]
    assert len(events) == 1
    assert events[0].egpt_sql_guard["row_cap_applied"] is True


def test_effective_limit_clamp_carries_the_statement_that_triggered_it(caplog):
    """Every other guard event carries `statement=` -- a clamp with none couldn't be
    correlated back to the query that triggered it in the audit log."""
    guard = _guard(row_cap=1000)
    with caplog.at_level("WARNING"):
        guard.effective_limit(10_000_000, sql="SELECT * FROM orders")
    events = [r for r in caplog.records if getattr(r, SQL_GUARD_LOG_FIELD, None) is not None]
    assert events[0].egpt_sql_guard["statement"] == "SELECT * FROM orders"


def test_effective_limit_none_does_not_log():
    guard = _guard(row_cap=1000)
    import logging

    class _Handler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record)

    handler = _Handler()
    logger = logging.getLogger("egpt")
    logger.addHandler(handler)
    try:
        guard.effective_limit(None)
    finally:
        logger.removeHandler(handler)
    assert not any(getattr(r, SQL_GUARD_LOG_FIELD, None) is not None for r in handler.records)


# ---------------------------------------------------------------------------
# SQLGuardEvent: illegal states unrepresentable
# ---------------------------------------------------------------------------


def test_refused_and_row_cap_applied_together_is_not_constructible():
    """A capped statement is, by definition, one that executed -- 'refused and capped' is not
    a real outcome, or a consumer grouping audit events by `decision` alone could misreport
    which one actually happened."""
    from egpt.guardrails.sql_guard import SQLGuardEvent

    with pytest.raises(ValueError):
        SQLGuardEvent(decision="refused", row_cap_applied=True)


# ---------------------------------------------------------------------------
# Constructor validation: a caller mistake fails at construction, not at use
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_row_cap", [-1, "1000", 1.5, True])
def test_invalid_row_cap_rejected_at_construction(bad_row_cap):
    with pytest.raises(ValueError):
        SQLGuard(allowlist=_Allowlist(frozenset()), row_cap=bad_row_cap)


@pytest.mark.parametrize("bad_timeout", [-1, "5000", 5.0, True])
def test_invalid_statement_timeout_ms_rejected_at_construction(bad_timeout):
    with pytest.raises(ValueError):
        SQLGuard(allowlist=_Allowlist(frozenset()), statement_timeout_ms=bad_timeout)


# ---------------------------------------------------------------------------
# Async twin
# ---------------------------------------------------------------------------


async def test_acheck_matches_check():
    guard = _guard("orders")
    assert await guard.acheck("SELECT * FROM orders") == ("orders",)


async def test_acheck_refuses_the_same_way():
    guard = _guard("orders")
    with pytest.raises(SQLGuardRefusal) as excinfo:
        await guard.acheck("DELETE FROM orders")
    assert excinfo.value.check == "write_statement"
