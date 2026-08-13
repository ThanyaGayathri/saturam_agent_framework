"""Scope and tenancy enforcement on the components router (EGPT-2.4).

Two gates are under test, both attached at router level by `get_components_router`:

- `require_route_scopes("/components")` -- the `components:read|write|delete` map in
  `os/scopes.py` is enforced by the router itself, not only by whichever middleware happens to
  be installed. It resolves the scope from the *route pattern*, so a mount prefix cannot lift it.
- `require_tenant_id` -- in a deployment that configured a tenant claim, a principal that
  resolves to no tenant is refused rather than served every tenant's rows.

The apps here are bare `FastAPI()` instances with a small middleware standing in for
`AuthMiddleware`, the same shape `test_components_policy.py` uses. That keeps these tests about
what the router enforces rather than about token decoding, and it is also what proves the
important negative: with no principal state at all, both gates stay dormant.
"""

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from egpt.db.base import BaseDb
from egpt.os.auth import _scope_table_for_prefix
from egpt.os.routers.components import get_components_router
from egpt.os.scopes import get_default_scope_mappings, get_required_scopes_for_route
from egpt.os.settings import EgptServingSettings
from egpt.os.tenancy import TENANCY_REQUIRED_DETAIL

COMPONENT_ROW = {
    "component_id": "agent-1",
    "component_type": "agent",
    "name": "Test Agent",
    "current_version": 1,
    "created_at": 1700000000,
    "updated_at": 1700000000,
}
CONFIG_ROW = {"component_id": "agent-1", "version": 1, "config": {}, "stage": "draft", "created_at": 1700000000}

# Every route the router exposes, as (method, path, required scope). The scope column is
# asserted against os/scopes.py by TestScopeMapIsTheSourceOfTruth rather than trusted.
ROUTES: List[tuple] = [
    ("GET", "/components", "components:read"),
    ("POST", "/components", "components:write"),
    ("GET", "/components/agent-1", "components:read"),
    ("PATCH", "/components/agent-1", "components:write"),
    ("DELETE", "/components/agent-1", "components:delete"),
    ("GET", "/components/agent-1/configs", "components:read"),
    ("POST", "/components/agent-1/configs", "components:write"),
    ("GET", "/components/agent-1/configs/current", "components:read"),
    ("GET", "/components/agent-1/configs/1", "components:read"),
    ("PATCH", "/components/agent-1/configs/1", "components:write"),
    ("DELETE", "/components/agent-1/configs/1", "components:delete"),
    ("POST", "/components/agent-1/configs/1/set-current", "components:write"),
    # Reading a component's change history is a read of the component (EGPT-2.6). Note the
    # mapping is required, not decorative: require_route_scopes matches on the route pattern
    # with endswith, "/components/*" does not suffix-match "/components/*/audit", and an
    # unmapped route under this prefix is a hard 403.
    ("GET", "/components/agent-1/audit", "components:read"),
    # The dry run (EGPT-2.9) is a write scope: it evaluates the same capability allow-list a real
    # write does, so it must not be reachable with components:read alone.
    ("POST", "/components/validate", "components:write"),
]

# Bodies that satisfy each write route's request model. Deliberately config-less: a request
# with no config exercises the branch that used to skip the tenant-scoped existence check.
BODIES: Dict[str, Dict[str, Any]] = {
    "POST /components": {"name": "Test Agent", "component_type": "agent"},
    "PATCH /components/agent-1": {"name": "Renamed"},
    "POST /components/agent-1/configs": {"config": {}},
    "PATCH /components/agent-1/configs/1": {"label": "stable"},
    "POST /components/validate": {"component_type": "agent", "config": {}},
}


@pytest.fixture
def mock_db():
    abstract = {
        name: MagicMock() for name in dir(BaseDb) if getattr(getattr(BaseDb, name, None), "__isabstractmethod__", False)
    }
    db = type("MockDb", (BaseDb,), abstract)()
    db.id = "test-db"
    db.get_component = MagicMock(return_value=COMPONENT_ROW)
    db.upsert_component = MagicMock(return_value=COMPONENT_ROW)
    db.delete_component = MagicMock(return_value=True)
    db.create_component_with_config = MagicMock(return_value=(COMPONENT_ROW, CONFIG_ROW))
    db.get_config = MagicMock(return_value=CONFIG_ROW)
    db.upsert_config = MagicMock(return_value=CONFIG_ROW)
    db.delete_config = MagicMock(return_value=True)
    db.list_configs = MagicMock(return_value=[])
    db.list_components = MagicMock(return_value=([], 0))
    db.set_current_version = MagicMock(return_value=True)
    return db


