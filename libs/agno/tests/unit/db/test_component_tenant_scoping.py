"""Tenant scoping for the component tables (EGPT-2.1).

Exercises a real SQLite database rather than mocks, so the assertions cover the actual DDL
(column + composite indexes) and the actual SQL predicates, not just call signatures.

Where the isolation actually lives: every mutation (delete_component, delete_config,
set_current_version, upsert_config) runs a tenant-scoped existence precheck and bails before
touching anything. **Those prechecks are the load-bearing layer, and they are what the
cross-tenant tests below pin.** The tenant predicates on the inner UPDATE/DELETE statements
are defence in depth: mutation testing shows removing one is survived by this suite, because
the precheck has already refused. Do not "simplify" a precheck away on the belief that the
inner predicate is covered -- it is not.
"""

import os
import tempfile

import pytest
from sqlalchemy import text

from egpt.db.base import ComponentType
from egpt.db.component_scope import TENANT_COLUMN, TENANT_SCOPED_COMPONENT_TABLES
from egpt.db.migrations.manager import MigrationManager
from egpt.db.migrations.versions.v2_6_0 import COMPONENT_TABLE_TYPES

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"

COMPONENT_TABLES = ("egpt_components", "egpt_component_configs", "egpt_component_links")

# Every tenant-scoped component table, derived from the single declaration in
# ``egpt.db.component_scope`` so this list cannot fall behind it. Wider than COMPONENT_TABLES
# above, which is the set the v2_6_0 migration touches -- ``component_audit`` was born with a
# tenant_id column and is therefore not part of that migration but is part of the contract.
ALL_TENANT_SCOPED_TABLES = tuple(f"egpt_{t}" for t in TENANT_SCOPED_COMPONENT_TABLES)

EXPECTED_INDEXES = {
    "egpt_components": {"idx_egpt_components_tenant_id_component_id"},
    "egpt_component_configs": {"idx_egpt_component_configs_tenant_id_component_id"},
    "egpt_component_links": {
        "idx_egpt_component_links_tenant_id_parent_component_id",
        "idx_egpt_component_links_tenant_id_child_component_id",
    },
    "egpt_component_audit": {"idx_egpt_component_audit_tenant_id_component_id"},
}


# ============================================================================
# FIXTURES / HELPERS
# ============================================================================


@pytest.fixture
def sqlite_db():
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SqliteDb(db_file=path)
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass


def _columns(db, table_name):
    with db.Session() as sess:
        return {row[1] for row in sess.execute(text(f"PRAGMA table_info({table_name})")).fetchall()}


def _indexes(db, table_name):
    with db.Session() as sess:
        return {row[1] for row in sess.execute(text(f"PRAGMA index_list({table_name})")).fetchall()}


def _create_component(db, component_id, tenant_id, *, stage="draft", links=None, name=None):
    return db.create_component_with_config(
        component_id=component_id,
        component_type=ComponentType.AGENT,
        name=name or component_id,
        config={"model": "gpt-5.5"},
        stage=stage,
        links=links,
        tenant_id=tenant_id,
    )


# ============================================================================
# SCHEMA
# ============================================================================


def test_component_tables_have_tenant_id_column(sqlite_db):
    """Every component table carries a tenant_id column once created.

    This is what "tenant_id is a recognized filterable column" means for components (EGPT-2.7):
    a real, declared column on every one of the four tables, not a key inside the config JSON
    and not a value the query layer has to interpret. Component queries take fixed typed
    parameters rather than filter expressions, so there is no ``allowed_columns`` whitelist in
    the picture -- ``db/filter_converter.py`` is trace-only and the traces table has no
    tenant_id. The DDL is the whitelist, and this test is what pins it.
    """
    _create_component(sqlite_db, "agent-1", TENANT_A)

    for table_name in ALL_TENANT_SCOPED_TABLES:
        assert TENANT_COLUMN in _columns(sqlite_db, table_name), f"{table_name} is missing {TENANT_COLUMN}"


