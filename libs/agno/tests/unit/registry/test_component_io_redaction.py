"""Secrets must not travel in an exported definition (EGPT-2.8).

The concrete hazard: ``PostgresDb.to_dict`` emits ``db_url``, ``Agent.to_dict`` embeds the db
dict, so a stored agent config can hold ``postgresql+psycopg://ai:hunter2@host/ai``. Exporting
that verbatim would put a production password into a file whose whole purpose is to be moved
around and checked in.

Two layers are tested here and they fail differently on purpose:

* the **structural** rules *drop* connection keys from the ``db`` block, because a sentinel
  ``db_url`` would be handed to a driver and produce a confusing connection error where an
  absent one produces a clean "unresolvable db";
* the **generic** scan *replaces* credential-shaped values with a sentinel, because the key is
  one we did not anticipate and its presence is information the operator wants.

The last test is the one that makes redaction usable rather than merely safe: a redacted config
still imports and still hydrates, as long as the target knows a database by that id.
"""

import pytest

from egpt.registry.io import REDACTED, redact_config

DSN = "postgresql+psycopg://ai:hunter2@db.internal:5432/ai"


class TestTheDbBlock:
    def test_a_postgres_dsn_is_dropped_and_reported(self):
        config = {"id": "a", "db": {"id": "os-db", "type": "postgres", "db_url": DSN, "db_schema": "ai"}}

        result, redacted = redact_config(config)

        assert "db_url" not in result["db"]
        assert redacted == ["db.db_url"]

    def test_what_survives_is_what_lets_the_config_still_resolve(self):
        config = {
            "db": {
                "id": "os-db",
                "type": "postgres",
                "db_url": DSN,
                "db_schema": "ai",
                "table_prefix": "egpt_",
                "session_table": "custom_sessions",
            }
        }

        result, _redacted = redact_config(config)

        assert result["db"] == {
            "id": "os-db",
            "type": "postgres",
            "db_schema": "ai",
            "table_prefix": "egpt_",
            "session_table": "custom_sessions",
        }

    def test_a_sqlite_file_path_is_dropped(self):
        config = {"db": {"id": "os-db", "type": "sqlite", "db_file": "/srv/production/egpt.db"}}

        result, redacted = redact_config(config)

        assert "db_file" not in result["db"]
        assert redacted == ["db.db_file"]

    def test_clickhouse_host_and_password_are_dropped(self):
        config = {"db": {"id": "ch", "type": "clickhouse", "host": "clickhouse.internal", "password": "hunter2"}}

        result, redacted = redact_config(config)

        assert result["db"] == {"id": "ch", "type": "clickhouse"}
        assert sorted(redacted) == ["db.host", "db.password"]

    def test_a_null_connection_field_is_not_reported_as_a_secret(self):
        """Reporting `db_url: None` as redacted would make the list noise rather than a signal."""
        config = {"db": {"id": "os-db", "type": "sqlite", "db_url": None}}

        _result, redacted = redact_config(config)

        assert redacted == []

    def test_a_config_without_a_db_block_is_untouched(self):
        config = {"id": "a", "model": {"id": "gpt-5.5"}}

        result, redacted = redact_config(config)

        assert result == config
        assert redacted == []


class TestTheGenericScan:
    def test_a_credential_key_nested_deep_is_replaced(self):
        config = {"tools": [{"name": "search", "settings": {"api_key": "sk-live-123"}}]}

        result, redacted = redact_config(config)

        assert result["tools"][0]["settings"]["api_key"] == REDACTED
        assert redacted == ["tools[0].settings.api_key"]

    def test_an_authorization_header_is_replaced(self):
        config = {"mcp": {"headers": {"Authorization": "Bearer abc123"}}}

        result, redacted = redact_config(config)

        assert result["mcp"]["headers"]["Authorization"] == REDACTED

    def test_a_dsn_parked_under_an_unremarkable_key_is_caught_by_value(self):
        """The key is one nobody anticipated; the *value* is unmistakable."""
        config = {"metadata": {"backup_target": DSN}}

        result, redacted = redact_config(config)

        assert result["metadata"]["backup_target"] == REDACTED
        assert redacted == ["metadata.backup_target"]

    @pytest.mark.parametrize("key", ["max_tokens", "tokens", "keystore_path", "secretary_agent_id"])
    def test_ordinary_settings_that_merely_contain_a_secret_word_survive(self, key):
        """The separator anchoring is the whole point of the pattern.

        A plain substring match on "token" redacts `max_tokens` -- a real model setting -- and
        the corpus quietly loses configuration on every export. Regressions here are silent, so
        they get their own parametrized case.
        """
        config = {key: 4096}

        result, redacted = redact_config(config)

        assert result[key] == 4096
        assert redacted == []

    @pytest.mark.parametrize("key", ["aws_access_key_id", "aws_secret_access_key", "auth_token", "api_key"])
    def test_credential_keys_are_caught_wherever_the_word_sits(self, key):
        """Why the pattern is not simply anchored to the end of the key.

        `aws_access_key_id` is a real credential field whose secret word is in the *middle*.
        Tail-anchoring would read more conservatively and miss it, so the pattern accepts some
        over-redaction instead -- which is safe here precisely because every removal is listed
        in the document's `redacted` field rather than happening silently.
        """
        config = {"tools": [{"settings": {key: "value"}}]}

        result, redacted = redact_config(config)

        assert result["tools"][0]["settings"][key] == REDACTED
        assert redacted == [f"tools[0].settings.{key}"]

    def test_a_url_without_credentials_survives(self):
        config = {"metadata": {"docs": "https://example.com/handbook"}}

        result, redacted = redact_config(config)

        assert result["metadata"]["docs"] == "https://example.com/handbook"
        assert redacted == []


