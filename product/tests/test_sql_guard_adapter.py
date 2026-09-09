"""R6's retail-side seam: SemanticTableAllowlist adapts SemanticRead to the framework's
SQLGuard, without SQLGuard ever importing this package (the framework/product boundary).
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "sqlglot", reason="SQLGuard parses SQL with sqlglot's dialect-aware AST"
)

from egpt.exceptions import SQLGuardRefusal  # noqa: E402

from egpt_retail.app.sql_guard import build_warehouse_read_guard  # noqa: E402
from egpt_retail.contracts.semantic_stub import StubSemanticRead  # noqa: E402


def _reader(*table_names: str) -> StubSemanticRead:
    return StubSemanticRead.from_payload(
        {"tables": [{"name": name} for name in table_names]}
    )


def test_allowed_table_from_the_semantic_layer_passes():
    guard = build_warehouse_read_guard(_reader("crm_contact", "crm_deal"))
    assert guard.check("SELECT * FROM crm_contact") == ("crm_contact",)


def test_table_absent_from_the_semantic_layer_refuses():
    """The allow-list comes from SemanticRead.list_tables(), not from a hardcoded set -- a
    table the semantic layer has never heard of (proposed-but-unaccepted, or simply absent)
    is refused the same way an unknown table name is."""
    guard = build_warehouse_read_guard(_reader("crm_contact"))
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT * FROM internal_salaries")
    assert excinfo.value.check == "table_not_allowed"


def test_proposed_but_unaccepted_table_is_not_in_the_allowlist():
    """available_tables() only ever sees accepted rows (SemanticRead's include_proposed
    default) -- a proposed table is exactly as unreachable as one that was never harvested."""
    reader = StubSemanticRead.from_payload(
        {"tables": [{"name": "web_sessions", "status": "proposed"}]}
    )
    guard = build_warehouse_read_guard(reader)
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT * FROM web_sessions")
    assert excinfo.value.check == "table_not_allowed"


@pytest.mark.anyio
async def test_aallowed_tables_matches_the_sync_reader():
    guard = build_warehouse_read_guard(_reader("crm_contact"))
    assert await guard.acheck("SELECT * FROM crm_contact") == ("crm_contact",)


def test_schema_qualified_to_silver_passes():
    guard = build_warehouse_read_guard(_reader("crm_contact"))
    assert guard.check("SELECT * FROM silver.crm_contact") == ("crm_contact",)


def test_schema_qualified_to_a_different_schema_refuses_even_with_the_bare_name_allowed():
    """The allow-list is bare-name-only (available_tables() drops schema), so without a
    configured schema this would be indistinguishable from silver.crm_contact. R6's guard is
    built with schema=SILVER_SCHEMA specifically to close that."""
    guard = build_warehouse_read_guard(_reader("crm_contact"))
    with pytest.raises(SQLGuardRefusal) as excinfo:
        guard.check("SELECT * FROM landing.crm_contact")
    assert excinfo.value.check == "table_not_allowed"
