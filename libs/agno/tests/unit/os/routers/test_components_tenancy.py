"""Cross-tenant behaviour of the components CRUD API (EGPT-2.4).

Two properties are pinned here:

1. **A component outside your tenant is indistinguishable from one that does not exist.** Every
   route answers 404, with the same body it gives for a genuinely absent id. The interesting
   case is a config write carrying no config blob -- that used to skip the tenant-scoped
   existence check and surface a foreign component as a 400 from the db layer instead.
2. **Client-supplied tenancy never wins.** No request model declares `tenant_id`, so a value in
   the body or the query string is dropped and the principal's tenant is what reaches the db.

The mock db stands in for the tenant predicate: `get_component` returns a row for the owning
tenant and `None` for anyone else, which is exactly what the real backends do with
`WHERE tenant_id = :tenant_id`.
"""

from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from egpt.db.base import BaseDb
from egpt.os.routers.components import get_components_router
from egpt.os.settings import EgptServingSettings

TENANT_HEADER = "x-test-tenant"
OWNER = "acme"
INTRUDER = "globex"

COMPONENT_ROW = {
    "component_id": "agent-1",
    "component_type": "agent",
    "name": "Test Agent",
    "current_version": 1,
    "created_at": 1700000000,
    "updated_at": 1700000000,
}
CONFIG_ROW = {"component_id": "agent-1", "version": 1, "config": {}, "stage": "draft", "created_at": 1700000000}
AUDIT_ROW = {
    "audit_id": 1,
    "component_id": "agent-1",
    "tenant_id": OWNER,
    "version": 1,
    "action": "config.create",
    "actor": "alice",
    "note": None,
    "details": None,
    "created_at": 1700000000,
}

# Routes that address a specific component, with a body where the model needs one. Every entry
# must answer 404 for a caller outside the owning tenant.
SCOPED_ROUTES = [
    ("GET", "/components/agent-1", None),
    ("PATCH", "/components/agent-1", {"name": "Renamed"}),
    ("DELETE", "/components/agent-1", None),
    ("GET", "/components/agent-1/configs/current", None),
    ("GET", "/components/agent-1/configs/1", None),
    ("DELETE", "/components/agent-1/configs/1", None),
    ("POST", "/components/agent-1/configs/1/set-current", None),
    # The two that used to depend on whether a config blob was attached.
    ("POST", "/components/agent-1/configs", {"config": {}}),
    ("PATCH", "/components/agent-1/configs/1", {"label": "stable"}),
    ("POST", "/components/agent-1/configs", {"label": "stable", "config": {}}),
    ("GET", "/components/agent-1/audit", None),
]


@pytest.fixture
def mock_db():
    """A db where `agent-1` belongs to OWNER and is invisible to everyone else."""
    abstract = {
        name: MagicMock() for name in dir(BaseDb) if getattr(getattr(BaseDb, name, None), "__isabstractmethod__", False)
    }
    db = type("MockDb", (BaseDb,), abstract)()
    db.id = "test-db"

    def get_component(component_id, component_type=None, *, tenant_id):
        if component_id != "agent-1":
            return None
        # None means unscoped, which sees every row -- the real backends' semantics.
        if tenant_id is not None and tenant_id != OWNER:
            return None
        return COMPONENT_ROW

    def scoped(value, absent):
        def _call(component_id, *args, tenant_id=None, **kwargs):
            if component_id != "agent-1" or (tenant_id is not None and tenant_id != OWNER):
                return absent
            return value

        return _call

    db.get_component = MagicMock(side_effect=get_component)
    db.upsert_component = MagicMock(return_value=COMPONENT_ROW)
    db.delete_component = MagicMock(side_effect=scoped(True, False))
    db.create_component_with_config = MagicMock(return_value=(COMPONENT_ROW, CONFIG_ROW))
    db.get_config = MagicMock(side_effect=scoped(CONFIG_ROW, None))
    db.upsert_config = MagicMock(return_value=CONFIG_ROW)
    db.delete_config = MagicMock(side_effect=scoped(True, False))
    db.list_configs = MagicMock(return_value=[])
    db.list_components = MagicMock(return_value=([], 0))
    db.set_current_version = MagicMock(side_effect=scoped(True, False))
    db.list_component_audit = MagicMock(side_effect=scoped(([AUDIT_ROW], 1), ([], 0)))
    return db


def build_client(db) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def set_tenant(request: Request, call_next):
        request.state.tenant_id = request.headers.get(TENANT_HEADER) or None
        return await call_next(request)

    app.include_router(get_components_router(os_db=db, settings=EgptServingSettings()))
    return TestClient(app)


def call(client: TestClient, method: str, path: str, body: Optional[Dict[str, Any]], tenant: Optional[str]):
    headers = {TENANT_HEADER: tenant} if tenant else {}
    return client.request(method, path, json=body, headers=headers)


