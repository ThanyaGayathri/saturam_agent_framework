"""The DB-defined agent registry, end to end, on the PR gate (EGPT-2.10).

The Postgres tier already proves this path against real SQL -- see
``tests/integration/os/{hydration,tenancy,audit}_postgres``. What only *this* file provides is
that the proof runs at all on a pull request: those suites are gated on
``EGPT_INTEGRATION_POSTGRES``, so until EGPT-2.10 they executed nowhere, and a regression in a
feature the product treats as mandatory could merge green.

So this is the fast path, not a second opinion: a real ``SqliteDb`` in a temp file, a real
``EgptOS`` with real JWT middleware, a stub model, and no services -- covering the round trip
(create -> validate -> publish -> hydrate -> run), the audit trail it must leave behind, cache
eviction on publish, adversarial tenant isolation, the negative validation and allow-list
rejections, and hydration of all three component types rather than agents alone.

Where the two tiers overlap the Postgres one is authoritative: it can catch jsonb key ordering,
SERIAL semantics and predicate pushdown that SQLite cannot express. Anything asserted here is
asserted because it is backend-independent.
"""

import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Tuple
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("sqlalchemy", reason="SqliteDb is built on SQLAlchemy")
jwt = pytest.importorskip("jwt", reason="pyjwt is required to mint the tenant-scoped tokens")

from fastapi.testclient import TestClient  # noqa: E402

from egpt.integrations.db.sqlite import SqliteDb  # noqa: E402
from egpt.models.base import Model  # noqa: E402
from egpt.models.message import MessageMetrics  # noqa: E402
from egpt.models.response import ModelResponse  # noqa: E402
from egpt.os import EgptOS  # noqa: E402
from egpt.os.config import AuthorizationConfig  # noqa: E402
from egpt.registry import Registry  # noqa: E402
from egpt.registry.hydrate import ComponentConfigCache  # noqa: E402
from egpt.registry.policy import CapabilityPolicy  # noqa: E402
from egpt.tools.toolkit import Toolkit  # noqa: E402

JWT_SECRET = "test-secret-key-for-registry-roundtrip-0123456789"
OS_ID = "registry-roundtrip-os"
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
    "workflows:read",
    "workflows:run",
]

MOCK_MODEL = {"id": "mock-1", "provider": "mock", "name": "MockModel"}
AGENT_CONFIG: Dict[str, Any] = {"model": dict(MOCK_MODEL)}

MISSING_ID = "no-such-component"


class MockModel(Model):
    """A registry-resolvable model that answers without any provider call.

    Copied from the Postgres hydration suite rather than shared: there is no common stub-model
    module in this repo, and every suite that needs one defines its own.
    """

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


class EchoTools(Toolkit):
    """One registered tool, so tool refs are actually checked.

    ``_check_tool_refs`` is registry-gated: with nothing registered it cannot tell "unknown
    tool" from "nothing is registered here" and deliberately declines to reject. A suite that
    wants to prove an unresolvable tool is refused therefore has to register something first.
    """

    def __init__(self):
        super().__init__(name="echo_tools", tools=[self.echo])

    def echo(self, text: str) -> str:
        """Echo the text back."""
        return text


def make_token(scopes: List[str], tenant: Optional[str], user_id: str = "tester") -> str:
    from datetime import datetime, timedelta, timezone

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


def auth(scopes: Optional[List[str]] = None, tenant: Optional[str] = None) -> Dict[str, str]:
    return {"Authorization": f"Bearer {make_token(scopes if scopes is not None else ALL_SCOPES, tenant)}"}


FORM = {"Content-Type": "application/x-www-form-urlencoded"}


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def db_file() -> Iterator[str]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = handle.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def db(db_file) -> SqliteDb:
    return SqliteDb(db_file=db_file)


@pytest.fixture
def cache() -> ComponentConfigCache:
    """A deliberately long TTL.

    The production default is 30s, which would let a test "prove" eviction by simply outliving
    the entry. At 300s nothing expires inside a test run, so an assertion that a publish is
    served immediately can only pass because the router evicted.
    """
    return ComponentConfigCache(ttl_seconds=300)


@pytest.fixture
def registry(cache) -> Registry:
    return Registry(models=[MockModel()], tools=[EchoTools()], component_config_cache=cache)


