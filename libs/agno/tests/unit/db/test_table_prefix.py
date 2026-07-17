"""Tests for configurable database table prefix (EGPT-0.7)."""

import pytest

from egpt.db.in_memory.in_memory_db import InMemoryDb
from egpt.db.json.json_db import JsonDb
from egpt.db.postgres.postgres import PostgresDb
from egpt.db.sqlite.async_sqlite import AsyncSqliteDb
from egpt.db.sqlite.sqlite import SqliteDb
from egpt.db.table_names import (
    DEFAULT_TABLE_PREFIX,
    LEGACY_TABLE_PREFIX,
    TABLE_SUFFIXES,
    table_name_attr,
    validate_table_prefix,
)
from egpt.db.utils import _clone_db_with_table_overrides, resolve_db_from_config

# Literal expectations, intentionally NOT derived from DEFAULT_TABLE_PREFIX:
# if the constant regresses to "agno_" these assertions must fail.
EXPECTED_DEFAULTS = {
    "session_table_name": "egpt_sessions",
    "culture_table_name": "egpt_culture",
    "memory_table_name": "egpt_memories",
    "metrics_table_name": "egpt_metrics",
    "eval_table_name": "egpt_eval_runs",
    "knowledge_table_name": "egpt_knowledge",
    "trace_table_name": "egpt_traces",
    "span_table_name": "egpt_spans",
    "versions_table_name": "egpt_schema_versions",
    "components_table_name": "egpt_components",
    "component_configs_table_name": "egpt_component_configs",
    "component_links_table_name": "egpt_component_links",
    "learnings_table_name": "egpt_learnings",
    "schedules_table_name": "egpt_schedules",
    "schedule_runs_table_name": "egpt_schedule_runs",
    "approvals_table_name": "egpt_approvals",
    "auth_tokens_table_name": "egpt_auth_tokens",
    "service_accounts_table_name": "egpt_service_accounts",
}


class TestTablePrefixConstants:
    def test_default_prefix_literal(self):
        assert DEFAULT_TABLE_PREFIX == "egpt_"
        assert LEGACY_TABLE_PREFIX == "agno_"

    def test_suffix_map_matches_expected_defaults(self):
        derived = {table_name_attr(param): f"egpt_{suffix}" for param, suffix in TABLE_SUFFIXES.items()}
        assert derived == EXPECTED_DEFAULTS


class TestTablePrefixDefaults:
    def test_json_db_uses_branded_default_prefix(self, tmp_path):
        db = JsonDb(db_path=str(tmp_path / "json_db"))

        assert db.table_prefix == "egpt_"
        for attr, expected in EXPECTED_DEFAULTS.items():
            assert getattr(db, attr) == expected

    def test_sqlite_db_uses_branded_default_prefix(self, tmp_path):
        db = SqliteDb(db_file=str(tmp_path / "test.db"))

        assert db.table_prefix == "egpt_"
        for attr, expected in EXPECTED_DEFAULTS.items():
            assert getattr(db, attr) == expected

    def test_custom_table_prefix_applies_to_all_defaults(self, tmp_path):
        db = SqliteDb(db_file=str(tmp_path / "test.db"), table_prefix="acme_")

        assert db.table_prefix == "acme_"
        for param, suffix in TABLE_SUFFIXES.items():
            assert getattr(db, table_name_attr(param)) == f"acme_{suffix}"

    def test_explicit_table_override_takes_precedence_over_prefix(self, tmp_path):
        db = JsonDb(
            db_path=str(tmp_path / "json_db"),
            table_prefix="acme_",
            session_table="custom_sessions",
        )

        assert db.table_prefix == "acme_"
        assert db.session_table_name == "custom_sessions"
        assert db.memory_table_name == "acme_memories"

    def test_in_memory_db_accepts_table_prefix(self):
        db = InMemoryDb(table_prefix="acme_")

        assert db.session_table_name == "acme_sessions"

        restored = InMemoryDb.from_dict(db.to_dict())
        assert restored.table_prefix == "acme_"
        assert restored.session_table_name == "acme_sessions"


class TestTablePrefixValidation:
    def test_none_resolves_to_default(self):
        assert validate_table_prefix(None) == "egpt_"

    def test_empty_prefix_means_unprefixed(self, tmp_path):
        db = SqliteDb(db_file=str(tmp_path / "test.db"), table_prefix="")

        assert db.table_prefix == ""
        assert db.session_table_name == "sessions"
        assert db.memory_table_name == "memories"

    @pytest.mark.parametrize(
        "bad_prefix",
        ["BAD_", "1abc_", "has space_", "has-dash_", "ACME_", "a" * 33],
    )
    def test_invalid_prefix_raises(self, tmp_path, bad_prefix):
        with pytest.raises(ValueError):
            SqliteDb(db_file=str(tmp_path / "test.db"), table_prefix=bad_prefix)


