"""Tests for the v2.10.0 traces tenant_id migration (EGPT-4.3a, aicoe-tracker#201).

Covers:
- up() adds tenant_id column + index
- Idempotent rerun (no error on second run)
- Missing table skips cleanly
- down() removes column + index
- Existing rows survive the round trip
"""

import pytest
from sqlalchemy import text

from egpt.integrations.db.sqlite import SqliteDb


def _make_pre_migration_traces_table(db: SqliteDb) -> str:
    """Create a traces table without tenant_id (the pre-EGPT-4.2 shape)."""
    table_name = db.trace_table_name
    with db.Session() as sess, sess.begin():
        sess.execute(
            text(
                f"""CREATE TABLE IF NOT EXISTS {table_name} (
                    trace_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    run_id TEXT,
                    session_id TEXT,
                    user_id TEXT,
                    agent_id TEXT,
                    team_id TEXT,
                    workflow_id TEXT,
                    created_at TEXT NOT NULL
                )"""
            )
        )
    return table_name


def _columns(db: SqliteDb, table_name: str) -> set:
    with db.Session() as sess:
        result = sess.execute(text(f"PRAGMA table_info({table_name})"))
        return {row[1] for row in result}


def _index_names(db: SqliteDb, table_name: str) -> set:
    with db.Session() as sess:
        result = sess.execute(text(f"PRAGMA index_list({table_name})"))
        return {row[1] for row in result}


def _migrate_up(db: SqliteDb):
    from egpt.db.migrations.versions.v2_10_0 import up

    up(db, "traces", db.trace_table_name)


def _migrate_down(db: SqliteDb):
    from egpt.db.migrations.versions.v2_10_0 import down

    down(db, "traces", db.trace_table_name)


@pytest.fixture
def db(tmp_path):
    instance = SqliteDb(db_file=str(tmp_path / "traces_migration_test.db"))
    yield instance
    if instance.db_engine:
        instance.db_engine.dispose()


class TestTracesTenantMigration:
    """v2.10.0 migration: add tenant_id to traces."""

    def test_up_adds_column_and_index(self, db):
        name = _make_pre_migration_traces_table(db)
        assert "tenant_id" not in _columns(db, name)

        _migrate_up(db)

        assert "tenant_id" in _columns(db, name)
        expected_index = f"idx_{name}_tenant_id"
        assert expected_index in _index_names(db, name)

    def test_idempotent_rerun(self, db):
        """Running up() twice does not error."""
        name = _make_pre_migration_traces_table(db)

        _migrate_up(db)
        _migrate_up(db)  # Second run — should not raise

        assert "tenant_id" in _columns(db, name)

    def test_missing_table_skips_cleanly(self, db):
        """up() on a non-existent table returns False, no error."""
        from egpt.db.migrations.versions.v2_10_0 import up

        result = up(db, "traces", "nonexistent_table_xyz")
        assert result is False

    def test_down_removes_column_and_index(self, db):
        name = _make_pre_migration_traces_table(db)
        _migrate_up(db)
        assert "tenant_id" in _columns(db, name)

        _migrate_down(db)
        assert "tenant_id" not in _columns(db, name)
        expected_index = f"idx_{name}_tenant_id"
        assert expected_index not in _index_names(db, name)

    def test_existing_rows_survive_round_trip(self, db):
        """Existing traces survive up() and down() unchanged."""
        name = _make_pre_migration_traces_table(db)
        with db.Session() as sess, sess.begin():
            sess.execute(
                text(
                    f"INSERT INTO {name} (trace_id, name, status, start_time, end_time, "
                    f"duration_ms, created_at) VALUES "
                    f"('t1', 'test', 'OK', '2026-01-01T00:00:00Z', '2026-01-01T00:00:01Z', "
                    f"1000, '2026-01-01T00:00:00Z')"
                )
            )

        _migrate_up(db)

        with db.Session() as sess:
            row = sess.execute(text(f"SELECT trace_id, tenant_id FROM {name}")).fetchone()
        assert row[0] == "t1"
        assert row[1] is None, "existing row should have NULL tenant_id"

        _migrate_down(db)

        with db.Session() as sess:
            row = sess.execute(text(f"SELECT trace_id FROM {name}")).fetchone()
        assert row[0] == "t1"

    def test_non_traces_table_type_skips(self, db):
        """up() with a non-traces table_type returns False."""
        from egpt.db.migrations.versions.v2_10_0 import up

        result = up(db, "memories", db.trace_table_name)
        assert result is False
