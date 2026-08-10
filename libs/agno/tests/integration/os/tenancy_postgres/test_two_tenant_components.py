"""Two tenants against a real Postgres, through a real EgptOS with real JWTs (EGPT-2.4).

The unit tests prove each gate in isolation with a mocked db. What only this level can prove is
that the tenant predicate is actually a SQL predicate: that tenant B's `GET /components` runs a
`WHERE tenant_id = 'globex'` that genuinely excludes tenant A's rows, rather than a mock
returning what the test told it to.

Gated on ``EGPT_INTEGRATION_POSTGRES``. Bring the database up with::

    ./cookbook/scripts/run_pgvector.sh
    EGPT_INTEGRATION_POSTGRES=1 pytest libs/agno/tests/integration/os/tenancy_postgres -v
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from egpt.os import EgptOS
from egpt.os.config import AuthorizationConfig

jwt = pytest.importorskip("jwt", reason="pyjwt is required for the tenancy integration suite")

JWT_SECRET = "test-secret-key-for-tenancy-tests-0123456789"
OS_ID = "tenancy-test-os"
TENANT_CLAIM = "tid"

TENANT_A = "acme"
TENANT_B = "globex"

ALL_SCOPES = ["components:read", "components:write", "components:delete"]

AGENT_CONFIG = {"model": {"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"}}


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
def tenancy_db(postgres_engine):
    """A PostgresDb with component and service-account tables unique to this run."""
    from egpt.integrations.db.postgres import PostgresDb  # noqa: PLC0415

    suffix = uuid4().hex[:8]
    return PostgresDb(
        db_engine=postgres_engine,
        db_schema="test_schema",
        session_table=f"t_sessions_{suffix}",
        memory_table=f"t_memories_{suffix}",
        metrics_table=f"t_metrics_{suffix}",
        eval_table=f"t_evals_{suffix}",
        knowledge_table=f"t_knowledge_{suffix}",
    )


@pytest.fixture
def client(tenancy_db) -> TestClient:
    """A real EgptOS with RBAC and tenancy both switched on."""
    os_app = EgptOS(
        id=OS_ID,
        db=tenancy_db,
        authorization=True,
        authorization_config=AuthorizationConfig(
            verification_keys=[JWT_SECRET],
            algorithm="HS256",
            audience=OS_ID,
            tenant_id_claim=TENANT_CLAIM,
        ),
    )
    return TestClient(os_app.get_app())


def create_component(client: TestClient, component_id: str, tenant: str, config=None):
    return client.post(
        "/components",
        json={
            "name": component_id,
            "component_id": component_id,
            "component_type": "agent",
            "config": config if config is not None else AGENT_CONFIG,
        },
        headers=auth(tenant=tenant),
    )


# =============================================================================


class TestTwoTenantsAreIsolated:
    def test_a_component_is_created_under_the_callers_tenant(self, client):
        response = create_component(client, "agent-a", TENANT_A)
        assert response.status_code == 201, response.text

    def test_the_owner_can_read_it_back(self, client):
        create_component(client, "agent-a", TENANT_A)
        response = client.get("/components/agent-a", headers=auth(tenant=TENANT_A))
        assert response.status_code == 200
        assert response.json()["component_id"] == "agent-a"

    def test_another_tenant_cannot_read_it(self, client):
        create_component(client, "agent-a", TENANT_A)
        assert client.get("/components/agent-a", headers=auth(tenant=TENANT_B)).status_code == 404

    def test_another_tenant_cannot_update_it(self, client):
        create_component(client, "agent-a", TENANT_A)
        response = client.patch("/components/agent-a", json={"name": "hijacked"}, headers=auth(tenant=TENANT_B))
        assert response.status_code == 404

    def test_another_tenant_cannot_delete_it(self, client):
        create_component(client, "agent-a", TENANT_A)
        assert client.delete("/components/agent-a", headers=auth(tenant=TENANT_B)).status_code == 404
        # And it is still there for its owner.
        assert client.get("/components/agent-a", headers=auth(tenant=TENANT_A)).status_code == 200

    def test_another_tenant_cannot_write_a_config(self, client):
        create_component(client, "agent-a", TENANT_A)
        response = client.post(
            "/components/agent-a/configs", json={"config": AGENT_CONFIG}, headers=auth(tenant=TENANT_B)
        )
        assert response.status_code == 404

    def test_a_config_write_with_no_config_blob_is_also_refused(self, client):
        """The branch that used to skip the tenant-scoped existence check entirely."""
        create_component(client, "agent-a", TENANT_A)
        response = client.patch(
            "/components/agent-a/configs/1", json={"label": "stable"}, headers=auth(tenant=TENANT_B)
        )
        assert response.status_code == 404

    def test_listings_do_not_cross(self, client):
        create_component(client, "agent-a", TENANT_A)
        create_component(client, "agent-b", TENANT_B)

        a_ids = [c["component_id"] for c in client.get("/components", headers=auth(tenant=TENANT_A)).json()["data"]]
        b_ids = [c["component_id"] for c in client.get("/components", headers=auth(tenant=TENANT_B)).json()["data"]]

        assert a_ids == ["agent-a"]
        assert b_ids == ["agent-b"]

    def test_a_cross_tenant_miss_reads_the_same_as_a_genuine_one(self, client):
        create_component(client, "agent-a", TENANT_A)
        foreign = client.get("/components/agent-a", headers=auth(tenant=TENANT_B))
        absent = client.get("/components/agent-nope", headers=auth(tenant=TENANT_B))
        assert foreign.status_code == absent.status_code == 404


class TestRbacIsEnforced:
    def test_reading_without_the_read_scope_is_403(self, client):
        assert client.get("/components", headers=auth(scopes=[], tenant=TENANT_A)).status_code == 403

    def test_creating_with_only_read_is_403(self, client):
        response = client.post(
            "/components",
            json={"name": "x", "component_type": "agent", "config": AGENT_CONFIG},
            headers=auth(scopes=["components:read"], tenant=TENANT_A),
        )
        assert response.status_code == 403

    def test_deleting_with_only_write_is_403(self, client):
        create_component(client, "agent-a", TENANT_A)
        response = client.delete("/components/agent-a", headers=auth(scopes=["components:write"], tenant=TENANT_A))
        assert response.status_code == 403


class TestTenancyFailsClosed:
    def test_a_token_without_the_tenant_claim_is_refused(self, client):
        """Unscoped is the widest tenancy: served, it would see every tenant's rows."""
        assert client.get("/components", headers=auth(tenant=None)).status_code == 403

    def test_it_is_refused_on_writes_too(self, client):
        response = client.post(
            "/components",
            json={"name": "x", "component_type": "agent", "config": AGENT_CONFIG},
            headers=auth(tenant=None),
        )
        assert response.status_code == 403