def build_os(db: SqliteDb, registry: Registry, **kwargs: Any) -> EgptOS:
    return EgptOS(
        id=OS_ID,
        db=db,
        registry=registry,
        authorization=True,
        authorization_config=AuthorizationConfig(
            verification_keys=[JWT_SECRET],
            algorithm="HS256",
            audience=OS_ID,
            tenant_id_claim=TENANT_CLAIM,
        ),
        **kwargs,
    )


@pytest.fixture
def client(db, registry) -> TestClient:
    return TestClient(build_os(db, registry).get_app())


# =============================================================================
# Helpers
# =============================================================================


def create_component(
    client: TestClient,
    component_id: str,
    tenant: Optional[str],
    *,
    component_type: str = "agent",
    config: Optional[Dict[str, Any]] = None,
    stage: str = "draft",
    name: Optional[str] = None,
):
    """``name`` lands in the config as well as on the component row.

    The two are separate fields and only the config one reaches the hydrated object, so a test
    that sets just the row name and then asserts on ``/agents/{id}`` would be asserting nothing.
    """
    if config is None:
        config = dict(AGENT_CONFIG)
        if name is not None:
            config["name"] = name
    return client.post(
        "/components",
        json={
            "name": name or component_id,
            "component_id": component_id,
            "component_type": component_type,
            "config": config,
            "stage": stage,
        },
        headers=auth(tenant=tenant),
    )


def publish_config(client: TestClient, component_id: str, tenant: Optional[str], config: Dict[str, Any]):
    return client.post(
        f"/components/{component_id}/configs",
        json={"config": config, "stage": "published"},
        headers=auth(tenant=tenant),
    )


def run_agent(client: TestClient, agent_id: str, tenant: Optional[str], message: str = "hello"):
    return client.post(
        f"/agents/{agent_id}/runs",
        data={"message": message, "stream": "false"},
        headers={**auth(tenant=tenant), **FORM},
    )


def audit_actions(client: TestClient, component_id: str, tenant: Optional[str]) -> List[str]:
    response = client.get(f"/components/{component_id}/audit", headers=auth(tenant=tenant))
    assert response.status_code == 200, response.text
    entries = response.json()["data"]
    # audit_id, not created_at, is the ordering key -- created_at is second-resolution and a
    # round trip writes several rows inside one second.
    return [entry["action"] for entry in sorted(entries, key=lambda e: e["audit_id"])]


# =============================================================================
# 1. The round trip
# =============================================================================


class TestTheRoundTrip:
    """create (validated) -> publish -> hydrate -> run, with nothing defined in code."""

    def test_a_definition_that_exists_only_as_rows_runs_a_turn(self, client):
        assert create_component(client, "db-agent", TENANT_A, name="DB Agent").status_code == 201

        published = publish_config(client, "db-agent", TENANT_A, {**AGENT_CONFIG, "name": "DB Agent"})
        assert published.status_code in (200, 201), published.text

        hydrated = client.get("/agents/db-agent", headers=auth(tenant=TENANT_A))
        assert hydrated.status_code == 200, hydrated.text
        assert hydrated.json()["name"] == "DB Agent"

        run = run_agent(client, "db-agent", TENANT_A)
        assert run.status_code == 200, run.text
        assert run.json()["content"] == "mock answer"

    def test_the_draft_is_stored_as_a_draft(self, client):
        """The round trip starts at draft; publish has to be the thing that changes the stage."""
        create_component(client, "db-agent", TENANT_A)

        current = client.get("/components/db-agent/configs/current", headers=auth(tenant=TENANT_A))
        assert current.status_code == 200, current.text
        assert current.json()["stage"] == "draft"

        publish_config(client, "db-agent", TENANT_A, dict(AGENT_CONFIG))
        promoted = client.get("/components/db-agent/configs/current", headers=auth(tenant=TENANT_A))
        assert promoted.json()["stage"] == "published"
        assert promoted.json()["version"] == 2

    def test_a_draft_only_component_is_refused_when_hydration_is_published_only(self, db, registry):
        client = TestClient(build_os(db, registry).get_app())
        registry.hydrate_published_only = True

        assert create_component(client, "draft-agent", TENANT_A, stage="draft").status_code == 201

        run = run_agent(client, "draft-agent", TENANT_A)
        assert run.status_code == 409, run.text
        assert "not published" in run.json()["detail"]

    def test_an_invalid_definition_never_becomes_a_runnable_agent(self, client):
        """The negative half of the round trip: rejection at write time means nothing to hydrate."""
        refused = create_component(client, "no-model", TENANT_A, config={"instructions": "hi"})
        assert refused.status_code == 422

        assert client.get("/components/no-model", headers=auth(tenant=TENANT_A)).status_code == 404
        assert client.get("/agents/no-model", headers=auth(tenant=TENANT_A)).status_code == 404


