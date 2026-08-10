"""Capability policy and tenant scoping on the components write path (EGPT-2.3).

Follows the fixture shape of `test_components_router.py`: a MagicMock-backed `BaseDb` subclass
and a bare `FastAPI()` with the router mounted. The one addition is a tiny middleware that sets
`request.state.tenant_id` from a header, standing in for the JWT `tenant_id_claim` -- which keeps
these tests about the router's behaviour rather than about token decoding.
"""

from typing import Optional
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from egpt.db.base import BaseDb
from egpt.os.routers.components import get_components_router
from egpt.os.settings import EgptServingSettings
from egpt.registry import Registry
from egpt.registry.policy import CapabilityPolicy
from egpt.tools.toolkit import Toolkit

TENANT_HEADER = "x-test-tenant"

COMPONENT_ROW = {
    "component_id": "agent-1",
    "component_type": "agent",
    "name": "Test Agent",
    "current_version": 1,
    "created_at": 1700000000,
    "updated_at": 1700000000,
}
CONFIG_ROW = {"component_id": "agent-1", "version": 1, "config": {}, "stage": "draft", "created_at": 1700000000}

VALID_CONFIG = {"model": "openai:gpt-5.5"}
SHELL_CONFIG = {"model": "openai:gpt-5.5", "tools": [{"name": "run_shell_command"}]}


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_db():
    abstract = {
        name: MagicMock() for name in dir(BaseDb) if getattr(getattr(BaseDb, name, None), "__isabstractmethod__", False)
    }
    db = type("MockDb", (BaseDb,), abstract)()
    db.id = "test-db"
    db.get_component = MagicMock(return_value=COMPONENT_ROW)
    db.create_component_with_config = MagicMock(return_value=(COMPONENT_ROW, CONFIG_ROW))
    db.upsert_config = MagicMock(return_value=CONFIG_ROW)
    db.list_configs = MagicMock(return_value=[])
    db.list_components = MagicMock(return_value=([], 0))
    return db


@pytest.fixture
def shell_registry():
    """A registry holding a stand-in ShellTools, so classification is unambiguous.

    `__module__` is rewritten to the real package: the classifier matches on module path, so this
    behaves exactly like the real toolkit without importing anything from integrations.
    """

    class FakeShellTools(Toolkit):
        def __init__(self):
            super().__init__(name="shell_tools", tools=[self.run_shell_command])

        def run_shell_command(self, command: str) -> str:
            """Run a shell command."""
            return command

    FakeShellTools.__module__ = "egpt.integrations.tools.shell.shell"
    return Registry(tools=[FakeShellTools()])