# =============================================================================
# Cross-tenant access looks like non-existence
# =============================================================================


class TestCrossTenantIsIndistinguishableFromAbsent:
    @pytest.mark.parametrize("method,path,body", SCOPED_ROUTES)
    def test_the_owner_is_served(self, mock_db, method, path, body):
        assert call(build_client(mock_db), method, path, body, OWNER).status_code < 400

    @pytest.mark.parametrize("method,path,body", SCOPED_ROUTES)
    def test_another_tenant_gets_404(self, mock_db, method, path, body):
        assert call(build_client(mock_db), method, path, body, INTRUDER).status_code == 404

    @pytest.mark.parametrize("method,path,body", SCOPED_ROUTES)
    def test_the_body_matches_a_genuine_miss(self, mock_db, method, path, body):
        """Same status and same message, so the response carries no signal that the id exists
        somewhere the caller cannot see."""
        client = build_client(mock_db)
        foreign = call(client, method, path, body, INTRUDER)
        absent = call(client, method, path.replace("agent-1", "no-such-agent"), body, INTRUDER)
        assert foreign.status_code == absent.status_code == 404
        assert foreign.json()["detail"].replace("agent-1", "no-such-agent") == absent.json()["detail"]

    def test_a_config_write_without_a_config_blob_is_also_404(self, mock_db):
        """The regression this hoist exists for: the existence check used to run only when a
        config was attached, so this request reached upsert_config and answered 400."""
        response = call(build_client(mock_db), "PATCH", "/components/agent-1/configs/1", {"label": "x"}, INTRUDER)
        assert response.status_code == 404

    def test_listing_another_tenants_configs_is_empty_not_an_error(self, mock_db):
        """Deliberate: [] is already what an absent component and a component with no configs
        both return, so there is nothing to distinguish."""
        assert call(build_client(mock_db), "GET", "/components/agent-1/configs", None, INTRUDER).status_code == 200


class TestIdCollisionIsUniform:
    """`component_id` is the global primary key, so a collision is always observable as
    "not 201". Answering 409-for-mine and 404-for-theirs would not hide the collision, it would
    label its owner. Both get the same 409."""

    @pytest.fixture
    def colliding_db(self, mock_db):
        mock_db.create_component_with_config = MagicMock(side_effect=ValueError("Component agent-1 already exists"))
        return mock_db

    @pytest.mark.parametrize("tenant", [OWNER, INTRUDER])
    def test_a_collision_is_409(self, colliding_db, tenant):
        body = {"name": "Test Agent", "component_id": "agent-1", "component_type": "agent"}
        response = call(build_client(colliding_db), "POST", "/components", body, tenant)
        assert response.status_code == 409

    def test_both_tenants_get_the_same_body(self, colliding_db):
        client = build_client(colliding_db)
        body = {"name": "Test Agent", "component_id": "agent-1", "component_type": "agent"}
        mine = call(client, "POST", "/components", body, OWNER)
        theirs = call(client, "POST", "/components", body, INTRUDER)
        assert mine.status_code == theirs.status_code
        assert mine.json() == theirs.json()

    def test_other_value_errors_are_still_400(self, mock_db):
        mock_db.create_component_with_config = MagicMock(side_effect=ValueError("stage must be draft or published"))
        body = {"name": "Test Agent", "component_type": "agent"}
        assert call(build_client(mock_db), "POST", "/components", body, OWNER).status_code == 400

    def test_a_label_collision_keeps_its_own_message(self, mock_db):
        """The 409 arm matches the id message exactly, not by substring (PR #55 review).

        `create_component_with_config` raises a second ValueError containing "already exists"
        for a duplicate `label`. A substring test caught it too and replaced the detail with a
        message about `component_id` -- naming a field the caller got right and dropping the
        one they got wrong. The 409 reasoning is about id collisions only; everything else
        keeps the 400 and its real message, as the sibling version routes already do.
        """
        mock_db.create_component_with_config = MagicMock(
            side_effect=ValueError("Label 'v1' already exists for agent-1")
        )
        body = {"name": "Test Agent", "component_id": "agent-1", "component_type": "agent"}
        response = call(build_client(mock_db), "POST", "/components", body, OWNER)
        assert response.status_code == 400
        assert response.json()["detail"] == "Label 'v1' already exists for agent-1"


# =============================================================================
# The principal's tenant always wins
# =============================================================================