class TestTheRoundTripIsAudited:
    def test_the_lifecycle_actions_are_recorded_in_order(self, client):
        create_component(client, "db-agent", TENANT_A)
        publish_config(client, "db-agent", TENANT_A, dict(AGENT_CONFIG))

        assert audit_actions(client, "db-agent", TENANT_A) == [
            "component.create",
            "config.create",
            "config.create",
            "config.publish",
        ]

    def test_publishing_a_version_writes_both_the_create_and_the_publish_row(self, client):
        """A new version born published is two events, not one.

        Collapsing them would lose the distinction between "this version was authored" and
        "this version became servable", which is the question the audit trail exists to answer.
        """
        create_component(client, "born-published", TENANT_A, stage="published")

        assert audit_actions(client, "born-published", TENANT_A) == [
            "component.create",
            "config.create",
            "config.publish",
        ]

    def test_a_refused_write_leaves_no_audit_row(self, client):
        create_component(client, "db-agent", TENANT_A)
        before = audit_actions(client, "db-agent", TENANT_A)

        # Published configs are immutable; this must be refused.
        publish_config(client, "db-agent", TENANT_A, dict(AGENT_CONFIG))
        refused = client.patch(
            "/components/db-agent/configs/2",
            json={"config": {**AGENT_CONFIG, "name": "Edited"}},
            headers=auth(tenant=TENANT_A),
        )
        assert refused.status_code == 400, refused.text

        after = audit_actions(client, "db-agent", TENANT_A)
        assert after == [*before, "config.create", "config.publish"]

    def test_the_actor_comes_from_the_token_subject(self, client):
        client.post(
            "/components",
            json={
                "name": "db-agent",
                "component_id": "db-agent",
                "component_type": "agent",
                "config": dict(AGENT_CONFIG),
            },
            headers={"Authorization": f"Bearer {make_token(ALL_SCOPES, TENANT_A, user_id='alice')}"},
        )

        entries = client.get("/components/db-agent/audit", headers=auth(tenant=TENANT_A)).json()["data"]
        assert {entry["actor"] for entry in entries} == {"alice"}


class TestPublishEvictsTheHydrationCache:
    def test_a_newly_published_config_is_served_immediately(self, client, cache):
        create_component(client, "db-agent", TENANT_A, name="Before")
        assert client.get("/agents/db-agent", headers=auth(tenant=TENANT_A)).json()["name"] == "Before"

        publish_config(client, "db-agent", TENANT_A, {**AGENT_CONFIG, "name": "After"})

        second = client.get("/agents/db-agent", headers=auth(tenant=TENANT_A))
        assert second.status_code == 200
        assert second.json()["name"] == "After", (
            "the 300s TTL cannot have expired, so a stale name means the mutation route did not evict"
        )

    def test_the_cache_is_the_one_hydration_reads(self, client, cache):
        """Guards the wiring, not the eviction.

        If EgptOS parked its own cache instead of adopting the registry's, every eviction
        assertion above would pass for the wrong reason -- the test's cache would simply be
        unused. So prove the hydration read populated *this* object.
        """
        create_component(client, "db-agent", TENANT_A)
        client.get("/agents/db-agent", headers=auth(tenant=TENANT_A))

        assert cache.invalidate("db-agent") > 0


# =============================================================================
# 2. Tenant isolation
# =============================================================================


# Every component route that names a component id, with a body where the method needs one.
SCOPED_ROUTES: List[Tuple[str, str, Optional[Dict[str, Any]]]] = [
    ("GET", "/components/{cid}", None),
    ("PATCH", "/components/{cid}", {"name": "Hijacked"}),
    ("DELETE", "/components/{cid}", None),
    ("GET", "/components/{cid}/configs", None),
    ("POST", "/components/{cid}/configs", {"config": AGENT_CONFIG}),
    ("PATCH", "/components/{cid}/configs/2", {"config": AGENT_CONFIG}),
    ("GET", "/components/{cid}/configs/current", None),
    ("GET", "/components/{cid}/configs/1", None),
    ("DELETE", "/components/{cid}/configs/2", None),
    ("POST", "/components/{cid}/configs/1/set-current", None),
    ("GET", "/components/{cid}/audit", None),
]


