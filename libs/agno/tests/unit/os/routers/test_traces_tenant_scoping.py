"""Per-tenant trace scoping tests (EGPT-4.3).

Pins:
- A scoped tenant sees its own traces.
- A scoped tenant does NOT see another tenant's traces.
- Cross-tenant GET /traces/{id} returns 404.
- An untenanted principal is refused (403) when tenancy is enabled.
- The filter helpers correctly scope and flatten.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egpt.os.routers.traces.traces import (
    _apply_tenant_scope_to_filter,
    _require_trace_tenant,
    get_traces_router,
)
from egpt.os.settings import EgptServingSettings
from egpt.os.tenancy import TENANCY_REQUIRED_DETAIL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_dbs():
    """Return a dbs dict with a mock default DB."""
    db = MagicMock()
    db.id = "test-db"
    return {"default": [db]}, db


def _app(*, tenant_id=None, tenancy_enabled=True):
    """Build a FastAPI app with the traces router, optional tenant stamping."""
    dbs, db = _mock_dbs()
    settings = EgptServingSettings(os_security_key=None, authorization_enabled=False)
    router = get_traces_router(dbs=dbs, settings=settings)
    app = FastAPI()
    app.include_router(router)

    # Enable tenancy detection
    if tenancy_enabled:
        app.state.tenant_id_claim = "tid"

    @app.middleware("http")
    async def stamp_state(request, call_next):
        if tenant_id is not None:
            request.state.tenant_id = tenant_id
        # Simulate no user isolation (admin-level user)
        request.state.user_isolation_enabled = False
        return await call_next(request)

    return app, db


# ---------------------------------------------------------------------------
# Unit tests for filter helpers
# ---------------------------------------------------------------------------


class TestApplyTenantScopeToFilter:
    """Verify _apply_tenant_scope_to_filter behavior."""

    def test_none_tenant_returns_filter_unchanged(self):
        filt = {"op": "EQ", "key": "status", "value": "OK"}
        assert _apply_tenant_scope_to_filter(filt, None) is filt

    def test_none_filter_returns_tenant_clause(self):
        result = _apply_tenant_scope_to_filter(None, "acme")
        assert result == {"op": "EQ", "key": "tenant_id", "value": "acme"}

    def test_wraps_non_and_filter(self):
        filt = {"op": "EQ", "key": "status", "value": "OK"}
        result = _apply_tenant_scope_to_filter(filt, "acme")
        assert result["op"] == "AND"
        assert len(result["conditions"]) == 2
        assert result["conditions"][0] == {"op": "EQ", "key": "tenant_id", "value": "acme"}
        assert result["conditions"][1] is filt

    def test_flattens_into_existing_and(self):
        """When the filter is already an AND, splices tenant clause in (no extra nesting)."""
        filt = {
            "op": "AND",
            "conditions": [
                {"op": "EQ", "key": "status", "value": "OK"},
                {"op": "EQ", "key": "agent_id", "value": "a1"},
            ],
        }
        result = _apply_tenant_scope_to_filter(filt, "acme")
        assert result["op"] == "AND"
        # 3 conditions: tenant + original 2, not nested AND-in-AND
        assert len(result["conditions"]) == 3
        assert result["conditions"][0] == {"op": "EQ", "key": "tenant_id", "value": "acme"}


class TestRequireTraceTenant:
    """Verify _require_trace_tenant behavior."""

    def test_none_tenant_passes(self):
        """No tenant (admin/pre-tenancy) — no check."""
        trace = SimpleNamespace(tenant_id="any")
        _require_trace_tenant(trace, None)  # Should not raise

    def test_matching_tenant_passes(self):
        trace = SimpleNamespace(tenant_id="acme")
        _require_trace_tenant(trace, "acme")  # Should not raise

    def test_mismatched_tenant_raises_404(self):
        from fastapi import HTTPException

        trace = SimpleNamespace(tenant_id="globex")
        with pytest.raises(HTTPException) as exc_info:
            _require_trace_tenant(trace, "acme")
        assert exc_info.value.status_code == 404

    def test_trace_without_tenant_raises_404_for_scoped_caller(self):
        """Fail-closed: a NULL-tenant trace is hidden from a scoped caller."""
        from fastapi import HTTPException

        trace = SimpleNamespace(tenant_id=None)
        with pytest.raises(HTTPException) as exc_info:
            _require_trace_tenant(trace, "acme")
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Integration tests: traces router with tenant scoping
# ---------------------------------------------------------------------------


class TestTracesRouterTenantFailClosed:
    """An untenanted principal is refused when tenancy is enabled."""

    def test_untenanted_principal_gets_403_on_list(self):
        app, db = _app(tenant_id=None, tenancy_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/traces")
        assert resp.status_code == 403
        assert TENANCY_REQUIRED_DETAIL in resp.json().get("detail", "")

    def test_untenanted_principal_gets_403_on_detail(self):
        app, db = _app(tenant_id=None, tenancy_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/traces/some-trace-id")
        assert resp.status_code == 403

    def test_untenanted_principal_gets_403_on_sessions(self):
        app, db = _app(tenant_id=None, tenancy_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/traces/sessions")
        assert resp.status_code == 403