def test_component_tables_have_tenant_leading_composite_indexes(sqlite_db):
    """Tenant-scoped lookups are backed by composite indexes with tenant_id leading."""
    _create_component(sqlite_db, "agent-1", TENANT_A)

    assert set(EXPECTED_INDEXES) == set(ALL_TENANT_SCOPED_TABLES), (
        "EXPECTED_INDEXES has fallen behind TENANT_SCOPED_COMPONENT_TABLES; a tenant-scoped "
        "table with no expected index would be silently unchecked"
    )
    for table_name, expected in EXPECTED_INDEXES.items():
        assert expected.issubset(_indexes(sqlite_db, table_name)), f"{table_name} is missing {expected}"


def test_tenant_id_leads_every_component_composite_index(sqlite_db):
    """tenant_id is the *first* column of each index, not merely present in one.

    A ``(component_id, tenant_id)`` index is unusable for "everything in this tenant" and turns
    a scoped list into a scan; the ordering is a correctness property of the scoping design, not
    a tuning detail.
    """
    _create_component(sqlite_db, "agent-1", TENANT_A)

    with sqlite_db.Session() as sess:
        for table_name, expected in EXPECTED_INDEXES.items():
            for index_name in expected:
                columns = [row[2] for row in sess.execute(text(f"PRAGMA index_info({index_name})")).fetchall()]
                assert columns[0] == TENANT_COLUMN, f"{index_name} on {table_name} leads with {columns[0]}"


def test_tenant_id_is_nullable(sqlite_db):
    """A component written without a tenant is accepted and stores NULL."""
    component, config = _create_component(sqlite_db, "agent-untenanted", None)

    assert component["tenant_id"] is None
    assert config["tenant_id"] is None


# ============================================================================
# READ SCOPING
# ============================================================================


def test_create_stores_tenant_on_component_config_and_links(sqlite_db):
    """tenant_id reaches all three tables, not just the component root."""
    _create_component(sqlite_db, "child", TENANT_A, stage="published")
    parent, config = _create_component(
        sqlite_db,
        "parent",
        TENANT_A,
        links=[
            {
                "link_kind": "member",
                "link_key": "child",
                "child_component_id": "child",
                "child_version": 1,
                "position": 0,
            }
        ],
    )

    assert parent["tenant_id"] == TENANT_A
    assert config["tenant_id"] == TENANT_A

    links = sqlite_db.get_links("parent", 1, tenant_id=TENANT_A)
    assert len(links) == 1
    assert links[0]["tenant_id"] == TENANT_A


def test_get_component_is_tenant_scoped(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)

    assert sqlite_db.get_component("agent-1", tenant_id=TENANT_A) is not None
    assert sqlite_db.get_component("agent-1", tenant_id=TENANT_B) is None


def test_get_component_without_tenant_applies_no_filter(sqlite_db):
    """tenant_id=None means "no tenant predicate", which keeps existing callers working."""
    _create_component(sqlite_db, "agent-1", TENANT_A)

    assert sqlite_db.get_component("agent-1", tenant_id=None) is not None


def test_list_components_scopes_both_rows_and_total_count(sqlite_db):
    """The count query is a separate statement, so it needs its own coverage."""
    _create_component(sqlite_db, "a-1", TENANT_A)
    _create_component(sqlite_db, "a-2", TENANT_A)
    _create_component(sqlite_db, "b-1", TENANT_B)

    rows_a, total_a = sqlite_db.list_components(tenant_id=TENANT_A)
    assert {row["component_id"] for row in rows_a} == {"a-1", "a-2"}
    assert total_a == 2

    rows_b, total_b = sqlite_db.list_components(tenant_id=TENANT_B)
    assert {row["component_id"] for row in rows_b} == {"b-1"}
    assert total_b == 1

    rows_all, total_all = sqlite_db.list_components(tenant_id=None)
    assert total_all == 3
    assert len(rows_all) == 3


