"""End-to-end capability policy through the real app and a real SQLite db (EGPT-2.3).

The unit tests prove each layer in isolation. What only this level can prove is the property the
ticket actually asks for -- that the policy is enforced at write **and** at hydration, and that
the second one is not redundant: a definition accepted while a grant was in place is refused at
read time once the grant is revoked, even though the row is already sitting in the database.

Real `EgptOS`, real SQLite, no docker and no env flags -- matching `test_components_validation.py`.
"""

import pytest
from fastapi.testclient import TestClient

from egpt.os import EgptOS
from egpt.registry import Registry
from egpt.registry.policy import CapabilityPolicy
from egpt.tools.toolkit import Toolkit

AGENT_CONFIG = {"model": {"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"}}
SQL_AGENT_CONFIG = {**AGENT_CONFIG, "tools": [{"name": "run_sql_query"}]}

# These clients carry no JWT, so every request is unscoped and resolves to the `defaults` block
# (see CapabilityPolicy.for_tenant). The grants therefore live in `defaults` here; the per-tenant
# selection itself is covered by the unit tests, which can set request.state.tenant_id directly.
RESTRICTIVE = {"defaults": {"models": ["openai"], "tools": ["*"], "granted_privileged": []}}
PERMISSIVE = {"defaults": {"models": ["openai"], "tools": ["*"], "granted_privileged": ["sql"]}}


@pytest.fixture
def sql_registry():
    """A registry holding a stand-in SQLTools so `run_sql_query` classifies unambiguously.

    `__module__` is rewritten to the package the real toolkit lives in; classification matches on
    module path, so this is equivalent without importing anything from integrations.
    """

    class FakeSQLTools(Toolkit):
        def __init__(self):
            super().__init__(name="sql_tools", tools=[self.run_sql_query])

        def run_sql_query(self, query: str) -> str:
            """Run a SQL query."""
            return query

    FakeSQLTools.__module__ = "egpt.integrations.tools.sql.sql"
    return Registry(tools=[FakeSQLTools()])


def build_os(db, document, registry):
    return EgptOS(db=db, registry=registry, capability_policy=CapabilityPolicy.from_dict(document))


def create_agent(client, component_id, config):
    return client.post(
        "/components",
        json={
            "name": component_id,
            "component_id": component_id,
            "component_type": "agent",
            "config": config,
        },
    )


class TestPolicyIsEnforcedOnWrite:
    def test_a_privileged_tool_is_refused_and_nothing_is_stored(self, shared_db, sql_registry):
        client = TestClient(build_os(shared_db, RESTRICTIVE, sql_registry).get_app())

        response = create_agent(client, "sql-agent", SQL_AGENT_CONFIG)

        assert response.status_code == 422
        body = response.json()
        assert body["error_code"] == "CONFIG_VALIDATION_ERROR"
        assert [(e["path"], e["code"]) for e in body["errors"]] == [("config.tools[0].name", "privileged_tool_blocked")]
        assert client.get("/components/sql-agent").status_code == 404

    def test_a_disallowed_model_is_refused(self, shared_db, sql_registry):
        document = {"defaults": {"models": ["anthropic"]}}
        client = TestClient(build_os(shared_db, document, sql_registry).get_app())

        response = create_agent(client, "openai-agent", AGENT_CONFIG)

        assert response.status_code == 422
        assert [e["code"] for e in response.json()["errors"]] == ["capability_not_permitted"]

    def test_a_granted_definition_is_accepted_and_persisted(self, shared_db, sql_registry):
        client = TestClient(build_os(shared_db, PERMISSIVE, sql_registry).get_app())

        assert create_agent(client, "sql-agent", SQL_AGENT_CONFIG).status_code == 201

        stored = client.get("/components/sql-agent/configs/current").json()["config"]
        assert stored["tools"] == [{"name": "run_sql_query"}]


class TestPolicyIsEnforcedAtHydration:
    """The acceptance criterion the write-path check cannot satisfy on its own."""

    def test_a_stored_config_stops_hydrating_once_its_grant_is_revoked(self, shared_db, sql_registry):
        # 1. Written while `sql` was granted -- perfectly legitimate at the time.
        permissive_client = TestClient(build_os(shared_db, PERMISSIVE, sql_registry).get_app())
        assert create_agent(permissive_client, "sql-agent", SQL_AGENT_CONFIG).status_code == 201

        # 2. The grant is revoked. The row is untouched and still in the database.
        restrictive_os = build_os(shared_db, RESTRICTIVE, sql_registry)
        restrictive_client = TestClient(restrictive_os.get_app())
        assert restrictive_client.get("/components/sql-agent").status_code == 200

        # 3. Hydration now refuses it -- 403, not 404: the agent exists, it is just not permitted.
        response = restrictive_client.get("/agents/sql-agent")
        assert response.status_code == 403
        assert "sql" in response.json()["detail"]

    def test_a_revoked_agent_drops_out_of_the_listing(self, shared_db, sql_registry):
        permissive_client = TestClient(build_os(shared_db, PERMISSIVE, sql_registry).get_app())
        create_agent(permissive_client, "sql-agent", SQL_AGENT_CONFIG)
        create_agent(permissive_client, "plain-agent", AGENT_CONFIG)
        assert {a["id"] for a in permissive_client.get("/agents").json()} == {"sql-agent", "plain-agent"}

        restrictive_client = TestClient(build_os(shared_db, RESTRICTIVE, sql_registry).get_app())

        # The violating one is omitted; the other is unaffected, so one bad row does not
        # empty the page.
        assert {a["id"] for a in restrictive_client.get("/agents").json()} == {"plain-agent"}


class TestNoPolicyIsAFullNoOp:
    """The opt-in contract, proven through the whole stack rather than at a single layer."""

    def test_a_privileged_definition_writes_and_hydrates_when_no_policy_is_configured(
        self, shared_db, sql_registry, monkeypatch
    ):
        monkeypatch.delenv("EGPT_CAPABILITY_POLICY_FILE", raising=False)
        monkeypatch.delenv("AGNO_CAPABILITY_POLICY_FILE", raising=False)
        client = TestClient(EgptOS(db=shared_db, registry=sql_registry).get_app())

        assert create_agent(client, "sql-agent", SQL_AGENT_CONFIG).status_code == 201
        assert client.get("/agents/sql-agent").status_code == 200


class TestPolicyFileWiring:
    def test_a_document_on_disk_is_loaded_and_enforced(self, shared_db, sql_registry, tmp_path, monkeypatch):
        import json

        path = tmp_path / "capability-policy.json"
        path.write_text(json.dumps(RESTRICTIVE))
        monkeypatch.setenv("EGPT_CAPABILITY_POLICY_FILE", str(path))

        client = TestClient(EgptOS(db=shared_db, registry=sql_registry).get_app())

        assert create_agent(client, "sql-agent", SQL_AGENT_CONFIG).status_code == 422

    def test_a_mistyped_path_fails_loudly_at_construction(self, shared_db, tmp_path, monkeypatch):
        """It must not silently degrade to "no policy" -- that is the control switching itself off."""
        monkeypatch.setenv("EGPT_CAPABILITY_POLICY_FILE", str(tmp_path / "typo.json"))

        with pytest.raises(FileNotFoundError, match="Capability policy file not found"):
            EgptOS(db=shared_db)