def build_client(
    db,
    *,
    scopes: Optional[List[str]] = None,
    authorization_enabled: bool = False,
    tenant_id: Optional[str] = None,
    tenant_claim: Optional[str] = None,
    mount_prefix: Optional[str] = None,
) -> TestClient:
    """An app whose principal is whatever the arguments say, with no real auth in the way.

    `tenant_claim` stands in for `AuthorizationConfig(tenant_id_claim=...)`: EgptOS stamps it on
    `app.state`, and that is the only thing `tenancy_enabled` consults.
    """
    app = FastAPI()

    @app.middleware("http")
    async def set_principal(request: Request, call_next):
        request.state.authorization_enabled = authorization_enabled
        request.state.scopes = list(scopes or [])
        request.state.tenant_id = tenant_id
        return await call_next(request)

    app.include_router(get_components_router(os_db=db, settings=EgptServingSettings()))

    if mount_prefix is not None:
        parent = FastAPI()
        parent.mount(mount_prefix, app)
        app.state.tenant_id_claim = tenant_claim
        parent.state.tenant_id_claim = tenant_claim
        return TestClient(parent)

    app.state.tenant_id_claim = tenant_claim
    return TestClient(app)


def call(client: TestClient, method: str, path: str, prefix: str = ""):
    key = f"{method} {path}"
    return client.request(method, f"{prefix}{path}", json=BODIES.get(key))


# =============================================================================
# The scope map is the source of truth
# =============================================================================


class TestScopeMapIsTheSourceOfTruth:
    """The gate derives from os/scopes.py; nothing about it is written down twice."""

    def test_the_table_holds_exactly_the_component_entries(self):
        table = _scope_table_for_prefix("/components")
        mappings = get_default_scope_mappings()
        expected = {key for key in mappings if key.split(" ", 1)[1].startswith("/components")}
        assert len(table) == len(expected)

    @pytest.mark.parametrize("method,path,scope", ROUTES)
    def test_each_route_requires_what_scopes_py_declares(self, method, path, scope):
        assert get_required_scopes_for_route(get_default_scope_mappings(), method, path) == [scope]

    def test_every_router_route_is_mapped(self, mock_db):
        """A route added without a scopes.py entry must fail here, not in production."""
        table = _scope_table_for_prefix("/components")
        router = get_components_router(os_db=mock_db, settings=EgptServingSettings())
        for route in router.routes:
            for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
                pattern = route.path.replace("{component_id}", "*").replace("{version}", "*")
                assert any(m == method and pattern.endswith(p) for m, p in table), (
                    f"{method} {route.path} has no scope mapping"
                )

    def test_an_unmapped_prefix_is_rejected_at_wiring_time(self):
        with pytest.raises(ValueError, match="No scope mappings"):
            _scope_table_for_prefix("/not-a-real-prefix")


# =============================================================================
# Scope enforcement
# =============================================================================


class TestDormantWithoutAuthorization:
    """An instance that never enabled RBAC keeps today's behaviour, gate or no gate."""

    @pytest.mark.parametrize("method,path,scope", ROUTES)
    def test_every_route_is_reachable(self, mock_db, method, path, scope):
        client = build_client(mock_db, authorization_enabled=False)
        assert call(client, method, path).status_code != 403


class TestScopeEnforcement:
    @pytest.mark.parametrize("method,path,scope", ROUTES)
    def test_the_mapped_scope_admits(self, mock_db, method, path, scope):
        client = build_client(mock_db, authorization_enabled=True, scopes=[scope])
        assert call(client, method, path).status_code != 403

    @pytest.mark.parametrize("method,path,scope", ROUTES)
    def test_no_scopes_is_refused(self, mock_db, method, path, scope):
        client = build_client(mock_db, authorization_enabled=True, scopes=[])
        response = call(client, method, path)
        assert response.status_code == 403
        assert scope in response.json()["detail"]

    @pytest.mark.parametrize("method,path,scope", ROUTES)
    def test_a_foreign_scope_is_refused(self, mock_db, method, path, scope):
        client = build_client(mock_db, authorization_enabled=True, scopes=["agents:read", "sessions:write"])
        assert call(client, method, path).status_code == 403

    @pytest.mark.parametrize("method,path,scope", ROUTES)
    def test_admin_admits(self, mock_db, method, path, scope):
        client = build_client(mock_db, authorization_enabled=True, scopes=["agent_os:admin"])
        assert call(client, method, path).status_code != 403

    def test_read_does_not_grant_write(self, mock_db):
        client = build_client(mock_db, authorization_enabled=True, scopes=["components:read"])
        assert call(client, "POST", "/components").status_code == 403

    def test_write_does_not_grant_delete(self, mock_db):
        client = build_client(mock_db, authorization_enabled=True, scopes=["components:write"])
        assert call(client, "DELETE", "/components/agent-1").status_code == 403

    def test_listing_is_a_hard_refusal_not_a_filtered_list(self, mock_db):
        """`/components` is outside the agents/teams/workflows family, so the GET-listing
        filtered-access fallback in check_route_scopes must not fire for it."""
        client = build_client(mock_db, authorization_enabled=True, scopes=["components:agent-1:read"])
        assert call(client, "GET", "/components").status_code == 403


