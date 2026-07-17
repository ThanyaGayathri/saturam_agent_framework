"""Tests for configurable database table prefix (EGPT-0.7)."""

import pytest

from egpt.brand import TABLE_PREFIX
from egpt.db.json.json_db import JsonDb
from egpt.db.sqlite.sqlite import SqliteDb

DEFAULT_TABLE_NAMES = (
    "session_table_name",
    "culture_table_name",
    "memory_table_name",
    "metrics_table_name",
    "eval_table_name",
    "knowledge_table_name",
    "trace_table_name",
    "span_table_name",
    "versions_table_name",
    "components_table_name",
    "component_configs_table_name",
    "component_links_table_name",
    "learnings_table_name",
    "schedules_table_name",
    "schedule_runs_table_name",
    "approvals_table_name",
    "auth_tokens_table_name",
    "service_accounts_table_name",
)

EXPECTED_DEFAULTS = {
    "session_table_name": f"{TABLE_PREFIX}sessions",
    "culture_table_name": f"{TABLE_PREFIX}culture",
    "memory_table_name": f"{TABLE_PREFIX}memories",
    "metrics_table_name": f"{TABLE_PREFIX}metrics",
    "eval_table_name": f"{TABLE_PREFIX}eval_runs",
    "knowledge_table_name": f"{TABLE_PREFIX}knowledge",
    "trace_table_name": f"{TABLE_PREFIX}traces",
    "span_table_name": f"{TABLE_PREFIX}spans",
    "versions_table_name": f"{TABLE_PREFIX}schema_versions",
    "components_table_name": f"{TABLE_PREFIX}components",
    "component_configs_table_name": f"{TABLE_PREFIX}component_configs",
    "component_links_table_name": f"{TABLE_PREFIX}component_links",
    "learnings_table_name": f"{TABLE_PREFIX}learnings",
    "schedules_table_name": f"{TABLE_PREFIX}schedules",
    "schedule_runs_table_name": f"{TABLE_PREFIX}schedule_runs",
    "approvals_table_name": f"{TABLE_PREFIX}approvals",
    "auth_tokens_table_name": f"{TABLE_PREFIX}auth_tokens",
    "service_accounts_table_name": f"{TABLE_PREFIX}service_accounts",
}


class TestTablePrefixDefaults:
    def test_json_db_uses_branded_default_prefix(self, tmp_path):
        db = JsonDb(db_path=str(tmp_path / "json_db"))

        assert db.table_prefix == TABLE_PREFIX
        for attr in DEFAULT_TABLE_NAMES:
            assert getattr(db, attr) == EXPECTED_DEFAULTS[attr]

    def test_sqlite_db_uses_branded_default_prefix(self, tmp_path):
        db = SqliteDb(db_file=str(tmp_path / "test.db"))

        assert db.table_prefix == TABLE_PREFIX
        for attr in DEFAULT_TABLE_NAMES:
            assert getattr(db, attr) == EXPECTED_DEFAULTS[attr]

    def test_custom_table_prefix_applies_to_all_defaults(self, tmp_path):
        db = JsonDb(db_path=str(tmp_path / "json_db"), table_prefix="acme_")

        assert db.table_prefix == "acme_"
        assert db.session_table_name == "acme_sessions"
        assert db.components_table_name == "acme_components"
        assert db.trace_table_name == "acme_traces"

    def test_explicit_table_override_takes_precedence_over_prefix(self, tmp_path):
        db = JsonDb(
            db_path=str(tmp_path / "json_db"),
            table_prefix="acme_",
            session_table="custom_sessions",
        )

        assert db.table_prefix == "acme_"
        assert db.session_table_name == "custom_sessions"
        assert db.memory_table_name == "acme_memories"


class TestTablePrefixSerialization:
    def test_to_dict_includes_table_prefix(self, tmp_path):
        db = JsonDb(db_path=str(tmp_path / "json_db"), table_prefix="acme_")

        data = db.to_dict()

        assert data["table_prefix"] == "acme_"
        assert data["session_table"] == "acme_sessions"

    def test_sqlite_from_dict_round_trips_table_prefix(self, tmp_path):
        db = SqliteDb(db_file=str(tmp_path / "test.db"), table_prefix="acme_")
        restored = SqliteDb.from_dict(db.to_dict())

        assert restored.table_prefix == "acme_"
        assert restored.session_table_name == "acme_sessions"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
