"""
Unit tests for the Components router.

Tests cover:
- GET /components - List components
- POST /components - Create component
- GET /components/{component_id} - Get component
- PATCH /components/{component_id} - Update component
- DELETE /components/{component_id} - Delete component
- GET /components/{component_id}/configs - List configs
- POST /components/{component_id}/configs - Create config
- GET /components/{component_id}/configs/current - Get current config
- GET /components/{component_id}/configs/{version} - Get config version
- PATCH /components/{component_id}/configs/{version} - Update config
- DELETE /components/{component_id}/configs/{version} - Delete config
- POST /components/{component_id}/configs/{version}/set-current - Set current version
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egpt.db.base import BaseDb, ComponentType
from egpt.os.routers.components import get_components_router
from egpt.os.settings import AgnoAPISettings

# Since EGPT-2.2 a non-empty config is validated before it is persisted, and an agent
# definition has to name a model. Tests that only care about the routing use this.
VALID_AGENT_CONFIG = {"model": "openai:gpt-5.5"}

# =============================================================================
# Fixtures
# =============================================================================


def _create_mock_db_class():
    """Create a concrete BaseDb subclass with all abstract methods stubbed."""
    abstract_methods = {}
    for name in dir(BaseDb):
        attr = getattr(BaseDb, name, None)
        if getattr(attr, "__isabstractmethod__", False):
            abstract_methods[name] = MagicMock()
    return type("MockDb", (BaseDb,), abstract_methods)


@pytest.fixture
def mock_db():
    """Create a mock database instance."""
    MockDbClass = _create_mock_db_class()
    db = MockDbClass()
    db.id = "test-db"
    db.list_components = MagicMock()
    # Defaults to an existing agent: the config endpoints look the component up to find out
    # which schema to validate against. Tests that care about the lookup override it.
    db.get_component = MagicMock(
        return_value={
            "component_id": "agent-1",
            "component_type": "agent",
            "name": "Agent",
            "created_at": 1234567890,
        }
    )
    db.upsert_component = MagicMock()
    db.delete_component = MagicMock()
    db.create_component_with_config = MagicMock()
    db.list_configs = MagicMock()
    db.get_config = MagicMock()
    db.upsert_config = MagicMock()
    db.delete_config = MagicMock()
    db.set_current_version = MagicMock()
    db.to_dict = MagicMock(return_value={"type": "postgres", "id": "test-db"})
    return db


@pytest.fixture
def settings():
    """Create test settings with auth disabled (no security key = auth disabled)."""
    return AgnoAPISettings()


@pytest.fixture
def client(mock_db, settings):
    """Create a FastAPI test client with the components router."""
    app = FastAPI()
    router = get_components_router(os_db=mock_db, settings=settings)
    app.include_router(router)
    return TestClient(app)


# =============================================================================
# List Components Tests
# =============================================================================


class TestListComponents:
    """Tests for GET /components endpoint."""

    def test_list_components_returns_paginated_response(self, client, mock_db):
        """Test list_components returns paginated response."""
        mock_db.list_components.return_value = (
            [
                {"component_id": "agent-1", "name": "Agent 1", "component_type": "agent", "created_at": 1234567890},
                {"component_id": "agent-2", "name": "Agent 2", "component_type": "agent", "created_at": 1234567890},
            ],
            2,
        )

        response = client.get("/components")

        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 2
        assert data["meta"]["total_count"] == 2
        assert data["meta"]["page"] == 1

    def test_list_components_with_type_filter(self, client, mock_db):
        """Test list_components filters by component type."""
        mock_db.list_components.return_value = ([], 0)

        response = client.get("/components?component_type=agent")

        assert response.status_code == 200
        mock_db.list_components.assert_called_once()
        call_args = mock_db.list_components.call_args
        assert call_args.kwargs["component_type"] == ComponentType.AGENT

    def test_list_components_with_pagination(self, client, mock_db):
        """Test list_components with pagination parameters."""
        mock_db.list_components.return_value = ([], 100)

        response = client.get("/components?page=3&limit=10")

        assert response.status_code == 200
        mock_db.list_components.assert_called_once()
        call_args = mock_db.list_components.call_args
        assert call_args.kwargs["limit"] == 10
        assert call_args.kwargs["offset"] == 20  # (3-1) * 10

    def test_list_components_handles_error(self, client, mock_db):
        """Test list_components returns 500 on error."""
        mock_db.list_components.side_effect = Exception("DB error")

        response = client.get("/components")

        assert response.status_code == 500


# =============================================================================
# Create Component Tests
# =============================================================================


class TestCreateComponent:
    """Tests for POST /components endpoint."""

    def test_create_component_success(self, client, mock_db):
        """Test create_component creates a new component."""
        mock_db.create_component_with_config.return_value = (
            {
                "component_id": "test-agent",
                "name": "Test Agent",
                "component_type": "agent",
                "created_at": 1234567890,
            },
            {"version": 1},
        )

        response = client.post(
            "/components",
            json={
                "name": "Test Agent",
                "component_type": "agent",
                "config": {"id": "test-agent", **VALID_AGENT_CONFIG},
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["component_id"] == "test-agent"
        assert data["name"] == "Test Agent"

    def test_create_component_generates_id_from_name(self, client, mock_db):
        """Test create_component generates ID from name if not provided."""
        mock_db.create_component_with_config.return_value = (
            {"component_id": "my-agent", "name": "My Agent", "component_type": "agent", "created_at": 1234567890},
            {"version": 1},
        )

        response = client.post(
            "/components",
            json={"name": "My Agent", "component_type": "agent"},
        )

        assert response.status_code == 201
        # Verify that component_id was generated (checked in the call)
        call_args = mock_db.create_component_with_config.call_args
        assert call_args.kwargs["component_id"] == "my-agent"

    def test_create_component_with_explicit_id(self, client, mock_db):
        """Test create_component uses provided component_id."""
        mock_db.create_component_with_config.return_value = (
            {"component_id": "custom-id", "name": "Test", "component_type": "agent", "created_at": 1234567890},
            {"version": 1},
        )

        response = client.post(
            "/components",
            json={
                "name": "Test",
                "component_type": "agent",
                "component_id": "custom-id",
            },
        )

        assert response.status_code == 201
        call_args = mock_db.create_component_with_config.call_args
        assert call_args.kwargs["component_id"] == "custom-id"

    def test_create_component_handles_value_error(self, client, mock_db):
        """Test create_component returns 400 on ValueError."""
        mock_db.create_component_with_config.side_effect = ValueError("Invalid config")

        response = client.post(
            "/components",
            json={"name": "Test", "component_type": "agent"},
        )

        assert response.status_code == 400


# =============================================================================
# Get Component Tests
# =============================================================================


class TestGetComponent:
    """Tests for GET /components/{component_id} endpoint."""

    def test_get_component_success(self, client, mock_db):
        """Test get_component returns component."""
        mock_db.get_component.return_value = {
            "component_id": "agent-1",
            "name": "Agent 1",
            "component_type": "agent",
            "created_at": 1234567890,
        }

        response = client.get("/components/agent-1")

        assert response.status_code == 200
        data = response.json()
        assert data["component_id"] == "agent-1"

    def test_get_component_not_found(self, client, mock_db):
        """Test get_component returns 404 when not found."""
        mock_db.get_component.return_value = None

        response = client.get("/components/nonexistent")

        assert response.status_code == 404


# =============================================================================
# Update Component Tests
# =============================================================================


class TestUpdateComponent:
    """Tests for PATCH /components/{component_id} endpoint."""

    def test_update_component_success(self, client, mock_db):
        """Test update_component updates component."""
        mock_db.get_component.return_value = {
            "component_id": "agent-1",
            "name": "Old Name",
            "component_type": "agent",
            "created_at": 1234567890,
        }
        mock_db.upsert_component.return_value = {
            "component_id": "agent-1",
            "name": "New Name",
            "component_type": "agent",
            "created_at": 1234567890,
        }

        response = client.patch("/components/agent-1", json={"name": "New Name"})

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Name"

    def test_update_component_not_found(self, client, mock_db):
        """Test update_component returns 404 when not found."""
        mock_db.get_component.return_value = None

        response = client.patch("/components/nonexistent", json={"name": "New Name"})

        assert response.status_code == 404


# =============================================================================
# Delete Component Tests
# =============================================================================


class TestDeleteComponent:
    """Tests for DELETE /components/{component_id} endpoint."""

    def test_delete_component_success(self, client, mock_db):
        """Test delete_component deletes component."""
        mock_db.delete_component.return_value = True

        response = client.delete("/components/agent-1")

        assert response.status_code == 204

    def test_delete_component_not_found(self, client, mock_db):
        """Test delete_component returns 404 when not found."""
        mock_db.delete_component.return_value = False

        response = client.delete("/components/nonexistent")

        assert response.status_code == 404


# =============================================================================
# List Configs Tests
# =============================================================================


class TestListConfigs:
    """Tests for GET /components/{component_id}/configs endpoint."""

    def test_list_configs_success(self, client, mock_db):
        """Test list_configs returns list of configs."""
        mock_db.list_configs.return_value = [
            {"component_id": "agent-1", "version": 1, "stage": "draft", "config": {}, "created_at": 1234567890},
            {"component_id": "agent-1", "version": 2, "stage": "published", "config": {}, "created_at": 1234567890},
        ]

        response = client.get("/components/agent-1/configs")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_list_configs_with_include_config(self, client, mock_db):
        """Test list_configs passes include_config parameter."""
        mock_db.list_configs.return_value = []

        response = client.get("/components/agent-1/configs?include_config=false")

        assert response.status_code == 200
        # tenant_id=None is what an unauthenticated caller resolves to (EGPT-2.3); per the
        # BaseDb contract it applies no tenant predicate, so the query is unchanged.
        mock_db.list_configs.assert_called_once_with("agent-1", include_config=False, tenant_id=None)


# =============================================================================
# Create Config Tests
# =============================================================================


class TestCreateConfig:
    """Tests for POST /components/{component_id}/configs endpoint."""

    def test_create_config_success(self, client, mock_db):
        """Test create_config creates new config version."""
        mock_db.upsert_config.return_value = {
            "component_id": "agent-1",
            "version": 1,
            "config": {"name": "Agent"},
            "stage": "draft",
            "created_at": 1234567890,
        }

        response = client.post(
            "/components/agent-1/configs",
            json={"config": {"name": "Agent", **VALID_AGENT_CONFIG}, "stage": "draft"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["version"] == 1

    def test_create_config_handles_value_error(self, client, mock_db):
        """Test create_config returns 400 on ValueError."""
        mock_db.upsert_config.side_effect = ValueError("Invalid config")

        response = client.post(
            "/components/agent-1/configs",
            json={"config": {}},
        )

        assert response.status_code == 400


# =============================================================================
# Get Current Config Tests
# =============================================================================


class TestGetCurrentConfig:
    """Tests for GET /components/{component_id}/configs/current endpoint."""

    def test_get_current_config_success(self, client, mock_db):
        """Test get_current_config returns current config."""
        mock_db.get_config.return_value = {
            "component_id": "agent-1",
            "version": 2,
            "config": {"name": "Agent"},
            "stage": "published",
            "created_at": 1234567890,
        }

        response = client.get("/components/agent-1/configs/current")

        assert response.status_code == 200
        data = response.json()
        assert data["version"] == 2

    def test_get_current_config_not_found(self, client, mock_db):
        """Test get_current_config returns 404 when no current config."""
        mock_db.get_config.return_value = None

        response = client.get("/components/agent-1/configs/current")

        assert response.status_code == 404


# =============================================================================
# Get Config Version Tests
# =============================================================================


class TestGetConfigVersion:
    """Tests for GET /components/{component_id}/configs/{version} endpoint."""

    def test_get_config_version_success(self, client, mock_db):
        """Test get_config_version returns specific version."""
        mock_db.get_config.return_value = {
            "component_id": "agent-1",
            "version": 3,
            "config": {"name": "Agent v3"},
            "stage": "published",
            "created_at": 1234567890,
        }

        response = client.get("/components/agent-1/configs/3")

        assert response.status_code == 200
        data = response.json()
        assert data["version"] == 3

    def test_get_config_version_not_found(self, client, mock_db):
        """Test get_config_version returns 404 when version not found."""
        mock_db.get_config.return_value = None

        response = client.get("/components/agent-1/configs/999")

        assert response.status_code == 404


# =============================================================================
# Update Config Tests
# =============================================================================


class TestUpdateConfig:
    """Tests for PATCH /components/{component_id}/configs/{version} endpoint."""

    def test_update_config_success(self, client, mock_db):
        """Test update_config updates config version."""
        mock_db.upsert_config.return_value = {
            "component_id": "agent-1",
            "version": 1,
            "config": {"name": "Updated Agent"},
            "stage": "draft",
            "created_at": 1234567890,
        }

        response = client.patch(
            "/components/agent-1/configs/1",
            json={"config": {"name": "Updated Agent", **VALID_AGENT_CONFIG}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["config"]["name"] == "Updated Agent"

    def test_update_config_handles_value_error(self, client, mock_db):
        """Test update_config returns 400 on ValueError."""
        mock_db.upsert_config.side_effect = ValueError("Cannot update published config")

        response = client.patch(
            "/components/agent-1/configs/1",
            json={"stage": "published"},
        )

        assert response.status_code == 400


# =============================================================================
# Delete Config Tests
# =============================================================================


class TestDeleteConfig:
    """Tests for DELETE /components/{component_id}/configs/{version} endpoint."""

    def test_delete_config_success(self, client, mock_db):
        """Test delete_config deletes config version."""
        mock_db.delete_config.return_value = True

        response = client.delete("/components/agent-1/configs/1")

        assert response.status_code == 204

    def test_delete_config_not_found(self, client, mock_db):
        """Test delete_config returns 404 when not found."""
        mock_db.delete_config.return_value = False

        response = client.delete("/components/agent-1/configs/999")

        assert response.status_code == 404

    def test_delete_config_handles_value_error(self, client, mock_db):
        """Test delete_config returns 400 on ValueError."""
        mock_db.delete_config.side_effect = ValueError("Cannot delete current config")

        response = client.delete("/components/agent-1/configs/1")

        assert response.status_code == 400


# =============================================================================
# Set Current Config Tests
# =============================================================================


class TestSetCurrentConfig:
    """Tests for POST /components/{component_id}/configs/{version}/set-current endpoint."""

    def test_set_current_config_success(self, client, mock_db):
        """Test set_current_config sets version as current."""
        mock_db.set_current_version.return_value = True
        mock_db.get_component.return_value = {
            "component_id": "agent-1",
            "name": "Agent 1",
            "component_type": "agent",
            "current_version": 3,
            "created_at": 1234567890,
        }

        response = client.post("/components/agent-1/configs/3/set-current")

        assert response.status_code == 200
        data = response.json()
        assert data["current_version"] == 3

    def test_set_current_config_not_found(self, client, mock_db):
        """Test set_current_config returns 404 when version not found."""
        mock_db.set_current_version.return_value = False

        response = client.post("/components/agent-1/configs/999/set-current")

        assert response.status_code == 404

    def test_set_current_config_handles_value_error(self, client, mock_db):
        """Test set_current_config returns 400 on ValueError."""
        mock_db.set_current_version.side_effect = ValueError("Version not published")

        response = client.post("/components/agent-1/configs/1/set-current")

        assert response.status_code == 400


# =============================================================================
# Config Validation Tests (EGPT-2.2)
# =============================================================================


class TestConfigValidation:
    """Semantic validation of the config blob on every write path."""

    def test_create_component_rejects_config_without_model(self, client, mock_db):
        response = client.post(
            "/components",
            json={"name": "Test", "component_type": "agent", "config": {"instructions": "be nice"}},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error_code"] == "CONFIG_VALIDATION_ERROR"
        assert body["schema_version"] == 1
        assert body["errors"] == [
            {
                "path": "config.model",
                "message": "Field 'model' is required for an agent definition.",
                "code": "missing_required_field",
            }
        ]
        mock_db.create_component_with_config.assert_not_called()

    def test_create_component_reports_every_error_at_once(self, client, mock_db):
        response = client.post(
            "/components",
            json={
                "name": "Test",
                "component_type": "agent",
                "config": {"model": {"id": "gpt-5.5", "provider": "nope"}, "instruction": "typo"},
            },
        )

        assert response.status_code == 422
        errors = {e["path"]: e["code"] for e in response.json()["errors"]}
        assert errors == {"config.instruction": "unknown_field", "config.model.provider": "unresolvable_ref"}

    def test_unknown_field_suggests_the_intended_one(self, client, mock_db):
        response = client.post(
            "/components",
            json={"name": "Test", "component_type": "agent", "config": {**VALID_AGENT_CONFIG, "instruction": "hi"}},
        )

        assert response.status_code == 422
        assert "Did you mean 'instructions'?" in response.json()["errors"][0]["message"]

    def test_valid_config_is_persisted_with_a_schema_version(self, client, mock_db):
        mock_db.create_component_with_config.return_value = (
            {"component_id": "a", "name": "A", "component_type": "agent", "created_at": 1},
            {"version": 1},
        )

        response = client.post(
            "/components",
            json={"name": "A", "component_type": "agent", "config": dict(VALID_AGENT_CONFIG)},
        )

        assert response.status_code == 201
        persisted = mock_db.create_component_with_config.call_args.kwargs["config"]
        assert persisted == {**VALID_AGENT_CONFIG, "schema_version": 1}

    def test_empty_config_creates_a_shell_component(self, client, mock_db):
        """A component with no config yet is a legitimate flow, so there is nothing to reject."""
        mock_db.create_component_with_config.return_value = (
            {"component_id": "a", "name": "A", "component_type": "team", "created_at": 1},
            {"version": 1},
        )

        response = client.post("/components", json={"name": "A", "component_type": "team"})

        assert response.status_code == 201

    def test_team_config_is_validated_at_the_envelope_level(self, client, mock_db):
        response = client.post(
            "/components",
            json={
                "name": "T",
                "component_type": "team",
                "config": {"members": [{"type": "squad", "agent_id": "a"}]},
            },
        )

        assert response.status_code == 422
        assert response.json()["errors"][0] == {
            "path": "config.members[0].type",
            "message": "Member 'type' must be 'agent' or 'team'.",
            "code": "invalid_value",
        }

    def test_team_cannot_contain_itself(self, client, mock_db):
        response = client.post(
            "/components",
            json={
                "name": "T",
                "component_id": "t-1",
                "component_type": "team",
                "config": {"members": [{"type": "team", "team_id": "t-1"}]},
            },
        )

        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "cyclic_reference"

    def test_workflow_config_rejects_an_unknown_step_type(self, client, mock_db):
        response = client.post(
            "/components",
            json={"name": "W", "component_type": "workflow", "config": {"steps": [{"type": "Frobnicate"}]}},
        )

        assert response.status_code == 422
        assert response.json()["errors"][0]["path"] == "config.steps[0].type"

    def test_create_config_validates_against_the_stored_component_type(self, client, mock_db):
        mock_db.get_component.return_value = {
            "component_id": "wf-1",
            "component_type": "workflow",
            "created_at": 1,
        }

        response = client.post("/components/wf-1/configs", json={"config": {"steps": [{"type": "Nope"}]}})

        assert response.status_code == 422
        assert response.json()["errors"][0]["path"] == "config.steps[0].type"
        mock_db.upsert_config.assert_not_called()

    def test_create_config_404s_for_an_unknown_component(self, client, mock_db):
        mock_db.get_component.return_value = None

        response = client.post("/components/nope/configs", json={"config": dict(VALID_AGENT_CONFIG)})

        assert response.status_code == 404
        mock_db.upsert_config.assert_not_called()

    def test_update_config_rejects_an_invalid_config(self, client, mock_db):
        response = client.patch("/components/agent-1/configs/1", json={"config": {"instructions": "hi"}})

        assert response.status_code == 422
        assert response.json()["errors"][0]["path"] == "config.model"
        mock_db.upsert_config.assert_not_called()

    def test_unresolvable_db_reference_is_rejected(self, client, mock_db):
        """The router used to log this and store the config anyway."""
        response = client.post(
            "/components",
            json={
                "name": "A",
                "component_type": "agent",
                "config": {**VALID_AGENT_CONFIG, "db": {"id": "does-not-exist"}},
            },
        )

        assert response.status_code == 422
        assert response.json()["errors"][0] == {
            "path": "config.db.id",
            "message": (
                "Database 'does-not-exist' could not be resolved. It matches neither the OS "
                "database nor any db registered on the Registry."
            ),
            "code": "unresolvable_ref",
        }

    def test_config_rejection_is_422_not_400(self, client, mock_db):
        """ConfigValidationError subclasses ValueError; the 400 handler must not swallow it."""
        for method, url in (
            (client.post, "/components"),
            (client.post, "/components/agent-1/configs"),
            (client.patch, "/components/agent-1/configs/1"),
        ):
            payload = {"config": {"instructions": "no model"}}
            if url == "/components":
                payload |= {"name": "A", "component_type": "agent"}

            response = method(url, json=payload)

            assert response.status_code == 422, url


# =============================================================================
# _resolve_db_in_config Tests
# =============================================================================
#
# These cover the components-router-specific merge behavior when a payload
# references a db by id: only whitelisted table-name fields are accepted from
# the caller; connection-defining fields (type / db_url / db_file / db_schema /
# id) always come from the resolved db so a caller cannot redirect a
# referenced db to a different backend through this path.


class TestResolveDbInConfig:
    """Tests for the _resolve_db_in_config helper in the components router."""

    def _make_os_db(self, tmp_path):
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        return SqliteDb(db_file=str(tmp_path / "os.db"))

    def test_no_db_in_config_is_noop(self, tmp_path):
        from egpt.os.routers.components.components import _resolve_db_in_config

        os_db = self._make_os_db(tmp_path)
        config = {"name": "agent"}

        out, _unresolved = _resolve_db_in_config(dict(config), os_db, None)

        assert out == config

    def test_db_none_is_removed(self, tmp_path):
        from egpt.os.routers.components.components import _resolve_db_in_config

        os_db = self._make_os_db(tmp_path)

        out, _unresolved = _resolve_db_in_config({"name": "agent", "db": None}, os_db, None)

        assert "db" not in out

    def test_db_without_id_is_passed_through(self, tmp_path):
        from egpt.os.routers.components.components import _resolve_db_in_config

        os_db = self._make_os_db(tmp_path)
        payload = {"db": {"type": "sqlite", "session_table": "custom"}}

        out, _unresolved = _resolve_db_in_config(dict(payload), os_db, None)

        assert out["db"] == payload["db"]

    def test_matching_id_merges_table_overrides_onto_resolved_db(self, tmp_path):
        """The reported bug: table-name overrides in the payload were being
        replaced with the resolved db's defaults."""
        from egpt.os.routers.components.components import _resolve_db_in_config

        os_db = self._make_os_db(tmp_path)
        payload = {
            "db": {
                "id": os_db.id,
                "session_table": "custom_sessions",
                "memory_table": "custom_memories",
            },
        }

        out, _unresolved = _resolve_db_in_config(dict(payload), os_db, None)

        assert out["db"]["session_table"] == "custom_sessions"
        assert out["db"]["memory_table"] == "custom_memories"
        # Connection metadata is filled in from the resolved db.
        assert out["db"]["type"] == "sqlite"
        assert out["db"]["db_file"] == os_db.db_file
        # Fields the caller didn't override are not serialized (sparse dict):
        # they re-derive from the resolved db's table_prefix on reload.
        assert "knowledge_table" not in out["db"]
        assert out["db"]["table_prefix"] == os_db.table_prefix

    def test_matching_id_rejects_caller_override_of_connection_fields(self, tmp_path):
        """Whitelist: a caller cannot redirect a referenced db by
        supplying type / db_url / db_file / db_schema."""
        from egpt.os.routers.components.components import _resolve_db_in_config

        os_db = self._make_os_db(tmp_path)
        payload = {
            "db": {
                "id": os_db.id,
                "type": "postgres",
                "db_url": "postgresql://attacker/evil",
                "db_file": "/evil.db",
                "db_schema": "public",
                "session_table": "custom_sessions",
            },
        }

        out, _unresolved = _resolve_db_in_config(dict(payload), os_db, None)

        resolved_db_dict = out["db"]
        # Connection fields MUST come from os_db, never from the caller.
        assert resolved_db_dict["type"] == "sqlite"
        assert resolved_db_dict["db_file"] == os_db.db_file
        assert resolved_db_dict.get("db_url") == os_db.db_url
        assert resolved_db_dict["id"] == os_db.id
        # The only caller-provided field that is allowed through is the
        # whitelisted table-name override.
        assert resolved_db_dict["session_table"] == "custom_sessions"

    def test_matching_id_ignores_non_whitelisted_keys(self, tmp_path):
        """Unknown keys in the payload must not leak into the stored config."""
        from egpt.os.routers.components.components import _resolve_db_in_config

        os_db = self._make_os_db(tmp_path)
        payload = {
            "db": {
                "id": os_db.id,
                "session_table": "custom_sessions",
                "arbitrary_extension": "something",
            },
        }

        out, _unresolved = _resolve_db_in_config(dict(payload), os_db, None)

        assert "arbitrary_extension" not in out["db"]
        assert out["db"]["session_table"] == "custom_sessions"

    def test_matching_id_honors_table_prefix_override(self, tmp_path):
        """A caller-supplied table_prefix must take effect: the resolved db's
        concrete default table names (which would otherwise shadow the prefix)
        are dropped so they get re-derived from the new prefix."""
        from egpt.os.routers.components.components import _resolve_db_in_config

        os_db = self._make_os_db(tmp_path)
        payload = {"db": {"id": os_db.id, "table_prefix": "acme_"}}

        out, _unresolved = _resolve_db_in_config(dict(payload), os_db, None)

        assert out["db"]["table_prefix"] == "acme_"
        # Connection metadata still comes from the resolved db.
        assert out["db"]["type"] == "sqlite"
        assert out["db"]["db_file"] == os_db.db_file
        # Inherited concrete default names are dropped so the prefix wins on reload.
        assert "session_table" not in out["db"]
        assert "knowledge_table" not in out["db"]

    def test_table_prefix_with_explicit_override_keeps_explicit_name(self, tmp_path):
        """An explicit per-table name provided alongside a new table_prefix is
        preserved, while the other tables re-derive from the prefix."""
        from egpt.os.routers.components.components import _resolve_db_in_config

        os_db = self._make_os_db(tmp_path)
        payload = {
            "db": {
                "id": os_db.id,
                "table_prefix": "acme_",
                "session_table": "custom_sessions",
            },
        }

        out, _unresolved = _resolve_db_in_config(dict(payload), os_db, None)

        assert out["db"]["table_prefix"] == "acme_"
        assert out["db"]["session_table"] == "custom_sessions"
        # Non-overridden tables are dropped for re-derivation from the prefix.
        assert "memory_table" not in out["db"]