@pytest.fixture
def seeded(client) -> TestClient:
    """Tenant A owns ``agent-a`` at two versions; tenant B owns nothing."""
    assert create_component(client, "agent-a", TENANT_A, name="Tenant A Agent").status_code == 201
    assert publish_config(client, "agent-a", TENANT_A, dict(AGENT_CONFIG)).status_code in (200, 201)
    return client


def _request(client: TestClient, method: str, path: str, cid: str, tenant: Optional[str]):
    body = next(b for m, p, b in SCOPED_ROUTES if (m, p) == (method, path))
    return client.request(method, path.format(cid=cid), headers=auth(tenant=tenant), json=body)


def _snapshot(db: SqliteDb) -> Dict[str, Any]:
    """Tenant A's persisted rows.

    Read from the db rather than over HTTP on purpose: a route's response envelope carries
    per-request noise (``meta.search_time_ms``) that would make any two snapshots differ, and
    the property under test is about what is *stored*, not about what a route chooses to render.
    """
    return {
        "component": db.get_component("agent-a", tenant_id=TENANT_A),
        "configs": db.list_configs("agent-a", include_config=True, tenant_id=TENANT_A),
        "audit": db.list_component_audit("agent-a", limit=100, tenant_id=TENANT_A),
        "links": db.get_links("agent-a", version=2, tenant_id=TENANT_A),
    }


class TestForeignTenantIsRefused:
    @pytest.mark.parametrize("method,path", [(m, p) for m, p, _ in SCOPED_ROUTES])
    def test_a_foreign_component_is_indistinguishable_from_a_missing_one(self, seeded, method, path):
        """Asserts the property, not a status code.

        Which status a route picks is its own business -- ``/configs`` answers ``200 []`` where
        ``GET /components/{id}`` answers ``404``, and both are defensible. What must hold
        everywhere is that tenant B cannot tell tenant A's component from one that does not
        exist; any divergence turns the route into an existence oracle over A's component names.
        """
        foreign = _request(seeded, method, path, "agent-a", TENANT_B)
        missing = _request(seeded, method, path, MISSING_ID, TENANT_B)

        assert foreign.status_code == missing.status_code, (
            f"{method} {path}: foreign tenant got {foreign.status_code}, nonexistent id got {missing.status_code}"
        )
        assert foreign.text.replace("agent-a", MISSING_ID) == missing.text, (
            f"{method} {path}: response bodies differ beyond the echoed component id"
        )

    def test_listing_excludes_foreign_rows_and_their_count(self, seeded):
        """A correct page with an inflated total still leaks how many components tenant A has."""
        response = seeded.get("/components", headers=auth(tenant=TENANT_B))

        assert response.status_code == 200
        assert response.json()["data"] == []
        assert response.json()["meta"]["total_count"] == 0

    def test_every_write_attempt_leaves_tenant_a_untouched(self, seeded, db):
        """A route can refuse in its response and still have written on the way there."""
        before = _snapshot(db)

        for method, path, _ in SCOPED_ROUTES:
            _request(seeded, method, path, "agent-a", TENANT_B)

        assert _snapshot(db) == before

    def test_the_owner_is_unaffected(self, seeded):
        for method, path, _ in SCOPED_ROUTES:
            _request(seeded, method, path, "agent-a", TENANT_B)

        assert seeded.get("/components/agent-a", headers=auth(tenant=TENANT_A)).status_code == 200
        assert run_agent(seeded, "agent-a", TENANT_A).status_code == 200


class TestCrossTenantHydrationIsRefused:
    def test_tenant_b_cannot_hydrate_or_run_tenant_as_agent(self, seeded):
        assert seeded.get("/agents/agent-a", headers=auth(tenant=TENANT_B)).status_code == 404
        assert run_agent(seeded, "agent-a", TENANT_B).status_code == 404

    def test_the_refusal_matches_a_nonexistent_agent_exactly(self, seeded):
        foreign = run_agent(seeded, "agent-a", TENANT_B)
        missing = run_agent(seeded, MISSING_ID, TENANT_B)

        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()

    def test_the_agent_listing_is_scoped(self, seeded):
        assert [a["id"] for a in seeded.get("/agents", headers=auth(tenant=TENANT_A)).json()] == ["agent-a"]
        assert seeded.get("/agents", headers=auth(tenant=TENANT_B)).json() == []


