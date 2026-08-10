"""Tenant-scoped runtime hydration of DB-defined components against a real stack (EGPT-2.5).

The unit tier proves each mechanism against a mocked db. What only this level can prove:

* an agent that exists ONLY as rows in Postgres -- authored over the components API by a
  JWT-authenticated tenant -- hydrates and runs a turn end to end, scoped to that tenant;
* tenant B's attempt to run tenant A's agent is the same 404 a nonexistent id gets, because
  the tenant predicate is real SQL, not a mock honoring the test's intent;
* a config update takes effect on the next run: the mutation route's cache eviction is
  exercised through the same HTTP surface a real deployment uses;
* published-only hydration refuses a draft-only component with a 409;
* a team whose members are DB rows hydrates fully within the tenant.

Gated on ``EGPT_INTEGRATION_POSTGRES``. Bring the database up with::

    ./cookbook/scripts/run_pgvector.sh
    EGPT_INTEGRATION_POSTGRES=1 pytest libs/agno/tests/integration/os/hydration_postgres -v
"""

from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Iterator, Optional
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from egpt.models.base import Model
from egpt.models.message import MessageMetrics
from egpt.models.response import ModelResponse
from egpt.os import EgptOS
from egpt.os.config import AuthorizationConfig
from egpt.registry import Registry

jwt = pytest.importorskip("jwt", reason="pyjwt is required for the hydration integration suite")

JWT_SECRET = "test-secret-key-for-hydration-tests-0123456789"
OS_ID = "hydration-test-os"
TENANT_CLAIM = "tid"

TENANT_A = "acme"
TENANT_B = "globex"

ALL_SCOPES = [
    "components:read",
    "components:write",
    "components:delete",
    "agents:read",
    "agents:write",
    "agents:run",
    "teams:read",
    "teams:write",
    "teams:run",
]

MOCK_MODEL = {"id": "mock-1", "provider": "mock", "name": "MockModel"}


class MockModel(Model):
    """A registry-resolvable model that answers without any provider call."""

    def __init__(self):
        super().__init__(id="mock-1", name="MockModel", provider="mock")
        self._mock_response = ModelResponse(
            content="mock answer",
            role="assistant",
            response_usage=MessageMetrics(),
        )
        self.response = Mock(return_value=self._mock_response)
        self.aresponse = AsyncMock(return_value=self._mock_response)

    def get_instructions_for_model(self, *args, **kwargs):
        return None

    def get_system_message_for_model(self, *args, **kwargs):
        return None

    async def aget_instructions_for_model(self, *args, **kwargs):
        return None

    async def aget_system_message_for_model(self, *args, **kwargs):
        return None

    def parse_args(self, *args, **kwargs):
        return {}

    def _parse_provider_response(self, response, **kwargs) -> ModelResponse:
        return self._mock_response

    def _parse_provider_response_delta(self, response) -> ModelResponse:
        return self._mock_response

    def invoke(self, *args, **kwargs) -> ModelResponse:
        return self._mock_response

    async def ainvoke(self, *args, **kwargs) -> ModelResponse:
        return self._mock_response

    def invoke_stream(self, *args, **kwargs) -> Iterator[ModelResponse]:
        yield self._mock_response

    async def ainvoke_stream(self, *args, **kwargs) -> AsyncIterator[ModelResponse]:
        yield self._mock_response
        return


def make_token(scopes, tenant: Optional[str], user_id: str = "tester") -> str:
    payload: Dict[str, Any] = {
        "sub": user_id,
        "aud": OS_ID,
        "scopes": scopes,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "iat": datetime.now(timezone.utc),
    }
    if tenant is not None:
        payload[TENANT_CLAIM] = tenant
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def auth(scopes=ALL_SCOPES, tenant: Optional[str] = None) -> Dict[str, str]:
    return {"Authorization": f"Bearer {make_token(scopes, tenant)}"}


@pytest.fixture
def hydration_db(postgres_engine):
    from egpt.integrations.db.postgres import PostgresDb  # noqa: PLC0415

    suffix = uuid4().hex[:8]
    return PostgresDb(
        db_engine=postgres_engine,
        db_schema="test_schema",
        session_table=f"h_sessions_{suffix}",
        memory_table=f"h_memories_{suffix}",
        metrics_table=f"h_metrics_{suffix}",
        eval_table=f"h_evals_{suffix}",
        knowledge_table=f"h_knowledge_{suffix}",
    )


def _make_client(db, registry=None) -> TestClient:
    os_app = EgptOS(
        id=OS_ID,
        db=db,
        registry=registry if registry is not None else Registry(models=[MockModel()]),
        authorization=True,
        authorization_config=AuthorizationConfig(
            verification_keys=[JWT_SECRET],
            algorithm="HS256",
            audience=OS_ID,
            tenant_id_claim=TENANT_CLAIM,
        ),
    )
    return TestClient(os_app.get_app())


@pytest.fixture
def client(hydration_db) -> TestClient:
    return _make_client(hydration_db)