def build_client(db, policy: Optional[CapabilityPolicy] = None, registry: Optional[Registry] = None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def set_tenant(request: Request, call_next):
        request.state.tenant_id = request.headers.get(TENANT_HEADER) or None
        return await call_next(request)

    app.include_router(
        get_components_router(os_db=db, settings=EgptServingSettings(), registry=registry, capability_policy=policy)
    )
    return TestClient(app)


def post_component(client, config, tenant=None):
    headers = {TENANT_HEADER: tenant} if tenant else {}
    return client.post(
        "/components",
        json={"name": "Test Agent", "component_type": "agent", "config": config},
        headers=headers,
    )


def errors_of(response):
    return [(e["path"], e["code"]) for e in response.json().get("errors", [])]


# =============================================================================
# No policy configured: the change must be invisible
# =============================================================================


class TestNoPolicyConfigured:
    """The opt-in contract. Everything here must behave as it did before EGPT-2.3."""

    def test_a_privileged_tool_is_accepted_when_no_policy_is_configured(self, mock_db, shell_registry):
        client = build_client(mock_db, policy=None, registry=shell_registry)
        assert post_component(client, SHELL_CONFIG).status_code == 201

    def test_any_model_is_accepted_when_no_policy_is_configured(self, mock_db):
        client = build_client(mock_db, policy=None)
        assert post_component(client, {"model": "anthropic:claude-sonnet-5"}).status_code == 201

    def test_db_calls_carry_tenant_id_none(self, mock_db):
        """None applies no tenant predicate, so the SQL is unchanged."""
        client = build_client(mock_db, policy=None)
        post_component(client, VALID_CONFIG)
        assert mock_db.create_component_with_config.call_args.kwargs["tenant_id"] is None


# =============================================================================
# Enforcement
# =============================================================================


class TestDisallowedCapabilitiesAreRefused:
    def test_a_disallowed_model_is_rejected_with_a_reason(self, mock_db):
        policy = CapabilityPolicy.from_dict({"defaults": {"models": ["openai"]}})
        client = build_client(mock_db, policy=policy)

        response = post_component(client, {"model": "anthropic:claude-sonnet-5"})

        assert response.status_code == 422
        assert errors_of(response) == [("config.model", "capability_not_permitted")]
        assert "anthropic:claude-sonnet-5" in response.json()["errors"][0]["message"]
        mock_db.create_component_with_config.assert_not_called()

    @pytest.mark.parametrize("tool_name, slug", [("run_shell_command", "shell"), ("run_sql_query", "sql")])
    def test_privileged_tools_are_blocked_by_default(self, mock_db, tool_name, slug):
        policy = CapabilityPolicy.from_dict({"defaults": {"models": ["openai"], "tools": ["*"]}})
        client = build_client(mock_db, policy=policy)

        response = post_component(client, {"model": "openai:gpt-5.5", "tools": [{"name": tool_name}]})

        assert response.status_code == 422
        assert errors_of(response) == [("config.tools[0].name", "privileged_tool_blocked")]
        assert slug in response.json()["errors"][0]["message"]
        mock_db.create_component_with_config.assert_not_called()

    def test_the_permitted_set_is_accepted(self, mock_db):
        policy = CapabilityPolicy.from_dict({"defaults": {"models": ["openai"], "tools": ["*"]}})
        client = build_client(mock_db, policy=policy)
        assert post_component(client, VALID_CONFIG).status_code == 201

    def test_every_violation_comes_back_in_one_response(self, mock_db):
        policy = CapabilityPolicy.from_dict({"defaults": {"models": ["openai"], "tools": ["*"]}})
        client = build_client(mock_db, policy=policy)

        response = post_component(client, {"model": "anthropic:x", "tools": [{"name": "run_sql_query"}]})

        assert response.status_code == 422
        assert errors_of(response) == [
            ("config.model", "capability_not_permitted"),
            ("config.tools[0].name", "privileged_tool_blocked"),
        ]


class TestExplicitGrant:
    """Acceptance criterion 5: a specific tenant may be allowed a normally-privileged tool."""

    @pytest.fixture
    def policy(self):
        return CapabilityPolicy.from_dict(
            {
                "defaults": {"models": ["openai"], "tools": ["*"]},
                "tenants": {"acme": {"models": ["openai"], "tools": ["*"], "granted_privileged": ["shell"]}},
            }
        )

    def test_the_granted_tenant_may_use_it(self, mock_db, policy, shell_registry):
        client = build_client(mock_db, policy=policy, registry=shell_registry)
        assert post_component(client, SHELL_CONFIG, tenant="acme").status_code == 201

    def test_another_tenant_still_may_not(self, mock_db, policy, shell_registry):
        client = build_client(mock_db, policy=policy, registry=shell_registry)
        response = post_component(client, SHELL_CONFIG, tenant="other")
        assert response.status_code == 422
        assert errors_of(response) == [("config.tools[0].name", "privileged_tool_blocked")]

    def test_an_unscoped_caller_still_may_not(self, mock_db, policy, shell_registry):
        client = build_client(mock_db, policy=policy, registry=shell_registry)
        assert post_component(client, SHELL_CONFIG).status_code == 422


class TestConfigEndpointsEnforceToo:
    """The policy gates every write path, not just component creation."""

    def test_create_config_rejects_a_privileged_tool(self, mock_db):
        policy = CapabilityPolicy.from_dict({"defaults": {"models": ["openai"], "tools": ["*"]}})
        client = build_client(mock_db, policy=policy)

        response = client.post("/components/agent-1/configs", json={"config": SHELL_CONFIG})

        assert response.status_code == 422
        assert errors_of(response) == [("config.tools[0].name", "privileged_tool_blocked")]
        mock_db.upsert_config.assert_not_called()

    def test_update_config_rejects_a_privileged_tool(self, mock_db):
        policy = CapabilityPolicy.from_dict({"defaults": {"models": ["openai"], "tools": ["*"]}})
        client = build_client(mock_db, policy=policy)

        response = client.patch("/components/agent-1/configs/1", json={"config": SHELL_CONFIG})

        assert response.status_code == 422
        assert errors_of(response) == [("config.tools[0].name", "privileged_tool_blocked")]
        mock_db.upsert_config.assert_not_called()


# =============================================================================
# Tenant scoping of the DB calls
# =============================================================================


class TestTenantIsThreadedToTheDb:
    def test_create_stamps_the_tenant(self, mock_db):
        client = build_client(mock_db)
        post_component(client, VALID_CONFIG, tenant="acme")
        assert mock_db.create_component_with_config.call_args.kwargs["tenant_id"] == "acme"

    def test_reads_are_scoped_to_the_tenant(self, mock_db):
        client = build_client(mock_db)
        client.get("/components/agent-1", headers={TENANT_HEADER: "acme"})
        assert mock_db.get_component.call_args.kwargs["tenant_id"] == "acme"

    def test_listing_is_scoped_to_the_tenant(self, mock_db):
        client = build_client(mock_db)
        client.get("/components", headers={TENANT_HEADER: "acme"})
        assert mock_db.list_components.call_args.kwargs["tenant_id"] == "acme"

    def test_config_writes_are_scoped_to_the_tenant(self, mock_db):
        client = build_client(mock_db)
        client.post("/components/agent-1/configs", json={"config": VALID_CONFIG}, headers={TENANT_HEADER: "acme"})
        assert mock_db.upsert_config.call_args.kwargs["tenant_id"] == "acme"

    def test_delete_is_scoped_to_the_tenant(self, mock_db):
        mock_db.delete_component = MagicMock(return_value=True)
        client = build_client(mock_db)
        client.delete("/components/agent-1", headers={TENANT_HEADER: "acme"})
        assert mock_db.delete_component.call_args.kwargs["tenant_id"] == "acme"