class TestAValidTokenWithoutATenantClaimFailsClosed:
    """The middleware resolves no tenant for a valid JWT missing the configured claim.

    The DB-backed reads must refuse that rather than run unscoped -- an unscoped read here
    would return every tenant's rows.
    """

    def test_listing_components_is_refused(self, seeded):
        assert seeded.get("/components", headers=auth(tenant=None)).status_code == 403

    def test_listing_agents_is_refused(self, seeded):
        assert seeded.get("/agents", headers=auth(tenant=None)).status_code == 403

    def test_a_direct_hydration_is_refused(self, seeded):
        assert seeded.get("/agents/agent-a", headers=auth(tenant=None)).status_code == 403


# =============================================================================
# 3. Negative validation and allow-lists
# =============================================================================


class TestInvalidConfigsAreRefused:
    """Each rejection carries the code the closed vocabulary in registry/schema.py assigns it.

    The vocabulary itself is unit-tested in ``test_config_schema.py`` against the validator.
    What is new here is that the code survives the router, the middleware and the exception
    handlers instead of being flattened into a bare 500 or 422.
    """

    @pytest.mark.parametrize(
        "case,config,path,code",
        [
            (
                "missing required field",
                {"instructions": "no model here"},
                "config.model",
                "missing_required_field",
            ),
            (
                "unknown model provider",
                {"model": {"id": "x", "provider": "acme-does-not-exist"}},
                "config.model.provider",
                "unresolvable_ref",
            ),
            (
                "unresolvable tool",
                {**AGENT_CONFIG, "tools": [{"name": "no_such_tool"}]},
                "config.tools[0].name",
                "unresolvable_ref",
            ),
            (
                "unknown field",
                {**AGENT_CONFIG, "instruction": "singular typo"},
                "config.instruction",
                "unknown_field",
            ),
            (
                "unknown db reference",
                {**AGENT_CONFIG, "db": {"id": "ghost"}},
                "config.db.id",
                "unresolvable_ref",
            ),
        ],
    )
    def test_the_rejection_names_the_field_and_the_reason(self, client, case, config, path, code):
        response = create_component(client, f"bad-{code}", TENANT_A, config=config)

        assert response.status_code == 422, response.text
        body = response.json()
        assert body["error_code"] == "CONFIG_VALIDATION_ERROR"
        assert (body["errors"][0]["path"], body["errors"][0]["code"]) == (path, code), case

    def test_a_rejected_config_is_not_stored(self, client):
        create_component(client, "rejected", TENANT_A, config={"instructions": "no model"})

        assert client.get("/components/rejected", headers=auth(tenant=TENANT_A)).status_code == 404

    def test_a_registered_tool_is_accepted(self, client):
        """The positive control for the unresolvable-tool case above.

        Without it, a validator that rejected every tool -- registered or not -- would look
        correct.
        """
        response = create_component(
            client, "tool-agent", TENANT_A, config={**AGENT_CONFIG, "tools": [{"name": "echo"}]}
        )

        assert response.status_code == 201, response.text

    def test_a_new_version_is_validated_too(self, client):
        """Validation on create is not enough -- the update route is the other way in."""
        create_component(client, "db-agent", TENANT_A)

        response = client.post(
            "/components/db-agent/configs",
            json={"config": {**AGENT_CONFIG, "instruction": "singular typo"}},
            headers=auth(tenant=TENANT_A),
        )

        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "unknown_field"