def test_get_config_and_list_configs_are_tenant_scoped(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)

    assert sqlite_db.get_config("agent-1", tenant_id=TENANT_A) is not None
    assert sqlite_db.get_config("agent-1", tenant_id=TENANT_B) is None

    assert len(sqlite_db.list_configs("agent-1", tenant_id=TENANT_A)) == 1
    assert sqlite_db.list_configs("agent-1", tenant_id=TENANT_B) == []


def test_get_links_and_get_dependents_are_tenant_scoped(sqlite_db):
    _create_component(sqlite_db, "child", TENANT_A, stage="published")
    _create_component(
        sqlite_db,
        "parent",
        TENANT_A,
        links=[
            {
                "link_kind": "member",
                "link_key": "child",
                "child_component_id": "child",
                "child_version": 1,
                "position": 0,
            }
        ],
    )

    assert len(sqlite_db.get_links("parent", 1, tenant_id=TENANT_A)) == 1
    assert sqlite_db.get_links("parent", 1, tenant_id=TENANT_B) == []

    assert len(sqlite_db.get_dependents("child", tenant_id=TENANT_A)) == 1
    assert sqlite_db.get_dependents("child", tenant_id=TENANT_B) == []


def test_load_component_graph_resolves_children_within_the_tenant(sqlite_db):
    _create_component(sqlite_db, "child", TENANT_A, stage="published")
    _create_component(
        sqlite_db,
        "parent",
        TENANT_A,
        stage="published",
        links=[
            {
                "link_kind": "member",
                "link_key": "child",
                "child_component_id": "child",
                "child_version": 1,
                "position": 0,
            }
        ],
    )

    graph = sqlite_db.load_component_graph("parent", tenant_id=TENANT_A)
    assert graph is not None
    assert graph["component"]["component_id"] == "parent"
    assert len(graph["children"]) == 1
    assert graph["children"][0]["graph"]["component"]["component_id"] == "child"

    assert sqlite_db.load_component_graph("parent", tenant_id=TENANT_B) is None


# ============================================================================
# MUTATION SCOPING
# ============================================================================


def test_delete_component_does_not_cross_tenants(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)

    assert sqlite_db.delete_component("agent-1", tenant_id=TENANT_B) is False
    assert sqlite_db.get_component("agent-1", tenant_id=TENANT_A) is not None

    assert sqlite_db.delete_component("agent-1", tenant_id=TENANT_A) is True
    assert sqlite_db.get_component("agent-1", tenant_id=TENANT_A) is None


def test_hard_delete_does_not_cross_tenants(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)

    assert sqlite_db.delete_component("agent-1", hard_delete=True, tenant_id=TENANT_B) is False
    assert sqlite_db.get_component("agent-1", tenant_id=TENANT_A) is not None


def test_upsert_config_does_not_cross_tenants(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)

    with pytest.raises(ValueError, match="not found"):
        sqlite_db.upsert_config("agent-1", config={"model": "gpt-5.5"}, tenant_id=TENANT_B)

    updated = sqlite_db.upsert_config("agent-1", config={"model": "other"}, tenant_id=TENANT_A)
    assert updated["version"] == 2
    assert updated["tenant_id"] == TENANT_A


def test_delete_config_does_not_cross_tenants(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)
    sqlite_db.upsert_config("agent-1", config={"model": "other"}, tenant_id=TENANT_A)

    assert sqlite_db.delete_config("agent-1", 2, tenant_id=TENANT_B) is False
    assert len(sqlite_db.list_configs("agent-1", tenant_id=TENANT_A)) == 2

    assert sqlite_db.delete_config("agent-1", 2, tenant_id=TENANT_A) is True
    assert len(sqlite_db.list_configs("agent-1", tenant_id=TENANT_A)) == 1


