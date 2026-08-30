"""Unit tests for GET /metrics/usage (EGPT-4.5).

Tests cover:
- UsageBreakdown / UsageMetricsResponse schema construction + serialization
- rows_to_usage_breakdowns helper
- require_tenant_id enforcement (403 when tenant missing)
- tenant_id passed from request state to DB layer
- Endpoint returns correct response (sync DB path)
- Endpoint exercises the async DB path
- Date validation (start_date > end_date → 400)
- Default 30-day window when no dates supplied
- NotImplementedError → 501
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from egpt.db.base import AsyncBaseDb
from egpt.os.routers.metrics.metrics import get_metrics_router, rows_to_usage_breakdowns
from egpt.os.routers.metrics.schemas import UsageBreakdown, UsageMetricsResponse
from egpt.os.settings import EgptServingSettings
from egpt.os.tenancy import TENANCY_REQUIRED_DETAIL


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestUsageBreakdown:
    """Verify UsageBreakdown model defaults and construction."""

    def test_defaults(self):
        row = UsageBreakdown()
        assert row.tenant_id is None
        assert row.agent_id is None
        assert row.model_name is None
        assert row.input_tokens == 0
        assert row.output_tokens == 0
        assert row.total_tokens == 0
        assert row.trace_count == 0
        assert row.span_count == 0

    def test_from_db_row(self):
        row = UsageBreakdown(
            tenant_id="t-1",
            agent_id="agent-a",
            model_name="gpt-4o",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            trace_count=3,
            span_count=5,
        )
        assert row.tenant_id == "t-1"
        assert row.model_name == "gpt-4o"
        assert row.total_tokens == 150


class TestUsageMetricsResponse:
    """Verify UsageMetricsResponse model."""

    def test_empty_usage(self):
        resp = UsageMetricsResponse(usage=[])
        assert resp.usage == []
        assert resp.start_date is None

    def test_serialization_round_trip(self):
        row = UsageBreakdown(
            tenant_id="t-1",
            agent_id="a-1",
            model_name="gpt-4o",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            trace_count=1,
            span_count=1,
        )
        resp = UsageMetricsResponse(usage=[row])
        data = resp.model_dump()
        restored = UsageMetricsResponse.model_validate(data)
        assert restored.usage[0].total_tokens == 15


# ---------------------------------------------------------------------------
# Row conversion helper
# ---------------------------------------------------------------------------


class TestRowConversion:
    """Verify the production rows_to_usage_breakdowns helper."""

    def test_convert_db_rows(self):
        raw_rows: List[Dict[str, Any]] = [
            {
                "tenant_id": "t-1",
                "agent_id": "agent-a",
                "model_name": "gpt-4o",
                "input_tokens": 448,
                "output_tokens": 148,
                "total_tokens": 596,
                "trace_count": 5,
                "span_count": 5,
            },
            {
                "tenant_id": "t-2",
                "agent_id": None,
                "model_name": None,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "trace_count": 0,
                "span_count": 0,
            },
        ]
        usage = rows_to_usage_breakdowns(raw_rows)
        assert len(usage) == 2
        assert usage[0].input_tokens == 448
        assert usage[1].agent_id is None

    def test_empty_rows(self):
        assert rows_to_usage_breakdowns([]) == []


# ---------------------------------------------------------------------------
# Helpers for endpoint tests
# ---------------------------------------------------------------------------


def _mock_dbs(rows: Optional[List[Dict[str, Any]]] = None, *, use_async: bool = False):
    """Return (dbs dict, mock db) with get_usage_metrics returning *rows*.

    When *use_async* is True, the mock is spec'd to ``AsyncBaseDb`` so the
    router takes the ``isinstance(db, AsyncBaseDb)`` branch.
    """
    if use_async:
        db = MagicMock(spec=AsyncBaseDb)
        db.get_usage_metrics = AsyncMock(return_value=rows or [])
    else:
        db = MagicMock()
        db.get_usage_metrics.return_value = rows or []
    db.id = "test-db"
    return {"default": [db]}, db


def _app(
    *,
    tenant_id: Optional[str] = "t-1",
    tenancy_enabled: bool = True,
    rows: Optional[List[Dict[str, Any]]] = None,
    use_async: bool = False,
):
    """Build a FastAPI app with the metrics router, optional tenant stamping."""
    dbs, db = _mock_dbs(rows, use_async=use_async)
    settings = EgptServingSettings(os_security_key=None, authorization_enabled=False)
    router = get_metrics_router(dbs=dbs, settings=settings)
    app = FastAPI()
    app.include_router(router)

    if tenancy_enabled:
        app.state.tenant_id_claim = "tid"

    @app.middleware("http")
    async def stamp_tenant(request, call_next):
        if tenant_id is not None:
            request.state.tenant_id = tenant_id
        return await call_next(request)

    return app, db


# ---------------------------------------------------------------------------
# Endpoint tests — sync DB path
# ---------------------------------------------------------------------------


class TestUsageEndpoint:
    """Test the GET /metrics/usage endpoint with mocked sync DB."""

    def test_returns_usage_data(self):
        fake_rows = [
            {
                "tenant_id": "t-1",
                "agent_id": "agent-a",
                "model_name": "gpt-4o",
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "trace_count": 2,
                "span_count": 3,
            },
        ]
        app, db = _app(tenant_id="t-1", rows=fake_rows)
        client = TestClient(app)
        resp = client.get("/metrics/usage")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["usage"]) == 1
        assert data["usage"][0]["tenant_id"] == "t-1"
        assert data["usage"][0]["input_tokens"] == 100

    def test_tenant_id_passed_to_db(self):
        app, db = _app(tenant_id="t-42")
        client = TestClient(app)
        client.get("/metrics/usage")

        db.get_usage_metrics.assert_called_once()
        kwargs = db.get_usage_metrics.call_args.kwargs
        assert kwargs["tenant_id"] == "t-42"

    def test_empty_usage_returns_200(self):
        app, db = _app(tenant_id="t-1")
        client = TestClient(app)
        resp = client.get("/metrics/usage")

        assert resp.status_code == 200
        assert resp.json()["usage"] == []

    def test_agent_filter_passed_to_db(self):
        app, db = _app(tenant_id="t-1")
        client = TestClient(app)
        client.get("/metrics/usage?agent_id=my-agent")

        kwargs = db.get_usage_metrics.call_args.kwargs
        assert kwargs["agent_id"] == "my-agent"

    def test_default_30_day_window(self):
        """When no dates are supplied, a 30-day default window is used."""
        app, db = _app(tenant_id="t-1")
        client = TestClient(app)
        resp = client.get("/metrics/usage")

        assert resp.status_code == 200
        data = resp.json()
        assert data["start_date"] is not None
        assert data["end_date"] is not None


# ---------------------------------------------------------------------------
# Endpoint tests — async DB path
# ---------------------------------------------------------------------------


class TestUsageEndpointAsync:
    """Test the GET /metrics/usage endpoint with mocked async DB."""

    def test_async_db_path(self):
        fake_rows = [
            {
                "tenant_id": "t-1",
                "agent_id": "a",
                "model_name": "gpt-4o",
                "input_tokens": 50,
                "output_tokens": 25,
                "total_tokens": 75,
                "trace_count": 1,
                "span_count": 1,
            },
        ]
        app, db = _app(tenant_id="t-1", rows=fake_rows, use_async=True)
        client = TestClient(app)
        resp = client.get("/metrics/usage")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["usage"]) == 1
        assert data["usage"][0]["total_tokens"] == 75
        # Verify the async mock was awaited
        db.get_usage_metrics.assert_awaited_once()


# ---------------------------------------------------------------------------
# Validation + error handling
# ---------------------------------------------------------------------------


class TestValidation:
    """Verify request validation."""

    def test_inverted_date_range_returns_400(self):
        app, _ = _app(tenant_id="t-1")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/metrics/usage?start_date=2025-02-01T00:00:00Z&end_date=2025-01-01T00:00:00Z")

        assert resp.status_code == 400
        assert "start_date" in resp.json()["detail"]

    def test_not_implemented_returns_501(self):
        """Backend raising NotImplementedError should produce 501."""
        db = MagicMock()
        db.get_usage_metrics.side_effect = NotImplementedError("unsupported")
        db.id = "test-db"
        dbs = {"default": [db]}
        settings = EgptServingSettings(os_security_key=None, authorization_enabled=False)
        router = get_metrics_router(dbs=dbs, settings=settings)

        app = FastAPI()
        app.include_router(router)
        app.state.tenant_id_claim = "tid"

        @app.middleware("http")
        async def stamp(request, call_next):
            request.state.tenant_id = "t-1"
            return await call_next(request)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/metrics/usage")
        assert resp.status_code == 501


# ---------------------------------------------------------------------------
# Tenant enforcement
# ---------------------------------------------------------------------------


class TestTenantEnforcement:
    """Verify require_tenant_id blocks untenanted callers."""

    def test_no_tenant_returns_403(self):
        app, _ = _app(tenant_id=None, tenancy_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/metrics/usage")

        assert resp.status_code == 403
        assert TENANCY_REQUIRED_DETAIL in resp.json().get("detail", "")