class TestPrivilegedToolsAndAllowLists:
    """The capability policy, over HTTP, for the tenant the token names."""

    @pytest.fixture
    def sql_registry(self, cache) -> Registry:
        """A stand-in SQLTools so ``run_sql_query`` classifies unambiguously.

        ``__module__`` is rewritten to the package the real toolkit lives in; classification
        matches on module path, so this is equivalent without importing from integrations.
        """

        class FakeSQLTools(Toolkit):
            def __init__(self):
                super().__init__(name="sql_tools", tools=[self.run_sql_query])

            def run_sql_query(self, query: str) -> str:
                """Run a SQL query."""
                return query

        FakeSQLTools.__module__ = "egpt.integrations.tools.sql.sql"
        return Registry(models=[MockModel()], tools=[FakeSQLTools()], component_config_cache=cache)

    def _client(self, db, registry, document) -> TestClient:
        return TestClient(build_os(db, registry, capability_policy=CapabilityPolicy.from_dict(document)).get_app())

    def test_a_privileged_tool_is_refused_and_nothing_is_stored(self, db, sql_registry):
        document = {"tenants": {TENANT_A: {"models": ["*"], "tools": ["*"], "granted_privileged": []}}}
        client = self._client(db, sql_registry, document)

        response = create_component(
            client, "sql-agent", TENANT_A, config={**AGENT_CONFIG, "tools": [{"name": "run_sql_query"}]}
        )

        assert response.status_code == 422, response.text
        body = response.json()
        assert [(e["path"], e["code"]) for e in body["errors"]] == [("config.tools[0].name", "privileged_tool_blocked")]
        assert client.get("/components/sql-agent", headers=auth(tenant=TENANT_A)).status_code == 404

    def test_a_disallowed_model_is_refused(self, db, sql_registry):
        document = {"tenants": {TENANT_A: {"models": ["anthropic"]}}}
        client = self._client(db, sql_registry, document)

        response = create_component(client, "mock-agent", TENANT_A)

        assert response.status_code == 422, response.text
        assert [e["code"] for e in response.json()["errors"]] == ["capability_not_permitted"]

    def test_a_granted_privileged_tool_is_accepted(self, db, sql_registry):
        document = {"tenants": {TENANT_A: {"models": ["*"], "tools": ["*"], "granted_privileged": ["sql"]}}}
        client = self._client(db, sql_registry, document)

        response = create_component(
            client, "sql-agent", TENANT_A, config={**AGENT_CONFIG, "tools": [{"name": "run_sql_query"}]}
        )

        assert response.status_code == 201, response.text

    def test_the_policy_is_per_tenant_not_global(self, db, sql_registry):
        """The allow-list is only meaningful if two tenants can get different answers."""
        document = {
            "tenants": {
                TENANT_A: {"models": ["*"], "tools": ["*"], "granted_privileged": ["sql"]},
                TENANT_B: {"models": ["*"], "tools": ["*"], "granted_privileged": []},
            }
        }
        client = self._client(db, sql_registry, document)
        sql_config = {**AGENT_CONFIG, "tools": [{"name": "run_sql_query"}]}

        assert create_component(client, "a-sql", TENANT_A, config=sql_config).status_code == 201
        assert create_component(client, "b-sql", TENANT_B, config=sql_config).status_code == 422

    def test_a_grant_revoked_after_the_write_stops_hydration(self, db, sql_registry):
        """The read-side gate is not redundant with the write-side one.

        A definition accepted while a grant was in place is still sitting in the database when
        the grant goes away, and hydration is the only thing left to refuse it.
        """
        sql_config = {**AGENT_CONFIG, "tools": [{"name": "run_sql_query"}]}
        permissive = {"tenants": {TENANT_A: {"models": ["*"], "tools": ["*"], "granted_privileged": ["sql"]}}}
        restrictive = {"tenants": {TENANT_A: {"models": ["*"], "tools": ["*"], "granted_privileged": []}}}

        assert (
            create_component(
                self._client(db, sql_registry, permissive), "sql-agent", TENANT_A, config=sql_config
            ).status_code
            == 201
        )

        after = self._client(db, sql_registry, restrictive)
        assert after.get("/components/sql-agent", headers=auth(tenant=TENANT_A)).status_code == 200

        # 403, not 404: the agent exists, it is just no longer permitted.
        refused = after.get("/agents/sql-agent", headers=auth(tenant=TENANT_A))
        assert refused.status_code == 403, refused.text
        assert "sql" in refused.json()["detail"]


# =============================================================================
# 4. Teams and workflows, not just single agents
# =============================================================================