class TestMountPrefixCannotBypass:
    """The bypass this gate exists for.

    Starlette leaves `scope["path"]` intact when an app is mounted, so `request.url.path` under
    `parent.mount("/api", os_app)` is `/api/components...`. The middleware keys its lookup on
    that path, finds no mapping, and an unmapped route defaults to allow. Resolving from the
    route pattern instead is what closes it.
    """

    def test_the_middleware_lookup_would_have_allowed_it(self):
        assert get_required_scopes_for_route(get_default_scope_mappings(), "POST", "/api/components") == []

    @pytest.mark.parametrize("method,path,scope", ROUTES)
    def test_the_router_still_refuses_under_a_prefix(self, mock_db, method, path, scope):
        client = build_client(mock_db, authorization_enabled=True, scopes=[], mount_prefix="/api")
        assert call(client, method, path, prefix="/api").status_code == 403

    @pytest.mark.parametrize("method,path,scope", ROUTES)
    def test_the_right_scope_still_admits_under_a_prefix(self, mock_db, method, path, scope):
        client = build_client(mock_db, authorization_enabled=True, scopes=[scope], mount_prefix="/api")
        assert call(client, method, path, prefix="/api").status_code != 403


# =============================================================================
# Tenancy fail-closed
# =============================================================================


class TestTenancyDisabled:
    """No tenant claim configured means no tenancy gate -- exactly today's behaviour."""

    @pytest.mark.parametrize("method,path,scope", ROUTES)
    def test_an_untenanted_principal_is_served(self, mock_db, method, path, scope):
        client = build_client(mock_db, authorization_enabled=True, scopes=[scope], tenant_id=None)
        assert call(client, method, path).status_code != 403


class TestTenancyFailsClosed:
    """With a claim configured, a principal that resolves to no tenant is refused.

    `None` is the *widest* tenancy -- it applies no DB predicate at all -- so serving it would
    turn "my token happens to omit the claim" into "I can read and edit every tenant".
    """

    @pytest.mark.parametrize("method,path,scope", ROUTES)
    def test_no_tenant_is_refused(self, mock_db, method, path, scope):
        client = build_client(mock_db, authorization_enabled=True, scopes=[scope], tenant_id=None, tenant_claim="tid")
        response = call(client, method, path)
        assert response.status_code == 403
        assert response.json()["detail"] == TENANCY_REQUIRED_DETAIL

    @pytest.mark.parametrize("method,path,scope", ROUTES)
    def test_a_tenanted_principal_is_served(self, mock_db, method, path, scope):
        client = build_client(mock_db, authorization_enabled=True, scopes=[scope], tenant_id="acme", tenant_claim="tid")
        assert call(client, method, path).status_code != 403

    def test_the_scope_gate_runs_first(self, mock_db):
        """Ordering matters for the message: a caller missing both gets the scope reason, which
        is the more specific of the two."""
        client = build_client(mock_db, authorization_enabled=True, scopes=[], tenant_id=None, tenant_claim="tid")
        response = call(client, "GET", "/components")
        assert response.status_code == 403
        assert response.json()["detail"] != TENANCY_REQUIRED_DETAIL

    def test_tenancy_is_not_gated_on_authorization(self, mock_db):
        """An instance can scope by tenant without turning RBAC on; the tenancy gate must still
        apply, since it reads app state rather than the RBAC flag."""
        client = build_client(mock_db, authorization_enabled=False, tenant_id=None, tenant_claim="tid")
        assert call(client, "GET", "/components").status_code == 403
