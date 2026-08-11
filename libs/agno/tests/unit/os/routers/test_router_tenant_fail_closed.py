"""Direct router DB reads fail closed when the principal carries no tenant (PR #57 review).

The auth middleware deliberately resolves no tenant for an otherwise-valid JWT that omits the
configured tenant claim, and the agents/teams/workflows routers carry only the authentication
dependency -- so before this gate, listings and direct lookups passed tenant_id=None to the DB,
which means *no predicate*: every tenant's components. These tests pin the fail-closed 403 (and
that the DB is never read), plus the documented in-memory exemption.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from egpt.agent.agent import Agent
from egpt.db.base import BaseDb
from egpt.os.routers.agents.router import get_agent_router
from egpt.os.routers.teams.router import get_team_router
from egpt.os.routers.workflows.router import get_workflow_router
from egpt.os.settings import EgptServingSettings
from egpt.os.tenancy import TENANCY_REQUIRED_DETAIL


def _mock_db():
    abstract = {
        name: MagicMock() for name in dir(BaseDb) if getattr(getattr(BaseDb, name, None), "__isabstractmethod__", False)
    }
    db = type("MockDb", (BaseDb,), abstract)()
    db.id = "test-db"
    db.list_components = MagicMock(return_value=([], 0))
    db.get_config = MagicMock(return_value=None)
    return db


def _client(router_factory, db, *, components=(), tenant_id=None, **os_attrs):
    """An app with tenancy enabled and no auth middleware: a valid principal, no tenant claim.

    current_tenant_id reads request.state.tenant_id (written by AuthMiddleware); with no
    middleware installed the attribute is absent, which is exactly the shape of a JWT that
    omits the configured claim. tenant_id simulates the middleware having resolved one.
    """
    os = SimpleNamespace(
        agents=list(components) if "agents" not in os_attrs else os_attrs.pop("agents"),
        teams=[],
        workflows=[],
        db=db,
        registry=None,
        **os_attrs,
    )
    app = FastAPI()
    app.include_router(router_factory(os))
    app.state.tenant_id_claim = "tid"
    if tenant_id is not None:

        @app.middleware("http")
        async def stamp_tenant(request, call_next):
            request.state.tenant_id = tenant_id
            return await call_next(request)

    return TestClient(app)


def _agent_client(db, **kwargs):
    return _client(lambda os: get_agent_router(os, settings=EgptServingSettings()), db, **kwargs)


class TestAgentsRouterFailsClosed:
    def test_listing_is_403_and_never_reads_the_db(self):
        db = _mock_db()
        response = _agent_client(db).get("/agents")
        assert response.status_code == 403
        assert response.json()["detail"] == TENANCY_REQUIRED_DETAIL
        db.list_components.assert_not_called()

    def test_direct_lookup_of_a_db_only_id_is_403_and_never_reads_the_db(self):
        db = _mock_db()
        response = _agent_client(db).get("/agents/db-agent")
        assert response.status_code == 403
        assert response.json()["detail"] == TENANCY_REQUIRED_DETAIL
        db.get_config.assert_not_called()

    def test_an_in_memory_agent_stays_reachable(self):
        """Code-defined components are deployment-owned, not tenant rows."""
        code_agent = Agent(id="code-agent", name="Code Agent")
        response = _agent_client(_mock_db(), components=[code_agent]).get("/agents/code-agent")
        assert response.status_code == 200
        assert response.json()["id"] == "code-agent"

    def test_a_resolved_tenant_scopes_the_listing(self):
        db = _mock_db()
        response = _agent_client(db, tenant_id="acme").get("/agents")
        assert response.status_code == 200
        assert db.list_components.call_args.kwargs["tenant_id"] == "acme"


class TestTeamsRouterFailsClosed:
    def test_listing_is_403_and_never_reads_the_db(self):
        db = _mock_db()
        client = _client(lambda os: get_team_router(os, settings=EgptServingSettings()), db)
        response = client.get("/teams")
        assert response.status_code == 403
        assert response.json()["detail"] == TENANCY_REQUIRED_DETAIL
        db.list_components.assert_not_called()

    def test_direct_lookup_of_a_db_only_id_is_403(self):
        db = _mock_db()
        client = _client(lambda os: get_team_router(os, settings=EgptServingSettings()), db)
        response = client.get("/teams/db-team")
        assert response.status_code == 403
        db.get_config.assert_not_called()


class TestWorkflowsRouterFailsClosed:
    def test_listing_is_403_and_never_reads_the_db(self):
        db = _mock_db()
        client = _client(lambda os: get_workflow_router(os, settings=EgptServingSettings()), db)
        response = client.get("/workflows")
        assert response.status_code == 403
        assert response.json()["detail"] == TENANCY_REQUIRED_DETAIL
        db.list_components.assert_not_called()

    def test_direct_lookup_of_a_db_only_id_is_403(self):
        db = _mock_db()
        client = _client(lambda os: get_workflow_router(os, settings=EgptServingSettings()), db)
        response = client.get("/workflows/db-workflow")
        assert response.status_code == 403
        db.get_config.assert_not_called()