class TestTeamHydration:
    def test_a_team_of_db_members_hydrates_and_runs(self, client):
        assert create_component(client, "member-1", TENANT_A, name="Member One").status_code == 201
        assert create_component(client, "member-2", TENANT_A, name="Member Two").status_code == 201

        team_config = {
            **AGENT_CONFIG,
            "members": [
                {"type": "agent", "agent_id": "member-1"},
                {"type": "agent", "agent_id": "member-2"},
            ],
        }
        assert (
            create_component(client, "db-team", TENANT_A, component_type="team", config=team_config).status_code == 201
        )

        team = client.get("/teams/db-team", headers=auth(tenant=TENANT_A))
        assert team.status_code == 200, team.text
        assert len(team.json()["members"]) == 2

    def test_a_team_is_not_visible_across_tenants(self, client):
        create_component(client, "member-1", TENANT_A)
        create_component(
            client,
            "db-team",
            TENANT_A,
            component_type="team",
            config={**AGENT_CONFIG, "members": [{"type": "agent", "agent_id": "member-1"}]},
        )

        assert client.get("/teams/db-team", headers=auth(tenant=TENANT_B)).status_code == 404

    def test_a_team_with_an_unresolvable_member_refuses_to_hydrate(self, client):
        """The "non-hydratable config" case, at the tier where it can actually happen.

        Members are authored independently, so a draft may legitimately name one that does not
        exist yet -- write-time validation therefore only warns. Hydration is where a partial
        team has to be refused instead of run half-built.
        """
        assert (
            create_component(
                client,
                "orphan-team",
                TENANT_A,
                component_type="team",
                config={**AGENT_CONFIG, "members": [{"type": "agent", "agent_id": "never-authored"}]},
            ).status_code
            == 201
        )

        response = client.get("/teams/orphan-team", headers=auth(tenant=TENANT_A))

        assert response.status_code == 500, response.text
        assert "never-authored" in response.json()["detail"]

    def test_a_member_hidden_by_tenancy_is_the_same_refusal(self, client):
        """Tenant scoping must not silently shrink a team to the members it can see."""
        assert create_component(client, "b-member", TENANT_B).status_code == 201
        assert (
            create_component(
                client,
                "cross-team",
                TENANT_A,
                component_type="team",
                config={**AGENT_CONFIG, "members": [{"type": "agent", "agent_id": "b-member"}]},
            ).status_code
            == 201
        )

        response = client.get("/teams/cross-team", headers=auth(tenant=TENANT_A))

        assert response.status_code == 500, response.text
        assert "b-member" in response.json()["detail"]


class TestImportAndSeedUnderEnforcedTenancy:
    """Regression cover for the bug EGPT-2.10 surfaced by switching the Postgres job on.

    ``_plan_entry`` probes for an id collision with a deliberately *unscoped* read, because
    ``component_id`` is the global primary key and only an unscoped read predicts what the write
    will do. A db built with ``require_tenant_id`` refuses unscoped component reads -- and
    ``EgptOS`` turns that on for every db the moment a tenant claim is configured. The two
    features met for the first time here, and import returned 500 on every tenancy-enabled
    deployment. Both of the suites that would have caught it were gated on Postgres and so had
    never run.

    These live at the PR-gate tier deliberately: the defect has nothing to do with a backend, and
    pinning it behind the Postgres gate is what let it ship.
    """

    BUNDLE = {
        "format": "egpt.component-bundle",
        "format_version": 1,
        "components": [
            {
                "component_id": "imported-agent",
                "component_type": "agent",
                "name": "Imported",
                "config": AGENT_CONFIG,
                "stage": "published",
                "version": 1,
            },
        ],
    }

    def test_the_tenancy_guard_is_actually_engaged(self, db, client):
        """The premise of every assertion below -- without it they would prove nothing."""
        assert db.require_tenant_id is True

    def test_importing_a_bundle_succeeds(self, client):
        response = client.post(
            "/components/import",
            json={"document": self.BUNDLE},
            headers=auth(tenant=TENANT_A),
        )

        assert response.status_code == 201, response.text
        assert response.json()["created"] == 1

    def test_the_imported_definition_is_owned_by_the_importing_tenant(self, client):
        client.post("/components/import", json={"document": self.BUNDLE}, headers=auth(tenant=TENANT_A))

        assert client.get("/components/imported-agent", headers=auth(tenant=TENANT_A)).status_code == 200
        assert client.get("/components/imported-agent", headers=auth(tenant=TENANT_B)).status_code == 404

    def test_the_imported_definition_hydrates_and_runs(self, client):
        client.post("/components/import", json={"document": self.BUNDLE}, headers=auth(tenant=TENANT_A))

        run = run_agent(client, "imported-agent", TENANT_A)
        assert run.status_code == 200, run.text
        assert run.json()["content"] == "mock answer"

    def test_export_then_import_round_trips(self, client):
        create_component(client, "source-agent", TENANT_A, name="Source", stage="published")

        exported = client.get("/components/source-agent/export", headers=auth(tenant=TENANT_A))
        assert exported.status_code == 200, exported.text

        imported = client.post(
            "/components/import",
            json={"document": exported.json(), "id_prefix": "copy-"},
            headers=auth(tenant=TENANT_A),
        )
        assert imported.status_code == 201, imported.text
        assert client.get("/components/copy-source-agent", headers=auth(tenant=TENANT_A)).status_code == 200

    def test_a_second_import_of_the_same_bundle_is_a_conflict_not_a_crash(self, client):
        """The probe's job is still done: a repeat import is refused, and legibly.

        Under the scoped fallback the caller *does* own the row, so this path is unchanged -- the
        fallback only loses visibility of ids held by *other* tenants.
        """
        assert (
            client.post("/components/import", json={"document": self.BUNDLE}, headers=auth(tenant=TENANT_A)).status_code
            == 201
        )

        again = client.post("/components/import", json={"document": self.BUNDLE}, headers=auth(tenant=TENANT_A))

        assert again.status_code == 409, again.text

    def test_seeding_from_a_directory_works_on_a_strict_db(self, db, client, registry, tmp_path):
        """The non-HTTP entry point into the same planner.

        ``registry`` is passed for the same reason the router passes it: the mock provider is
        only resolvable through it, and validation would otherwise reject the seed for an
        unrelated reason.
        """
        import json as json_module

        from egpt.components.io import seed_from_directory  # noqa: PLC0415

        # A seed file is a whole bundle, not a bare component entry.
        (tmp_path / "agent.seeded.json").write_text(
            json_module.dumps(
                {
                    "format": "egpt.component-bundle",
                    "format_version": 1,
                    "components": [
                        {
                            "component_id": "seeded-agent",
                            "component_type": "agent",
                            "name": "Seeded",
                            "config": AGENT_CONFIG,
                            "stage": "published",
                            "version": 1,
                        }
                    ],
                }
            )
        )

        results = seed_from_directory(db, tmp_path, tenant_id=TENANT_A, registry=registry)

        assert [r.action for r in results] == ["created"]
        assert client.get("/components/seeded-agent", headers=auth(tenant=TENANT_A)).status_code == 200
        assert client.get("/components/seeded-agent", headers=auth(tenant=TENANT_B)).status_code == 404