class TestTablePrefixSerialization:
    def test_to_dict_is_sparse_for_default_names(self, tmp_path):
        db = SqliteDb(db_file=str(tmp_path / "test.db"), table_prefix="acme_")

        data = db.to_dict()

        assert data["table_prefix"] == "acme_"
        # Default-derived names are not serialized: they re-derive on reload.
        for param in TABLE_SUFFIXES:
            assert param not in data

    def test_to_dict_serializes_only_non_default_names(self, tmp_path):
        db = SqliteDb(
            db_file=str(tmp_path / "test.db"),
            table_prefix="acme_",
            session_table="custom_sessions",
        )

        data = db.to_dict()

        assert data["session_table"] == "custom_sessions"
        assert "memory_table" not in data

    def test_sqlite_from_dict_round_trips_table_prefix(self, tmp_path):
        db = SqliteDb(db_file=str(tmp_path / "test.db"), table_prefix="acme_")
        restored = SqliteDb.from_dict(db.to_dict())

        assert restored.table_prefix == "acme_"
        assert restored.session_table_name == "acme_sessions"

    def test_postgres_from_dict_round_trips_table_prefix(self):
        from unittest.mock import MagicMock, patch

        mock_engine = MagicMock()
        mock_engine.url = "postgresql://localhost/test"
        with patch("egpt.db.postgres.postgres.create_engine", return_value=mock_engine):
            db = PostgresDb(db_url="postgresql://localhost/test", table_prefix="acme_")
            restored = PostgresDb.from_dict(db.to_dict())

        assert restored.table_prefix == "acme_"
        assert restored.session_table_name == "acme_sessions"
        assert restored.auth_tokens_table_name == "acme_auth_tokens"

    def test_from_dict_restores_auth_tokens_table(self, tmp_path):
        """Regression: from_dict used to drop auth_tokens_table, silently
        re-deriving it from the (new) prefix while every other table kept its
        stored name — breaking token verification after upgrade."""
        pre_rename_config = {
            "type": "sqlite",
            "db_file": str(tmp_path / "test.db"),
            "session_table": "agno_sessions",
            "auth_tokens_table": "agno_auth_tokens",
        }

        restored = SqliteDb.from_dict(pre_rename_config)

        assert restored.session_table_name == "agno_sessions"
        assert restored.auth_tokens_table_name == "agno_auth_tokens"

    def test_editing_prefix_in_stored_config_takes_effect(self, tmp_path):
        """With sparse serialization the table_prefix key is not inert:
        editing it in a stored config changes the derived names on reload."""
        db = SqliteDb(db_file=str(tmp_path / "test.db"))
        data = db.to_dict()
        data["table_prefix"] = "acme_"

        restored = SqliteDb.from_dict(data)

        assert restored.session_table_name == "acme_sessions"


class TestAsyncTablePrefix:
    def test_async_sqlite_uses_branded_default_prefix(self, tmp_path):
        db = AsyncSqliteDb(db_file=str(tmp_path / "test.db"))

        assert db.table_prefix == "egpt_"
        assert db.session_table_name == "egpt_sessions"
        assert db.memory_table_name == "egpt_memories"
        assert db.knowledge_table_name == "egpt_knowledge"

    def test_async_sqlite_custom_prefix_and_override(self, tmp_path):
        db = AsyncSqliteDb(
            db_file=str(tmp_path / "test.db"),
            table_prefix="acme_",
            session_table="custom_sessions",
        )

        assert db.table_prefix == "acme_"
        assert db.session_table_name == "custom_sessions"
        assert db.memory_table_name == "acme_memories"

    def test_async_sqlite_rejects_invalid_prefix(self, tmp_path):
        with pytest.raises(ValueError):
            AsyncSqliteDb(db_file=str(tmp_path / "test.db"), table_prefix="Not Valid")


class _FakeRegistry:
    def __init__(self, db):
        self._db = db

    def get_db(self, db_id):
        if self._db is not None and getattr(self._db, "id", None) == db_id:
            return self._db
        return None


