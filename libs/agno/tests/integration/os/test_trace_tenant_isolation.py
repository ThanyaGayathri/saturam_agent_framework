"""Integration test: cross-tenant trace isolation (EGPT-4.3c, aicoe-tracker#203).

Exercises the router + DB enforcement layer against a real SQLite backend
with two seeded tenants. Assumes request.state.tenant_id is already populated
correctly (stamps it directly via middleware, does not exercise JWT resolution).

Covers:
- Tenant A sees its own traces, not tenant B's
- Cross-tenant GET /traces/{id} returns 404
- NULL-tenant traces excluded from scoped listings
- Untenanted principal gets 403 when tenancy is enabled
- Pre-tenancy deployment sees all traces (backward compat)
- POST /traces/search respects tenant scoping
- Empty-string tenant normalised to None

Scope: SQLite only. Postgres/ClickHouse-specific paths require their own
integration tests with real backends.
"""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egpt.integrations.db.sqlite import SqliteDb
from egpt.os.routers.traces.traces import get_traces_router
from egpt.os.settings import EgptServingSettings
from egpt.os.tenancy import TENANCY_REQUIRED_DETAIL
from egpt.tracing.schemas import Trace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)


def _seed_trace(db: SqliteDb, trace_id: str, tenant_id: str, name: str = "TestAgent.run") -> None:
    """Insert a trace directly into the DB with a given tenant_id."""
    trace = Trace(
        trace_id=trace_id,
        name=name,
        status="OK",
        start_time=NOW,
        end_time=NOW,
        duration_ms=100,
        total_spans=1,
        error_count=0,
        run_id=f"run-{trace_id}",
        session_id=f"sess-{trace_id}",
        user_id="user-1",
        agent_id="agent-1",
        team_id=None,
        workflow_id=None,
        created_at=NOW,
        tenant_id=tenant_id,
    )
    db.upsert_trace(trace)


def _app(db: SqliteDb, *, tenant_id=None, tenancy_enabled=True):
    """Build a FastAPI app with the traces router backed by a real SQLite DB."""
    dbs = {"default": [db]}
    settings = EgptServingSettings(os_security_key=None, authorization_enabled=False)
    router = get_traces_router(dbs=dbs, settings=settings)
    app = FastAPI()
    app.include_router(router)

    if tenancy_enabled:
        app.state.tenant_id_claim = "tid"

    @app.middleware("http")
    async def stamp_state(request, call_next):
        if tenant_id is not None:
            request.state.tenant_id = tenant_id
        request.state.user_isolation_enabled = False
        return await call_next(request)

    return app


@pytest.fixture
def db(tmp_path):
    """A fresh SQLite DB with traces table created."""
    instance = SqliteDb(db_file=str(tmp_path / "trace_isolation_test.db"))
    # Force table creation
    instance._get_table(table_type="traces", create_table_if_not_found=True)
    instance._get_table(table_type="spans", create_table_if_not_found=True)
    yield instance
    instance.db_engine.dispose()


@pytest.fixture
def seeded_db(db):
    """DB with two tenants' traces seeded."""
    _seed_trace(db, "t-acme-1", "acme", name="AcmeAgent.run")
    _seed_trace(db, "t-acme-2", "acme", name="AcmeAgent.run")
    _seed_trace(db, "t-globex-1", "globex", name="GlobexAgent.run")
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTenantSeesOwnTraces:
    """A scoped tenant sees its own traces and only its own."""

    def test_acme_sees_own_traces(self, seeded_db):
        app = _app(seeded_db, tenant_id="acme")
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/traces")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        assert "data" in data, f"Expected PaginatedResponse with 'data' key, got: {list(data.keys())}"
        trace_ids = [t["trace_id"] for t in data["data"]]
        assert "t-acme-1" in trace_ids
        assert "t-acme-2" in trace_ids
        assert "t-globex-1" not in trace_ids

    def test_globex_sees_own_traces(self, seeded_db):
        app = _app(seeded_db, tenant_id="globex")
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/traces")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        assert "data" in data, f"Expected PaginatedResponse with 'data' key, got: {list(data.keys())}"
        trace_ids = [t["trace_id"] for t in data["data"]]
        assert "t-globex-1" in trace_ids
        assert "t-acme-1" not in trace_ids
        assert "t-acme-2" not in trace_ids