class TestWorkflowHydration:
    """Workflow hydration had no coverage above a mocked db before EGPT-2.10.

    A workflow's steps reference agents by id and fall back to a scoped DB read, so this is the
    third distinct hydration recursion in the registry -- and the only one that was never run
    against a real database.
    """

    WORKFLOW_CONFIG = {
        "name": "DB Workflow",
        "steps": [{"type": "Step", "name": "step-1", "agent_id": "wf-agent"}],
    }

    def test_a_workflow_whose_steps_are_db_rows_hydrates_and_runs(self, client):
        assert create_component(client, "wf-agent", TENANT_A, name="Workflow Agent").status_code == 201
        assert (
            create_component(
                client, "db-workflow", TENANT_A, component_type="workflow", config=dict(self.WORKFLOW_CONFIG)
            ).status_code
            == 201
        )

        hydrated = client.get("/workflows/db-workflow", headers=auth(tenant=TENANT_A))
        assert hydrated.status_code == 200, hydrated.text

        run = client.post(
            "/workflows/db-workflow/runs",
            data={"message": "hello", "stream": "false"},
            headers={**auth(tenant=TENANT_A), **FORM},
        )
        assert run.status_code == 200, run.text
        assert "mock answer" in run.text

    def test_a_workflow_is_not_visible_across_tenants(self, client):
        create_component(client, "wf-agent", TENANT_A)
        create_component(client, "db-workflow", TENANT_A, component_type="workflow", config=dict(self.WORKFLOW_CONFIG))

        assert client.get("/workflows/db-workflow", headers=auth(tenant=TENANT_B)).status_code == 404
        assert client.get("/workflows", headers=auth(tenant=TENANT_B)).json() == []

    def test_an_unknown_step_type_is_refused_at_write_time(self, client):
        response = create_component(
            client,
            "bad-workflow",
            TENANT_A,
            component_type="workflow",
            config={"name": "Bad", "steps": [{"type": "Nonsense", "name": "step-1"}]},
        )

        assert response.status_code == 422, response.text
        assert response.json()["errors"][0]["code"] == "invalid_value"