class TestResolveDbFromConfigWithPrefix:
    def test_clone_applies_table_prefix_override(self, tmp_path):
        src = SqliteDb(db_file=str(tmp_path / "src.db"))

        clone = _clone_db_with_table_overrides(src, {"table_prefix": "acme_"})

        assert clone is not None
        assert clone.table_prefix == "acme_"
        assert clone.session_table_name == "acme_sessions"
        # Engine is shared, not rebuilt.
        assert clone.db_engine is src.db_engine

    def test_differing_prefix_triggers_clone(self, tmp_path):
        os_db = SqliteDb(db_file=str(tmp_path / "os.db"))
        registry = _FakeRegistry(os_db)

        # Config references the registered db by id but requests a new prefix
        # and carries no concrete per-table names, so the prefix must win.
        data = {"id": os_db.id, "type": "sqlite", "table_prefix": "acme_"}

        resolved = resolve_db_from_config(data, registry=registry)

        assert resolved is not os_db
        assert resolved.table_prefix == "acme_"
        assert resolved.session_table_name == "acme_sessions"
        assert resolved.db_engine is os_db.db_engine

    def test_identical_prefix_returns_registered_instance(self, tmp_path):
        os_db = SqliteDb(db_file=str(tmp_path / "os.db"))
        registry = _FakeRegistry(os_db)

        resolved = resolve_db_from_config(os_db.to_dict(), registry=registry)

        assert resolved is os_db


class TestLegacyConfigEquivalence:
    """Pre-rename stored configs serialized the old agno_* defaults for every
    table. Those snapshots must not be misread as explicit overrides."""

    def _pre_rename_config(self, os_db):
        """Simulate a config serialized before the rename: every table name
        baked in as the old agno_* default, no table_prefix key."""
        config = {"type": "sqlite", "id": os_db.id, "db_file": os_db.db_file}
        legacy_names = {
            "session_table": "agno_sessions",
            "culture_table": "agno_culture",
            "memory_table": "agno_memories",
            "metrics_table": "agno_metrics",
            "eval_table": "agno_eval_runs",
            "knowledge_table": "agno_knowledge",
            "traces_table": "agno_traces",
            "spans_table": "agno_spans",
            "versions_table": "agno_schema_versions",
            "components_table": "agno_components",
            "component_configs_table": "agno_component_configs",
            "component_links_table": "agno_component_links",
            "learnings_table": "agno_learnings",
            "schedules_table": "agno_schedules",
            "schedule_runs_table": "agno_schedule_runs",
            "approvals_table": "agno_approvals",
            "auth_tokens_table": "agno_auth_tokens",
            "service_accounts_table": "agno_service_accounts",
        }
        config.update(legacy_names)
        return config

    def test_legacy_defaults_resolve_to_registered_instance(self, tmp_path):
        """The registered db must be reused as-is: no clone pinned to agno_*
        tables (split-brain) and no per-request clone churn."""
        os_db = SqliteDb(db_file=str(tmp_path / "os.db"))
        registry = _FakeRegistry(os_db)

        resolved = resolve_db_from_config(self._pre_rename_config(os_db), registry=registry)

        assert resolved is os_db

    def test_legacy_defaults_plus_genuine_override_clones_only_the_override(self, tmp_path):
        os_db = SqliteDb(db_file=str(tmp_path / "os.db"))
        registry = _FakeRegistry(os_db)
        config = self._pre_rename_config(os_db)
        config["session_table"] = "my_custom_sessions"

        resolved = resolve_db_from_config(config, registry=registry)

        assert resolved is not os_db
        assert resolved.session_table_name == "my_custom_sessions"
        # The legacy default names were NOT pinned onto the clone.
        assert resolved.memory_table_name == "egpt_memories"
        assert resolved.db_engine is os_db.db_engine

    def test_explicit_legacy_prefix_is_honored(self, tmp_path):
        """table_prefix="agno_" is the escape hatch and must remain a real
        override, re-deriving every default from the legacy prefix."""
        os_db = SqliteDb(db_file=str(tmp_path / "os.db"))
        registry = _FakeRegistry(os_db)
        config = {"type": "sqlite", "id": os_db.id, "table_prefix": "agno_"}

        resolved = resolve_db_from_config(config, registry=registry)

        assert resolved is not os_db
        assert resolved.table_prefix == "agno_"
        assert resolved.session_table_name == "agno_sessions"


class TestLegacyTableWarning:
    def test_warns_when_agno_sibling_exists(self, tmp_path, caplog):
        db = SqliteDb(db_file=str(tmp_path / "test.db"))

        # Provision the legacy sibling table directly.
        from sqlalchemy import text

        with db.db_engine.connect() as conn:
            conn.execute(text("CREATE TABLE agno_sessions (id TEXT)"))
            conn.commit()

        db._warn_if_legacy_table_exists("egpt_sessions")

        assert any("agno_sessions" in message for message in caplog.messages)

    def test_no_warning_without_legacy_sibling(self, tmp_path, caplog):
        db = SqliteDb(db_file=str(tmp_path / "test.db"))

        db._warn_if_legacy_table_exists("egpt_sessions")

        assert not any("agno_sessions" in message for message in caplog.messages)

    def test_no_warning_for_custom_prefix(self, tmp_path, caplog):
        db = SqliteDb(db_file=str(tmp_path / "test.db"), table_prefix="acme_")

        db._warn_if_legacy_table_exists("acme_sessions")

        assert not caplog.messages


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