def create_agent_component(client, component_id, tenant, *, name=None, instructions=None, stage="published"):
    config: Dict[str, Any] = {"model": dict(MOCK_MODEL)}
    if name:
        config["name"] = name
    if instructions:
        config["instructions"] = instructions
    return client.post(
        "/components",
        json={
            "name": name or component_id,
            "component_id": component_id,
            "component_type": "agent",
            "config": config,
            "stage": stage,
        },
        headers=auth(tenant=tenant),
    )


# =============================================================================


class TestDbOnlyAgentRunsEndToEnd:
    def test_an_agent_defined_solely_in_the_db_runs_a_turn(self, client):
        response = create_agent_component(client, "db-agent", TENANT_A, name="DB Agent")
        assert response.status_code == 201, response.text

        run = client.post(
            "/agents/db-agent/runs",
            data={"message": "hello", "stream": "false"},
            headers={**auth(tenant=TENANT_A), "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert run.status_code == 200, run.text
        assert run.json()["content"] == "mock answer"

    def test_the_hydrated_agent_is_visible_to_its_tenant(self, client):
        create_agent_component(client, "db-agent", TENANT_A, name="DB Agent")
        response = client.get("/agents/db-agent", headers=auth(tenant=TENANT_A))
        assert response.status_code == 200, response.text


class TestCrossTenantHydrationIsRefused:
    def test_tenant_b_cannot_run_tenant_as_agent(self, client):
        create_agent_component(client, "db-agent", TENANT_A)
        run = client.post(
            "/agents/db-agent/runs",
            data={"message": "hello", "stream": "false"},
            headers={**auth(tenant=TENANT_B), "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert run.status_code == 404, run.text

    def test_the_refusal_is_indistinguishable_from_a_missing_agent(self, client):
        create_agent_component(client, "db-agent", TENANT_A)
        foreign = client.post(
            "/agents/db-agent/runs",
            data={"message": "hello", "stream": "false"},
            headers={**auth(tenant=TENANT_B), "Content-Type": "application/x-www-form-urlencoded"},
        )
        missing = client.post(
            "/agents/no-such-agent/runs",
            data={"message": "hello", "stream": "false"},
            headers={**auth(tenant=TENANT_B), "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()


class TestUpdateTakesEffectOnTheNextRun:
    def test_publishing_a_new_version_is_served_immediately(self, client):
        """The mutation route evicts the hydration cache; without that, the 30s TTL would
        keep serving the old instructions."""
        create_agent_component(client, "db-agent", TENANT_A, name="Before")
        first = client.get("/agents/db-agent", headers=auth(tenant=TENANT_A))
        assert first.status_code == 200
        assert first.json()["name"] == "Before"

        response = client.post(
            "/components/db-agent/configs",
            json={"config": {"model": dict(MOCK_MODEL), "name": "After"}, "stage": "published"},
            headers=auth(tenant=TENANT_A),
        )
        assert response.status_code in (200, 201), response.text

        second = client.get("/agents/db-agent", headers=auth(tenant=TENANT_A))
        assert second.status_code == 200
        assert second.json()["name"] == "After"


class TestPublishedOnlyHydration:
    def test_a_draft_only_component_is_a_409(self, hydration_db):
        registry = Registry(models=[MockModel()], hydrate_published_only=True)
        client = _make_client(hydration_db, registry=registry)

        response = create_agent_component(client, "draft-agent", TENANT_A, stage="draft")
        assert response.status_code == 201, response.text

        run = client.post(
            "/agents/draft-agent/runs",
            data={"message": "hello", "stream": "false"},
            headers={**auth(tenant=TENANT_A), "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert run.status_code == 409, run.text
        assert "not published" in run.json()["detail"]

    def test_a_published_component_still_runs(self, hydration_db):
        registry = Registry(models=[MockModel()], hydrate_published_only=True)
        client = _make_client(hydration_db, registry=registry)
        create_agent_component(client, "pub-agent", TENANT_A)

        run = client.post(
            "/agents/pub-agent/runs",
            data={"message": "hello", "stream": "false"},
            headers={**auth(tenant=TENANT_A), "Content-Type": "application/x-www-form-urlencoded"},
        )
        assert run.status_code == 200, run.text


class TestTeamOfDbMembersHydrates:
    def test_a_team_with_db_member_agents_hydrates_within_the_tenant(self, client):
        assert create_agent_component(client, "member-1", TENANT_A, name="Member One").status_code == 201
        assert create_agent_component(client, "member-2", TENANT_A, name="Member Two").status_code == 201

        response = client.post(
            "/components",
            json={
                "name": "db-team",
                "component_id": "db-team",
                "component_type": "team",
                "config": {
                    "model": dict(MOCK_MODEL),
                    "members": [
                        {"type": "agent", "agent_id": "member-1"},
                        {"type": "agent", "agent_id": "member-2"},
                    ],
                },
                "stage": "published",
            },
            headers=auth(tenant=TENANT_A),
        )
        assert response.status_code == 201, response.text

        team = client.get("/teams/db-team", headers=auth(tenant=TENANT_A))
        assert team.status_code == 200, team.text

        foreign = client.get("/teams/db-team", headers=auth(tenant=TENANT_B))
        assert foreign.status_code == 404