class TestWriteValidationStillApplies:
    def test_an_invalid_config_is_422(self, client):
        """EGPT-2.2 runs on the write path ahead of persistence -- `model` is required."""
        response = create_component(client, "agent-bad", TENANT_A, config={"instructions": "hi"})
        assert response.status_code == 422
        assert response.json()["error_code"] == "CONFIG_VALIDATION_ERROR"

    def test_a_rejected_config_is_not_persisted(self, client):
        create_component(client, "agent-bad", TENANT_A, config={"instructions": "hi"})
        assert client.get("/components/agent-bad", headers=auth(tenant=TENANT_A)).status_code == 404


class TestClientSuppliedTenantCannotWin:
    def test_a_tenant_in_the_body_is_ignored(self, client):
        response = client.post(
            "/components",
            json={
                "name": "agent-a",
                "component_id": "agent-a",
                "component_type": "agent",
                "config": AGENT_CONFIG,
                "tenant_id": TENANT_B,
            },
            headers=auth(tenant=TENANT_A),
        )
        assert response.status_code == 201
        # It landed in the principal's tenant, not the one the body asked for.
        assert client.get("/components/agent-a", headers=auth(tenant=TENANT_A)).status_code == 200
        assert client.get("/components/agent-a", headers=auth(tenant=TENANT_B)).status_code == 404


class TestIdCollision:
    def test_a_collision_is_409_for_both_tenants(self, client):
        create_component(client, "agent-a", TENANT_A)
        mine = create_component(client, "agent-a", TENANT_A)
        theirs = create_component(client, "agent-a", TENANT_B)
        assert mine.status_code == theirs.status_code == 409
        assert mine.json() == theirs.json()