# =============================================================================
# Repeated Delete Against a Real Backend (EGPT-2.6 review, round 2)
# =============================================================================
#
# Every other DELETE test in this file mocks db.delete_component, so none of them exercise
# what a *real* backend reports on a repeat call -- and the router maps that boolean straight
# to 204/404 (components.py: `if not deleted: raise HTTPException(404, ...)`). Before the
# `deleted_at IS NULL` fix, SQLite's soft delete matched its own already-deleted row again and
# returned True a second time, so a repeat DELETE answered 204 twice instead of 204-then-404.


class TestRepeatedDeleteAgainstARealBackend:
    def _client(self, tmp_path):
        from egpt.integrations.db.sqlite.sqlite import SqliteDb

        os_db = SqliteDb(db_file=str(tmp_path / "os.db"))
        app = FastAPI()
        app.include_router(get_components_router(os_db=os_db, settings=AgnoAPISettings()))
        return TestClient(app)

    def test_the_first_delete_is_204_and_the_second_is_404(self, tmp_path):
        client = self._client(tmp_path)
        client.post(
            "/components",
            json={"component_id": "agent-1", "component_type": "agent", "name": "Agent", "config": VALID_AGENT_CONFIG},
        )

        first = client.delete("/components/agent-1")
        second = client.delete("/components/agent-1")

        assert first.status_code == 204
        assert second.status_code == 404