class TestClientSuppliedTenantIsIgnored:
    """No request model declares `tenant_id`, and pydantic drops unknown keys -- but that is a
    property worth pinning, because adding the field to a schema would silently make it
    spoofable."""

    def test_a_tenant_in_the_body_does_not_reach_the_db(self, mock_db):
        client = build_client(mock_db)
        body = {"name": "Test Agent", "component_type": "agent", "tenant_id": INTRUDER}
        call(client, "POST", "/components", body, OWNER)
        assert mock_db.create_component_with_config.call_args.kwargs["tenant_id"] == OWNER

    def test_a_tenant_in_the_query_string_does_not_reach_the_db(self, mock_db):
        client = build_client(mock_db)
        client.get(f"/components?tenant_id={INTRUDER}", headers={TENANT_HEADER: OWNER})
        assert mock_db.list_components.call_args.kwargs["tenant_id"] == OWNER

    def test_a_tenant_in_the_body_cannot_reach_another_tenants_component(self, mock_db):
        body = {"name": "Renamed", "tenant_id": OWNER}
        assert call(build_client(mock_db), "PATCH", "/components/agent-1", body, INTRUDER).status_code == 404

    def test_the_response_never_carries_a_tenant(self, mock_db):
        response = call(build_client(mock_db), "GET", "/components/agent-1", None, OWNER)
        assert "tenant_id" not in response.json()


class TestTenantReachesEveryDbCall:
    """The AC is "every CRUD op is tenant-scoped", so assert the kwarg on each call rather than
    inferring it from the status code."""

    @pytest.mark.parametrize(
        "method,path,body,db_method",
        [
            ("GET", "/components", None, "list_components"),
            ("GET", "/components/agent-1", None, "get_component"),
            ("PATCH", "/components/agent-1", {"name": "x"}, "upsert_component"),
            ("DELETE", "/components/agent-1", None, "delete_component"),
            ("GET", "/components/agent-1/configs", None, "list_configs"),
            ("POST", "/components/agent-1/configs", {"config": {}}, "upsert_config"),
            ("GET", "/components/agent-1/configs/current", None, "get_config"),
            ("DELETE", "/components/agent-1/configs/1", None, "delete_config"),
            ("POST", "/components/agent-1/configs/1/set-current", None, "set_current_version"),
        ],
    )
    def test_the_principals_tenant_is_passed(self, mock_db, method, path, body, db_method):
        call(build_client(mock_db), method, path, body, OWNER)
        assert getattr(mock_db, db_method).call_args.kwargs["tenant_id"] == OWNER


# =============================================================================
# The dry run is not a cross-tenant oracle (EGPT-2.9)
# =============================================================================
#
# `POST /components/validate` writes nothing, so it cannot leak rows the way a CRUD route can.
# What it could still leak is *existence*: an unauthenticated-by-tenant read of another tenant's
# component ids. It does not, and these tests pin the two reasons.
#
# Member and step references are checked for shape only -- `_check_members`/`_check_steps` in
# registry/schema.py never look an id up -- so no amount of crafting a team config turns the
# validator into a lookup. The one genuinely tenant-varying input is the capability policy, and
# `test_components_policy.py::TestPolicyOnTheDryRun` covers that the principal's tenant selects
# it. The `require_tenant_id` gate on this route is covered in `test_components_rbac.py`.


class TestTheDryRunIsNotACrossTenantOracle:
    def test_it_never_looks_a_component_up(self, mock_db):
        """No component read means no existence oracle, whatever ids the config names."""
        client = build_client(mock_db)

        for config in (
            {"component_type": "team", "config": {"members": [{"type": "agent", "agent_id": "agent-1"}]}},
            {"component_type": "agent", "config": {"model": "openai:gpt-5.5"}},
            {"component_type": "team", "config": {"members": [{"type": "agent", "agent_id": "agent-1"}]}},
        ):
            call(client, "POST", "/components/validate", config, INTRUDER)

        mock_db.get_component.assert_not_called()
        mock_db.get_config.assert_not_called()
        mock_db.list_components.assert_not_called()

    def test_the_verdict_does_not_depend_on_the_callers_tenant(self, mock_db):
        """The owner and an outsider get byte-identical answers for the same config."""
        client = build_client(mock_db)
        body = {"component_type": "team", "config": {"members": [{"type": "agent", "agent_id": "agent-1"}]}}

        owner = call(client, "POST", "/components/validate", body, OWNER)
        intruder = call(client, "POST", "/components/validate", body, INTRUDER)

        assert owner.status_code == intruder.status_code == 200
        assert owner.json() == intruder.json()

    def test_it_writes_nothing_regardless_of_tenant(self, mock_db):
        client = build_client(mock_db)

        call(
            client,
            "POST",
            "/components/validate",
            {"component_type": "agent", "config": {"model": "openai:gpt-5.5"}, "tenant_id": OWNER},
            INTRUDER,
        )

        mock_db.create_component_with_config.assert_not_called()
        mock_db.upsert_config.assert_not_called()
        mock_db.upsert_component.assert_not_called()
