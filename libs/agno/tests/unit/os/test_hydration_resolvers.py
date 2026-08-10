"""resolve_agent / resolve_team / resolve_workflow tenant semantics (EGPT-2.5).

The resolvers are the seam between HTTP and hydration: they read the caller's tenant off the
request the same way the components router does, scope the DB arm to it, and fail closed when
the deployment has opted into tenancy but the principal carries no tenant. The in-memory list
is exempt -- code-defined agents are deployment-owned, not tenant rows.
"""

import copy
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from egpt.agent.agent import Agent
from egpt.db.base import BaseDb
from egpt.os.tenancy import TENANCY_REQUIRED_DETAIL
from egpt.os.utils import get_agent_by_id_async, resolve_agent
from egpt.registry import Registry
from egpt.registry.hydrate import ComponentConfigCache

ROW = {
    "component_id": "db-agent",
    "config": {"name": "DB Agent"},
    "tenant_id": "acme",
    "version": 1,
    "stage": "published",
}


def _mock_db(row=ROW):
    abstract = {
        name: MagicMock() for name in dir(BaseDb) if getattr(getattr(BaseDb, name, None), "__isabstractmethod__", False)
    }
    db = type("MockDb", (BaseDb,), abstract)()
    db.id = "test-db"

    def get_config(component_id=None, version=None, label=None, tenant_id=None, **_):
        if row is None:
            return None
        if tenant_id is not None and tenant_id != row.get("tenant_id"):
            return None
        return copy.deepcopy(row)

    db.get_config = MagicMock(side_effect=get_config)
    return db


def _request(tenant_id=None, tenancy_on=False):
    """The two attributes the tenancy helpers read, shaped like a real Request."""
    request = MagicMock()
    request.state = MagicMock(spec=[])  # empty spec: getattr falls back to default
    if tenant_id is not None:
        request.state = MagicMock()
        request.state.tenant_id = tenant_id
    request.app.state.tenant_id_claim = "tid" if tenancy_on else None
    return request


class TestFailClosed:
    async def test_tenancy_on_and_no_tenant_refuses_the_db_arm(self):
        with pytest.raises(HTTPException) as excinfo:
            await resolve_agent("db-agent", [], db=_mock_db(), request=_request(tenancy_on=True))
        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == TENANCY_REQUIRED_DETAIL

    async def test_an_in_memory_agent_is_exempt_from_the_gate(self):
        code_agent = Agent(id="code-agent", name="Code Agent")
        resolved = await resolve_agent("code-agent", [code_agent], db=_mock_db(), request=_request(tenancy_on=True))
        assert resolved.id == "code-agent"

    async def test_tenancy_off_keeps_todays_unscoped_behavior(self):
        resolved = await resolve_agent("db-agent", [], db=_mock_db(), request=_request(tenancy_on=False))
        assert resolved.id == "db-agent"

    async def test_no_request_is_a_programmatic_caller_and_passes(self):
        resolved = await resolve_agent("db-agent", [], db=_mock_db(), request=None)
        assert resolved.id == "db-agent"


class TestTenantScoping:
    async def test_the_request_tenant_scopes_the_hydration(self):
        db = _mock_db()
        resolved = await resolve_agent("db-agent", [], db=db, request=_request(tenant_id="acme", tenancy_on=True))
        assert resolved.id == "db-agent"
        assert db.get_config.call_args.kwargs["tenant_id"] == "acme"

    async def test_a_cross_tenant_component_is_a_plain_404(self):
        """Byte-identical to nonexistent -- no oracle."""
        db = _mock_db()
        with pytest.raises(HTTPException) as excinfo:
            await resolve_agent("db-agent", [], db=db, request=_request(tenant_id="globex", tenancy_on=True))
        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == "Agent not found"

    async def test_an_explicit_tenant_id_wins_over_the_request(self):
        """The override is for callers without a Request (e.g. websocket paths)."""
        db = _mock_db()
        await resolve_agent(
            "db-agent", [], db=db, request=_request(tenant_id="globex", tenancy_on=True), tenant_id="acme"
        )
        assert db.get_config.call_args.kwargs["tenant_id"] == "acme"


class TestStatusMapping:
    async def test_a_draft_under_published_only_is_a_409(self):
        db = _mock_db(row={**ROW, "stage": "draft"})
        registry = Registry(hydrate_published_only=True)
        with pytest.raises(HTTPException) as excinfo:
            await resolve_agent("db-agent", [], db=db, registry=registry, request=None)
        assert excinfo.value.status_code == 409
        assert "not published" in excinfo.value.detail

    async def test_an_existing_but_broken_row_is_a_500_with_the_cause(self):
        db = _mock_db(row={**ROW, "config": None})
        with pytest.raises(HTTPException) as excinfo:
            await resolve_agent("db-agent", [], db=db, request=None)
        assert excinfo.value.status_code == 500
        assert "db-agent" in excinfo.value.detail

    async def test_a_genuinely_missing_component_stays_a_404(self):
        with pytest.raises(HTTPException) as excinfo:
            await resolve_agent("missing", [], db=_mock_db(row=None), request=None)
        assert excinfo.value.status_code == 404


class TestAsyncGetterOffloadsTheDbRead:
    async def test_the_db_arm_runs_in_the_threadpool(self, monkeypatch):
        """The component getter is sync; the async variant must not call it on the loop."""
        calls = {}

        async def fake_run_in_threadpool(fn, **kwargs):
            calls["fn"] = fn
            calls["kwargs"] = kwargs
            return None

        import egpt.os.utils as os_utils

        monkeypatch.setattr(os_utils, "run_in_threadpool", fake_run_in_threadpool)
        await get_agent_by_id_async("db-agent", [], db=_mock_db(), tenant_id="acme")

        from egpt.agent.agent import get_agent_by_id as component_getter

        assert calls["fn"] is component_getter
        assert calls["kwargs"]["tenant_id"] == "acme"

    async def test_the_cache_still_collapses_repeat_async_hydrations(self):
        db = _mock_db()
        registry = Registry(component_config_cache=ComponentConfigCache(ttl_seconds=30))
        await get_agent_by_id_async("db-agent", [], db=db, registry=registry, tenant_id="acme")
        await get_agent_by_id_async("db-agent", [], db=db, registry=registry, tenant_id="acme")
        assert db.get_config.call_count == 1
