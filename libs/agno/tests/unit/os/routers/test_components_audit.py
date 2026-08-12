"""The component audit endpoint and actor attribution (EGPT-2.6).

Two surfaces, one ticket:

- ``GET /components/{id}/audit`` -- the read. Same tenant-scoped 404 gate as every other route
  on this router, paginated like ``GET /components``, and never echoing ``tenant_id`` back.
- The **actor** reaching the DB. ``TestTheActorReachesTheDatabase`` walks all seven mutating
  routes and asserts each forwards ``request.state.user_id``. That is the load-bearing half:
  an audit trail whose actor column is always ``None`` records that something happened but not
  who did it, which is the one thing the ticket exists to add.

Mock-``BaseDb`` style, matching ``test_components_router.py`` -- the DB behaviour itself is
pinned against a real SQLite database in ``tests/unit/db/test_component_audit.py``.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from egpt.db.base import BaseDb
from egpt.db.component_audit import ComponentAuditAction
from egpt.os.routers.components import get_components_router
from egpt.os.settings import EgptServingSettings

VALID_AGENT_CONFIG = {"model": "openai:gpt-5.5"}

ACTOR = "alice"

AUDIT_ROW = {
    "audit_id": 2,
    "component_id": "agent-1",
    "tenant_id": "acme",
    "version": 1,
    "action": ComponentAuditAction.CONFIG_PUBLISH.value,
    "actor": ACTOR,
    "note": "ship it",
    "details": {"from_stage": "draft", "previous_current_version": None},
    "created_at": 1234567890,
}


# =============================================================================
# Fixtures
# =============================================================================


def _create_mock_db_class():
    abstract_methods = {}
    for name in dir(BaseDb):
        attr = getattr(BaseDb, name, None)
        if getattr(attr, "__isabstractmethod__", False):
            abstract_methods[name] = MagicMock()
    return type("MockDb", (BaseDb,), abstract_methods)


@pytest.fixture
def mock_db():
    db = _create_mock_db_class()()
    db.id = "test-db"
    db.get_component = MagicMock(
        return_value={
            "component_id": "agent-1",
            "component_type": "agent",
            "name": "Agent",
            "created_at": 1234567890,
        }
    )
    db.list_component_audit = MagicMock(return_value=([AUDIT_ROW], 1))
    db.upsert_component = MagicMock(
        return_value={"component_id": "agent-1", "component_type": "agent", "name": "Agent", "created_at": 1}
    )
    db.delete_component = MagicMock(return_value=True)
    db.create_component_with_config = MagicMock(
        return_value=({"component_id": "agent-1", "component_type": "agent", "name": "Agent", "created_at": 1}, {})
    )
    db.upsert_config = MagicMock(
        return_value={"component_id": "agent-1", "version": 1, "stage": "draft", "config": {}, "created_at": 1}
    )
    db.delete_config = MagicMock(return_value=True)
    db.set_current_version = MagicMock(return_value=True)
    db.to_dict = MagicMock(return_value={"type": "postgres", "id": "test-db"})
    return db


def _build_client(mock_db, actor=None):
    """A client whose requests carry ``actor`` as the authenticated principal.

    The middleware that normally writes ``request.state.user_id`` is not in play on a bare
    ``FastAPI()``, so a tiny middleware stands in for it -- which is also what makes the
    no-principal case (``actor=None``) exercisable.
    """
    app = FastAPI()

    @app.middleware("http")
    async def _stamp_principal(request, call_next):
        if actor is not None:
            request.state.user_id = actor
        return await call_next(request)

    app.include_router(get_components_router(os_db=mock_db, settings=EgptServingSettings()))
    return TestClient(app)


@pytest.fixture
def client(mock_db):
    return _build_client(mock_db, actor=ACTOR)


# =============================================================================
# GET /components/{component_id}/audit
# =============================================================================


class TestAuditEndpoint:
    def test_it_returns_the_history_paginated(self, client, mock_db):
        response = client.get("/components/agent-1/audit")

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["action"] == ComponentAuditAction.CONFIG_PUBLISH.value
        assert body["data"][0]["actor"] == ACTOR
        assert body["data"][0]["details"] == AUDIT_ROW["details"]
        assert body["meta"]["total_count"] == 1
        assert mock_db.list_component_audit.call_args.kwargs["limit"] == 20

    def test_it_preserves_the_order_the_db_returned(self, client, mock_db):
        """The route must not re-sort. ``audit_id`` ordering is the db's job and only it has
        the column."""
        rows = [dict(AUDIT_ROW, audit_id=i, action=ComponentAuditAction.CONFIG_UPDATE.value) for i in (3, 2, 1)]
        mock_db.list_component_audit.return_value = (rows, 3)

        body = client.get("/components/agent-1/audit").json()

        assert [r["audit_id"] for r in body["data"]] == [3, 2, 1]

    def test_it_never_exposes_the_tenant(self, client):
        """No component response model echoes tenancy back at the caller, and the audit row
        carries a ``tenant_id`` that would leak through a permissive model."""
        body = client.get("/components/agent-1/audit").json()

        assert "tenant_id" not in body["data"][0]

    def test_pagination_is_forwarded_as_an_offset(self, client, mock_db):
        client.get("/components/agent-1/audit?page=3&limit=10")

        kwargs = mock_db.list_component_audit.call_args.kwargs
        assert kwargs["limit"] == 10
        assert kwargs["offset"] == 20

    def test_filters_are_forwarded(self, client, mock_db):
        client.get(f"/components/agent-1/audit?action={ComponentAuditAction.CONFIG_PUBLISH.value}&version=2")

        kwargs = mock_db.list_component_audit.call_args.kwargs
        assert kwargs["action"] == ComponentAuditAction.CONFIG_PUBLISH.value
        assert kwargs["version"] == 2

    def test_an_unknown_action_filter_is_rejected(self, client, mock_db):
        """422 rather than an empty page: a typo'd filter must not read as "nothing ever
        happened to this component"."""
        response = client.get("/components/agent-1/audit?action=config.publishh")

        assert response.status_code == 422
        mock_db.list_component_audit.assert_not_called()

    def test_a_missing_component_is_a_404(self, client, mock_db):
        mock_db.get_component.return_value = None

        response = client.get("/components/agent-1/audit")

        assert response.status_code == 404
        mock_db.list_component_audit.assert_not_called()

    def test_the_component_lookup_is_tenant_scoped(self, client, mock_db):
        """The 404 gate is what makes another tenant's component indistinguishable from an
        absent one here, exactly as on every sibling route."""
        client.get("/components/agent-1/audit")

        assert "tenant_id" in mock_db.get_component.call_args.kwargs

    def test_a_db_error_is_a_500(self, client, mock_db):
        mock_db.list_component_audit.side_effect = RuntimeError("boom")

        assert client.get("/components/agent-1/audit").status_code == 500

    def test_limit_is_bounded(self, client):
        assert client.get("/components/agent-1/audit?limit=101").status_code == 422