# =============================================================================
# POST /components/validate -- the dry run (EGPT-2.9)
# =============================================================================
#
# The endpoint exists so an authoring client can show field-level errors *before* it writes
# anything. Two properties carry that: it stores nothing, and its 422 is the same body the write
# routes produce -- otherwise a client would need two error parsers and the console's single
# `normalizeError` would be a lie.


class TestValidateEndpoint:
    def test_a_valid_agent_config_validates(self, client):
        response = client.post(
            "/components/validate",
            json={"component_type": "agent", "config": VALID_AGENT_CONFIG},
        )

        assert response.status_code == 200
        assert response.json() == {"valid": True, "schema_version": 1}

    def test_it_writes_nothing(self, client, mock_db):
        """The defining property: a dry run must not touch a single row, valid or not."""
        client.post("/components/validate", json={"component_type": "agent", "config": VALID_AGENT_CONFIG})
        client.post("/components/validate", json={"component_type": "agent", "config": {"instructions": "hi"}})

        mock_db.create_component_with_config.assert_not_called()
        mock_db.upsert_component.assert_not_called()
        mock_db.upsert_config.assert_not_called()
        mock_db.set_current_version.assert_not_called()
        mock_db.delete_component.assert_not_called()
        mock_db.delete_config.assert_not_called()

    def test_an_invalid_config_returns_field_errors(self, client):
        response = client.post(
            "/components/validate",
            json={"component_type": "agent", "config": {"instructions": "no model here"}},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error_code"] == "CONFIG_VALIDATION_ERROR"
        assert body["schema_version"] == 1
        assert body["errors"][0]["path"] == "config.model"
        assert body["errors"][0]["code"] == "missing_required_field"

    def test_its_error_body_matches_the_write_route(self, client):
        """A client parses one shape. If these ever diverge, that assumption breaks silently."""
        bad_config = {"instructions": "no model here"}

        dry_run = client.post("/components/validate", json={"component_type": "agent", "config": bad_config})
        write = client.post(
            "/components",
            json={"name": "Agent", "component_type": "agent", "config": bad_config},
        )

        assert dry_run.status_code == write.status_code == 422
        assert dry_run.json() == write.json()

    def test_an_unresolvable_db_reference_is_a_field_error(self, client):
        """The db reference resolves before validation, so a bad id names its own field."""
        response = client.post(
            "/components/validate",
            json={
                "component_type": "agent",
                "config": {"model": "openai:gpt-5.5", "db": {"id": "no-such-db"}},
            },
        )

        assert response.status_code == 422
        paths = [error["path"] for error in response.json()["errors"]]
        assert "config.db.id" in paths

    def test_an_empty_config_validates(self, client):
        """Mirrors the write path: an empty config is a shell, and there is nothing to reject."""
        response = client.post("/components/validate", json={"component_type": "agent", "config": {}})

        assert response.status_code == 200

    def test_an_unknown_component_type_is_a_request_shape_error(self, client):
        """FastAPI's own 422, deliberately a different body -- a client must handle both."""
        response = client.post("/components/validate", json={"component_type": "sandwich", "config": {}})

        assert response.status_code == 422
        body = response.json()
        assert isinstance(body["detail"], list)
        assert "error_code" not in body

    def test_config_is_required(self, client):
        response = client.post("/components/validate", json={"component_type": "agent"})

        assert response.status_code == 422

    def test_the_caller_body_is_not_mutated_into_the_response(self, client):
        """The handler copies before resolving; a dry run returns a verdict, not a rewritten config."""
        response = client.post(
            "/components/validate",
            json={"component_type": "agent", "config": VALID_AGENT_CONFIG},
        )

        assert set(response.json()) == {"valid", "schema_version"}


# =============================================================================
# The dry run and the write route must agree on identity (PR #72 review, finding 1)
# =============================================================================
#
# `create_component` derives `component_id` from `name` when the caller omits it, and
# `component_id` is the *only* input to self-reference detection. A dry run that passed the
# caller's `component_id` straight through therefore answered 200 for a self-referencing team
# that the write route then rejected with `cyclic_reference` -- and on the console's default
# path, where the id box is optional and labelled "derived from name".


class TestTheDryRunDerivesTheIdLikeTheWriteRoute:
    SELF_REFERENCING_TEAM = {"members": [{"type": "team", "team_id": "support-team"}]}

    def test_a_self_reference_is_caught_when_the_id_is_derived_from_the_name(self, client):
        response = client.post(
            "/components/validate",
            json={"component_type": "team", "name": "Support Team", "config": self.SELF_REFERENCING_TEAM},
        )

        assert response.status_code == 422
        assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
            ("config.members[0].team_id", "cyclic_reference")
        ]

    def test_it_agrees_with_the_write_route_on_a_derived_id(self, client):
        """The byte-identity contract, on the path that used to break it."""
        body = {"name": "Support Team", "component_type": "team", "config": self.SELF_REFERENCING_TEAM}

        dry_run = client.post("/components/validate", json=body)
        write = client.post("/components", json=body)

        assert dry_run.status_code == write.status_code == 422
        assert dry_run.json() == write.json()

    def test_an_explicit_id_still_wins_over_the_name(self, client):
        """`component_id` is authoritative when supplied, exactly as on the write route."""
        response = client.post(
            "/components/validate",
            json={
                "component_type": "team",
                "component_id": "something-else",
                "name": "Support Team",
                "config": self.SELF_REFERENCING_TEAM,
            },
        )

        assert response.status_code == 200

    def test_a_team_that_does_not_reference_itself_is_unaffected(self, client):
        response = client.post(
            "/components/validate",
            json={
                "component_type": "team",
                "name": "Support Team",
                "config": {"members": [{"type": "agent", "agent_id": "faq-agent"}]},
            },
        )

        assert response.status_code == 200

    def test_name_is_optional(self, client):
        """Clients that always send an explicit id must not have to start sending a name."""
        response = client.post(
            "/components/validate",
            json={"component_type": "agent", "config": VALID_AGENT_CONFIG},
        )

        assert response.status_code == 200