class TestExportAppliesIt:
    def test_export_redacts_by_default_and_records_the_paths(self, component_db):
        from egpt.db.base import ComponentType
        from egpt.registry.io import export_component

        component_db.create_component_with_config(
            component_id="leaky-agent",
            component_type=ComponentType.AGENT,
            name="Leaky Agent",
            config={
                "id": "leaky-agent",
                "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
                "db": {"id": "os-db", "type": "postgres", "db_url": DSN},
            },
            stage="published",
        )

        document = export_component(component_db, "leaky-agent")

        assert document["redacted"] == ["components[0].config.db.db_url"]
        assert "db_url" not in document["components"][0]["config"]["db"]
        assert "contains_secrets" not in document

    def test_include_secrets_keeps_them_and_says_so(self, component_db):
        from egpt.db.base import ComponentType
        from egpt.registry.io import export_component

        component_db.create_component_with_config(
            component_id="leaky-agent",
            component_type=ComponentType.AGENT,
            name="Leaky Agent",
            config={
                "id": "leaky-agent",
                "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
                "db": {"id": "os-db", "type": "postgres", "db_url": DSN},
            },
            stage="published",
        )

        document = export_component(component_db, "leaky-agent", include_secrets=True)

        assert document["components"][0]["config"]["db"]["db_url"] == DSN
        # Explicit, so a CI check or a git hook can refuse the file rather than having to infer
        # safety from an empty `redacted` list.
        assert document["contains_secrets"] is True


class TestARedactedConfigStillWorks:
    def test_it_imports_and_hydrates_when_the_target_knows_the_db_id(self, component_db, target_db):
        """Redaction is only acceptable because the definition survives it.

        The db block keeps its `id`, and `resolve_db_reference` merges the *target's* connection
        details back in at import time. Nothing about the source's database travels.
        """
        from egpt.agent import get_agent_by_id
        from egpt.components.io import import_component
        from egpt.db.base import ComponentType
        from egpt.registry import Registry
        from egpt.registry.io import export_component

        component_db.create_component_with_config(
            component_id="leaky-agent",
            component_type=ComponentType.AGENT,
            name="Leaky Agent",
            config={
                "id": "leaky-agent",
                "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
                "db": {"id": "source-db", "type": "sqlite", "db_file": "/tmp/source.db"},
            },
            stage="published",
        )
        document = export_component(component_db, "leaky-agent")

        registry = Registry()
        registry.add_db(target_db)
        import_component(target_db, document, tenant_id="acme", registry=registry, db_id="target-db")

        agent = get_agent_by_id(target_db, "leaky-agent", registry=registry, tenant_id="acme")

        assert agent is not None
        assert agent.db is target_db

    def test_an_unresolvable_db_id_is_rejected_at_import_rather_than_stored(self, target_db):
        """The failure this closes: store fine, then silently lose the db at hydration time."""
        from egpt.components.io import import_component
        from egpt.registry import Registry
        from egpt.registry.io import EXPORT_FORMAT
        from egpt.registry.schema import ConfigValidationError

        document = {
            "format": EXPORT_FORMAT,
            "format_version": 1,
            "components": [
                {
                    "component_id": "orphan-agent",
                    "component_type": "agent",
                    "config": {
                        "id": "orphan-agent",
                        "model": {"id": "gpt-5.5", "name": "OpenAIResponses", "provider": "OpenAI"},
                        "db": {"id": "a-db-that-does-not-exist-here"},
                    },
                    "links": [],
                }
            ],
        }

        registry = Registry()
        registry.add_db(target_db)

        with pytest.raises(ConfigValidationError) as excinfo:
            import_component(target_db, document, tenant_id="acme", registry=registry)

        assert any(error.path == "db.id" for error in excinfo.value.errors)
        assert target_db.get_component("orphan-agent", tenant_id="acme") is None