# =============================================================================
# Actor attribution
# =============================================================================


class TestTheActorReachesTheDatabase:
    """Every mutating route forwards the authenticated principal.

    Parametrised over all seven so a route added later without ``Depends(get_actor)`` shows up
    here rather than as a column of nulls in production.
    """

    MUTATIONS = [
        ("post", "/components", {"json": {"name": "A", "component_type": "agent"}}, "create_component_with_config"),
        ("patch", "/components/agent-1", {"json": {"name": "B"}}, "upsert_component"),
        ("delete", "/components/agent-1", {}, "delete_component"),
        ("post", "/components/agent-1/configs", {"json": {"config": VALID_AGENT_CONFIG}}, "upsert_config"),
        ("patch", "/components/agent-1/configs/1", {"json": {"stage": "published"}}, "upsert_config"),
        ("delete", "/components/agent-1/configs/1", {}, "delete_config"),
        ("post", "/components/agent-1/configs/1/set-current", {}, "set_current_version"),
    ]

    @pytest.mark.parametrize("method,path,kwargs,db_method", MUTATIONS)
    def test_the_principal_is_forwarded(self, mock_db, method, path, kwargs, db_method):
        client = _build_client(mock_db, actor=ACTOR)

        response = getattr(client, method)(path, **kwargs)

        assert response.status_code < 400, response.text
        assert getattr(mock_db, db_method).call_args.kwargs["actor"] == ACTOR

    @pytest.mark.parametrize("method,path,kwargs,db_method", MUTATIONS)
    def test_an_unauthenticated_request_forwards_none(self, mock_db, method, path, kwargs, db_method):
        """``None`` is a legitimate steady state -- an open instance has no principal -- so the
        route must pass it through rather than refuse or invent one."""
        client = _build_client(mock_db, actor=None)

        response = getattr(client, method)(path, **kwargs)

        assert response.status_code < 400, response.text
        assert getattr(mock_db, db_method).call_args.kwargs["actor"] is None