def test_set_current_version_does_not_cross_tenants(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")

    assert sqlite_db.set_current_version("agent-1", 1, tenant_id=TENANT_B) is False
    assert sqlite_db.set_current_version("agent-1", 1, tenant_id=TENANT_A) is True


# ============================================================================
# LIFECYCLE STILL HOLDS
# ============================================================================


def test_published_configs_remain_immutable(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")

    with pytest.raises(ValueError, match="Cannot update published config"):
        sqlite_db.upsert_config("agent-1", version=1, config={"model": "other"}, tenant_id=TENANT_A)


def test_draft_config_is_deletable_and_current_is_not(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")
    sqlite_db.upsert_config("agent-1", config={"model": "draft"}, tenant_id=TENANT_A)

    # v2 is a draft and not current -> deletable
    assert sqlite_db.delete_config("agent-1", 2, tenant_id=TENANT_A) is True

    # v1 is published and current -> not deletable
    with pytest.raises(ValueError, match="Cannot delete current config"):
        sqlite_db.delete_config("agent-1", 1, tenant_id=TENANT_A)


def test_publishing_sets_current_version(sqlite_db):
    _create_component(sqlite_db, "agent-1", TENANT_A)
    sqlite_db.upsert_config("agent-1", version=1, stage="published", tenant_id=TENANT_A)

    component = sqlite_db.get_component("agent-1", tenant_id=TENANT_A)
    assert component["current_version"] == 1


# ============================================================================
# MIGRATION ROUND TRIP
# ============================================================================


def _strip_tenant_scoping(db):
    """Return the component tables to their pre-v2.6.0 shape."""
    from egpt.db.migrations.versions.v2_6_0 import _index_names

    with db.Session() as sess, sess.begin():
        for table_type, table_name in zip(COMPONENT_TABLE_TYPES, COMPONENT_TABLES):
            for index_name in _index_names(table_type, table_name):
                sess.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
            sess.execute(text(f"ALTER TABLE {table_name} DROP COLUMN tenant_id"))

    # Outside the DDL transaction: upsert_schema_version opens its own session.
    for table_name in COMPONENT_TABLES:
        db.upsert_schema_version(table_name=table_name, version="2.5.6")


@pytest.mark.asyncio
async def test_migration_adds_tenant_id_and_preserves_existing_rows(sqlite_db):
    """Up-migrating a pre-2.6.0 table adds the column and indexes without losing data."""
    _create_component(sqlite_db, "child", TENANT_A, stage="published")
    _create_component(
        sqlite_db,
        "parent",
        TENANT_A,
        stage="published",
        links=[
            {
                "link_kind": "member",
                "link_key": "child",
                "child_component_id": "child",
                "child_version": 1,
                "position": 0,
            }
        ],
    )

    _strip_tenant_scoping(sqlite_db)
    for table_name in COMPONENT_TABLES:
        assert "tenant_id" not in _columns(sqlite_db, table_name)

    await MigrationManager(sqlite_db).up()

    for table_name in COMPONENT_TABLES:
        assert "tenant_id" in _columns(sqlite_db, table_name)
        assert EXPECTED_INDEXES[table_name].issubset(_indexes(sqlite_db, table_name))

    # Existing version / stage / link data survived, with a NULL tenant.
    component = sqlite_db.get_component("parent", tenant_id=None)
    assert component is not None
    assert component["current_version"] == 1
    assert component["tenant_id"] is None

    config = sqlite_db.get_config("parent", version=1, tenant_id=None)
    assert config["stage"] == "published"

    links = sqlite_db.get_links("parent", 1, tenant_id=None)
    assert len(links) == 1
    assert links[0]["child_component_id"] == "child"


@pytest.mark.asyncio
async def test_migration_rolls_back_cleanly(sqlite_db):
    """Down-migrating removes the column and indexes and leaves the rows intact."""
    _create_component(sqlite_db, "agent-1", TENANT_A, stage="published")

    await MigrationManager(sqlite_db).down(target_version="2.5.6")

    for table_name in COMPONENT_TABLES:
        assert "tenant_id" not in _columns(sqlite_db, table_name)
        assert not EXPECTED_INDEXES[table_name] & _indexes(sqlite_db, table_name)

    with sqlite_db.Session() as sess:
        row = sess.execute(
            text("SELECT component_id, current_version FROM egpt_components WHERE component_id = 'agent-1'")
        ).fetchone()
        assert row is not None
        assert row[1] == 1

        stage = sess.execute(
            text("SELECT stage FROM egpt_component_configs WHERE component_id = 'agent-1' AND version = 1")
        ).scalar()
        assert stage == "published"


@pytest.mark.asyncio
async def test_migration_is_idempotent(sqlite_db):
    """Running up() twice against an already-migrated table is a no-op, not an error."""
    _create_component(sqlite_db, "agent-1", TENANT_A)

    manager = MigrationManager(sqlite_db)
    await manager.up(force=True)
    await manager.up(force=True)

    for table_name in COMPONENT_TABLES:
        assert "tenant_id" in _columns(sqlite_db, table_name)


def test_migration_skips_absent_tables(sqlite_db):
    """Deployments that never created component tables must not error on up()."""
    from egpt.db.migrations.versions import v2_6_0

    assert v2_6_0.up(sqlite_db, "components", "egpt_components") is False


def test_migration_ignores_non_component_table_types(sqlite_db):
    from egpt.db.migrations.versions import v2_6_0

    assert v2_6_0.up(sqlite_db, "sessions", "egpt_sessions") is False
    assert v2_6_0.down(sqlite_db, "sessions", "egpt_sessions") is False


# ============================================================================
# MIGRATION MANAGER BLAST RADIUS
# ============================================================================
#
# Registering the component table types widened what up()/down() iterate for EVERY
# backend. AsyncBaseDb models no component tables at all -- it has no components_table
# constructor param and no components_table_name attribute -- so a naive getattr would
# raise AttributeError and take the whole migration run down with it.


def test_table_type_map_matches_basedb_attributes(sqlite_db):
    """Every mapped attribute must actually exist on a sync db."""
    for table_type, attr in MigrationManager._TABLE_TYPE_TO_ATTR.items():
        assert hasattr(sqlite_db, attr), f"{table_type} -> {attr} missing on SqliteDb"


@pytest.mark.asyncio
async def test_migration_manager_survives_db_without_component_tables(tmp_path):
    """up()/down() must not raise on an async db that models no component tables."""
    # AsyncSqliteDb resolves the aiosqlite dialect in __init__, so without the driver this
    # would fail rather than skip. The test genuinely needs a live async engine.
    pytest.importorskip("aiosqlite", reason="MigrationManager.up() needs a real async engine")
    from egpt.integrations.db.sqlite.async_sqlite import AsyncSqliteDb

    db = AsyncSqliteDb(db_file=str(tmp_path / "async.db"))
    assert not hasattr(db, "components_table_name"), "fixture assumption changed"

    await MigrationManager(db).up()
    await MigrationManager(AsyncSqliteDb(db_file=str(tmp_path / "async.db"))).down(target_version="2.5.6")


@pytest.mark.asyncio
async def test_migration_manager_reports_unmigratable_table_type(tmp_path):
    """Naming a table type the db cannot migrate warns and returns, rather than raising."""
    pytest.importorskip("aiosqlite", reason="MigrationManager.up() needs a real async engine")
    from egpt.integrations.db.sqlite.async_sqlite import AsyncSqliteDb

    db = AsyncSqliteDb(db_file=str(tmp_path / "async.db"))

    await MigrationManager(db).up(table_type="components")
    assert MigrationManager(db)._resolve_tables("components") is None


def test_resolve_tables_skips_only_the_missing_types(sqlite_db):
    """A sync db resolves all ten; a db missing component attrs resolves the other seven."""
    resolved = MigrationManager(sqlite_db)._resolve_tables(None)
    assert len(resolved) == len(MigrationManager._TABLE_TYPE_TO_ATTR)

    class PartialDb:
        session_table_name = "s"
        memory_table_name = "m"

    partial = MigrationManager.__new__(MigrationManager)
    partial.db = PartialDb()
    assert {tt for tt, _ in partial._resolve_tables(None)} == {"sessions", "memories"}