class TestCrossTenantDetailReturns404:
    """A scoped tenant cannot read another tenant's trace by ID."""

    def test_acme_cannot_read_globex_trace(self, seeded_db):
        app = _app(seeded_db, tenant_id="acme")
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/traces/t-globex-1")
        assert resp.status_code == 404

    def test_acme_can_read_own_trace(self, seeded_db):
        app = _app(seeded_db, tenant_id="acme")
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/traces/t-acme-1")
        assert resp.status_code == 200


class TestUntenantedPrincipalRefused:
    """An untenanted principal is 403'd when tenancy is enabled."""

    def test_list_traces_403(self, seeded_db):
        app = _app(seeded_db, tenant_id=None, tenancy_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/traces")
        assert resp.status_code == 403
        assert TENANCY_REQUIRED_DETAIL in resp.json().get("detail", "")

    def test_detail_trace_403(self, seeded_db):
        app = _app(seeded_db, tenant_id=None, tenancy_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/traces/t-acme-1")
        assert resp.status_code == 403


class TestNullTenantTraceFailsClosed:
    """A NULL-tenant trace must not be visible to a scoped caller."""

    def test_null_tenant_trace_excluded_from_listing(self, db):
        """Traces with tenant_id=NULL must not appear in a scoped listing."""
        _seed_trace(db, "t-null-1", None, name="LegacyAgent.run")
        _seed_trace(db, "t-acme-1", "acme", name="AcmeAgent.run")

        app = _app(db, tenant_id="acme")
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/traces")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "data" in data
        trace_ids = [t["trace_id"] for t in data["data"]]
        assert "t-acme-1" in trace_ids
        assert "t-null-1" not in trace_ids, "NULL-tenant trace leaked to scoped caller"

    def test_null_tenant_trace_detail_returns_404(self, db):
        """A scoped caller cannot read a NULL-tenant trace by ID."""
        _seed_trace(db, "t-null-2", None, name="LegacyAgent.run")

        app = _app(db, tenant_id="acme")
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/traces/t-null-2")
        assert resp.status_code == 404


class TestPreTenancyDeploymentUnaffected:
    """Without tenancy enabled, traces are unscoped (backward compatible)."""

    def test_no_tenancy_sees_all_traces(self, seeded_db):
        app = _app(seeded_db, tenant_id=None, tenancy_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/traces")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        assert "data" in data, f"Expected PaginatedResponse with 'data' key, got: {list(data.keys())}"
        trace_ids = [t["trace_id"] for t in data["data"]]
        assert len(trace_ids) == 3


class TestEmptyStringTenantEdgeCase:
    """An empty-string tenant_id should be treated as no tenant (None)."""

    def test_empty_string_tenant_treated_as_untenanted(self, seeded_db):
        """Empty-string tenant should be refused (403) same as None."""
        app = _app(seeded_db, tenant_id="", tenancy_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/traces")
        # Empty-string tenant should be normalised to None by current_tenant_id,
        # which means require_tenant_id fires and returns 403.
        assert resp.status_code == 403


class TestSearchEndpointTenantIsolation:
    """POST /traces/search respects tenant scoping."""

    def test_search_scoped_to_tenant(self, seeded_db):
        """A tenant-scoped search returns only the caller's traces."""
        app = _app(seeded_db, tenant_id="acme")
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/traces/search",
            json={"filter": None, "group_by": "run", "limit": 50, "page": 1},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "data" in data, f"Expected 'data' key, got: {list(data.keys())}"
        trace_ids = [t["trace_id"] for t in data["data"]]
        assert "t-acme-1" in trace_ids
        assert "t-acme-2" in trace_ids
        assert "t-globex-1" not in trace_ids, "Cross-tenant trace leaked via search"

    def test_search_with_caller_filter_still_scoped(self, seeded_db):
        """A caller-supplied filter is AND-ed with the tenant clause."""
        app = _app(seeded_db, tenant_id="acme")
        client = TestClient(app, raise_server_exceptions=False)

        # Filter for a specific status — tenant scoping should still apply
        resp = client.post(
            "/traces/search",
            json={
                "filter": {"op": "EQ", "key": "status", "value": "OK"},
                "group_by": "run",
                "limit": 50,
                "page": 1,
            },
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "data" in data
        trace_ids = [t["trace_id"] for t in data["data"]]
        assert "t-globex-1" not in trace_ids, "Caller filter bypassed tenant scoping"
