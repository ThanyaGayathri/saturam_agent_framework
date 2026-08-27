"""Adversarial-tenant isolation against a real Postgres (EGPT-2.7).

``tests/unit/db/test_component_tenant_adversarial.py`` proves the same isolation on SQLite. This
suite exists because that is not sufficient evidence for Postgres: the two backends carry
independent implementations of all fifteen component methods, roughly ninety tenant predicates
each, and they differ in ways that matter here -- Postgres has a ``deleted_at`` column on
``component_configs`` that SQLite lacks, uses JSONB where SQLite uses JSON, and declares foreign
keys on ``component_links`` that SQLite does not. A predicate could be correct on one and missing
on the other.

Two levels, because they can fail independently:

1. **Through HTTP with real JWTs.** A complete EgptOS, RBAC and tenancy both on, tenant B's real
   token against tenant A's component ids. Covers the whole stack, and additionally pins that a
   foreign component 404s rather than 403s -- a 403 would confirm the id exists.
2. **Directly against ``PostgresDb``.** The same fifteen-method sweep as the unit suite, with
   tenant A's rows verified intact by raw SQL afterwards. This is the layer the issue is about,
   and the HTTP tests cannot reach a method the router does not expose.

Gated on ``EGPT_INTEGRATION_POSTGRES``. Bring the database up with::

    ./cookbook/scripts/run_pgvector.sh
    EGPT_INTEGRATION_POSTGRES=1 pytest \\
        libs/agno/tests/integration/os/tenancy_postgres/test_adversarial_tenant_isolation.py -v
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from egpt.db.base import ComponentType
from egpt.db.component_scope import TenantScopeRequiredError
from egpt.os import EgptOS
from egpt.os.config import AuthorizationConfig

jwt = pytest.importorskip("jwt", reason="pyjwt is required for the tenancy integration suite")

JWT_SECRET = "test-secret-key-for-tenancy-tests-0123456789"
OS_ID = "adversarial-test-os"
TENANT_CLAIM = "tid"

TENANT_A = "acme"
TENANT_B = "globex"

ALL_SCOPES = ["components:read", "components:write", "components:delete"]

AGENT_CONFIG = {"model": {"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"}}


# ============================================================================
# FIXTURES / HELPERS
# ============================================================================


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


@pytest.fixture(autouse=True)
def _no_ambient_strict_mode(monkeypatch):
    """Clear ``EGPT_REQUIRE_TENANT_ID`` for every test in this module.

    ``BaseDb`` reads that variable at construction, so a developer or CI job exporting it would
    otherwise silently change what these tests exercise: the strict-mode assertions would pass for
    the wrong reason, and the two that pin the *default* as off would fail. Tests that want strict
    mode ask for it explicitly, which is also what the assertions should be reading.
    """
    monkeypatch.delenv("EGPT_REQUIRE_TENANT_ID", raising=False)
    monkeypatch.delenv("AGNO_REQUIRE_TENANT_ID", raising=False)


@pytest.fixture
def adversarial_db(postgres_engine):
    """A PostgresDb with tables unique to this run, so parallel runs cannot collide."""
    from egpt.integrations.db.postgres import PostgresDb  # noqa: PLC0415

    suffix = uuid4().hex[:8]
    # The component table names are kept very short on purpose. Postgres truncates at 63
    # characters, and the links table's own name is the stem of a derived FK constraint name
    # (``<table>_parent_component_id_parent_version_fkey``, 40 characters of suffix), so a
    # descriptive name here fails at CREATE TABLE rather than at assertion time.
    return PostgresDb(
        db_engine=postgres_engine,
        db_schema="test_schema",
        session_table=f"adv_sessions_{suffix}",
        memory_table=f"adv_memories_{suffix}",
        metrics_table=f"adv_metrics_{suffix}",
        eval_table=f"adv_evals_{suffix}",
        knowledge_table=f"adv_knowledge_{suffix}",
        components_table=f"c_{suffix}",
        component_configs_table=f"cc_{suffix}",
        component_links_table=f"cl_{suffix}",
        component_audit_table=f"ca_{suffix}",
    )


@pytest.fixture
def client(adversarial_db) -> TestClient:
    os_app = EgptOS(
        id=OS_ID,
        db=adversarial_db,
        authorization=True,
        authorization_config=AuthorizationConfig(
            verification_keys=[JWT_SECRET],
            algorithm="HS256",
            audience=OS_ID,
            tenant_id_claim=TENANT_CLAIM,
        ),
    )
    return TestClient(os_app.get_app())


@pytest.fixture
def seeded(adversarial_db):
    """Tenant A owns a published component with a child link, a draft version, and history."""
    adversarial_db.create_component_with_config(
        component_id="child-a",
        component_type=ComponentType.AGENT,
        name="Child A",
        config=AGENT_CONFIG,
        stage="published",
        tenant_id=TENANT_A,
    )
    adversarial_db.create_component_with_config(
        component_id="agent-a",
        component_type=ComponentType.TEAM,
        name="Agent A",
        config=AGENT_CONFIG,
        stage="published",
        links=[
            {
                "link_kind": "member",
                "link_key": "child",
                "child_component_id": "child-a",
                "child_version": 1,
                "position": 0,
            }
        ],
        tenant_id=TENANT_A,
    )
    adversarial_db.upsert_config(
        component_id="agent-a",
        config=dict(AGENT_CONFIG, note="v2"),
        stage="draft",
        tenant_id=TENANT_A,
    )
    return adversarial_db


def _component_table_names(db):
    return (
        db.components_table_name,
        db.component_configs_table_name,
        db.component_links_table_name,
        db.component_audit_table_name,
    )


def _snapshot(db):
    """Every row of every component table, straight out of Postgres."""
    snap = {}
    with db.Session() as sess:
        for table in _component_table_names(db):
            rows = sess.execute(text(f"SELECT * FROM test_schema.{table} ORDER BY 1, 2")).fetchall()
            snap[table] = [tuple(str(v) for v in r) for r in rows]
    return snap


def _sweep_every_method_as(db, tenant_id):
    """Call all fifteen component methods against tenant A's ids as ``tenant_id``.

    Exceptions are swallowed on purpose: the attacker tries everything and does not care what
    comes back. The caller asserts on the state left behind.
    """
    attempts = [
        lambda: db.get_component("agent-a", tenant_id=tenant_id),
        lambda: db.list_components(tenant_id=tenant_id),
        lambda: db.get_config("agent-a", tenant_id=tenant_id),
        lambda: db.list_configs("agent-a", tenant_id=tenant_id),
        lambda: db.get_links("agent-a", version=1, tenant_id=tenant_id),
        lambda: db.get_dependents("child-a", tenant_id=tenant_id),
        lambda: db.load_component_graph("agent-a", tenant_id=tenant_id),
        lambda: db.list_component_audit("agent-a", tenant_id=tenant_id),
        lambda: db.upsert_component(component_id="agent-a", name="Hijacked", tenant_id=tenant_id),
        lambda: db.upsert_config("agent-a", config={"model": "evil"}, tenant_id=tenant_id),
        lambda: db.create_component_with_config(
            component_id="agent-a",
            component_type=ComponentType.AGENT,
            name="Hijacked",
            config={"model": "evil"},
            tenant_id=tenant_id,
        ),
        lambda: db.set_current_version("agent-a", version=1, tenant_id=tenant_id),
        lambda: db.delete_config("agent-a", version=2, tenant_id=tenant_id),
        lambda: db.delete_component("agent-a", hard_delete=True, tenant_id=tenant_id),
        # The fifteenth method: deliberately unscoped (aicoe-tracker#176), no tenant_id
        # parameter at all. Included so the sweep exercises it too -- the assertion is that
        # seeing tenant A's row changes nothing about tenant A's state, not that the call is
        # refused.
        lambda: db.component_id_is_taken("agent-a"),
    ]
    for attempt in attempts:
        try:
            attempt()
        except Exception:
            pass


# ============================================================================
# 1. THROUGH HTTP, WITH REAL JWTs
# ============================================================================


# Every component route that names a component id, with a body where the method needs one.
# The equivalent unit table (tests/unit/os/routers/test_components_tenancy.py) runs against a
# mock; this one runs against real SQL.
SCOPED_ROUTES = [
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

MISSING_ID = "no-such-component"


def _request(client, method, path, cid, tenant):
    body = next(b for m, p, b in SCOPED_ROUTES if (m, p) == (method, path))
    return client.request(method, path.format(cid=cid), headers=auth(tenant=tenant), json=body)


class TestForeignTenantIsRefusedOverHttp:
    @pytest.mark.parametrize("method,path", [(m, p) for m, p, _ in SCOPED_ROUTES])
    def test_a_foreign_component_is_indistinguishable_from_a_missing_one(self, client, seeded, method, path):
        """The property that matters, asserted instead of a hardcoded status code.

        Which status a route returns is its own business -- ``/configs`` answers ``200 []`` where
        ``GET /components/{id}`` answers ``404``, and both are defensible. What must hold on every
        route is that tenant B cannot *tell the difference* between a component belonging to
        tenant A and one that does not exist. Any divergence -- a different status, a different
        error shape, a populated body -- confirms the id is real and turns the route into an
        existence oracle over tenant A's component names.
        """
        foreign = _request(client, method, path, "agent-a", TENANT_B)
        missing = _request(client, method, path, MISSING_ID, TENANT_B)

        assert foreign.status_code == missing.status_code, (
            f"{method} {path}: foreign tenant got {foreign.status_code}, nonexistent id got "
            f"{missing.status_code}; the difference identifies tenant A's component"
        )
        # The only legitimate difference is the id echoed back in an error message, which the
        # caller supplied itself and so cannot learn anything from.
        assert foreign.text.replace("agent-a", MISSING_ID) == missing.text, (
            f"{method} {path}: response bodies differ beyond the echoed component id"
        )

    def test_list_excludes_foreign_components_and_their_count(self, client, seeded):
        """The total must exclude A's rows too, not just the page.

        A correct page with an inflated total still leaks how many components tenant A has.
        """
        response = client.get("/components", headers=auth(tenant=TENANT_B))
        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total_count"] == 0

    def test_the_owner_still_sees_its_own(self, client, seeded):
        response = client.get("/components/agent-a", headers=auth(tenant=TENANT_A))
        assert response.status_code == 200
        assert response.json()["component_id"] == "agent-a"

    def test_http_attempts_leave_tenant_a_untouched(self, client, seeded):
        """Every write route tried as tenant B, then tenant A's tables compared byte for byte.

        A route can refuse in its response and still have written on the way there.
        """
        before = _snapshot(seeded)
        for method, path, _ in SCOPED_ROUTES:
            _request(client, method, path, "agent-a", TENANT_B)
        assert _snapshot(seeded) == before


# ============================================================================
# 2. DIRECTLY AGAINST PostgresDb
# ============================================================================


class TestTenantBCannotReadOnPostgres:
    def test_reads_return_nothing(self, seeded):
        assert seeded.get_component("agent-a", tenant_id=TENANT_B) is None
        assert seeded.get_config("agent-a", tenant_id=TENANT_B) is None
        assert seeded.get_config("agent-a", version=1, tenant_id=TENANT_B) is None
        assert seeded.list_configs("agent-a", tenant_id=TENANT_B) == []
        assert seeded.get_links("agent-a", version=1, tenant_id=TENANT_B) == []
        assert seeded.get_dependents("child-a", tenant_id=TENANT_B) == []
        assert seeded.load_component_graph("agent-a", tenant_id=TENANT_B) is None

    def test_lists_exclude_rows_and_counts(self, seeded):
        rows, total = seeded.list_components(tenant_id=TENANT_B)
        assert (rows, total) == ([], 0)
        records, audit_total = seeded.list_component_audit("agent-a", tenant_id=TENANT_B)
        assert (records, audit_total) == ([], 0)

    def test_owner_reads_still_work(self, seeded):
        assert seeded.get_component("agent-a", tenant_id=TENANT_A) is not None
        assert seeded.load_component_graph("agent-a", tenant_id=TENANT_A) is not None
        _, total = seeded.list_components(tenant_id=TENANT_A)
        assert total == 2


class TestTenantBCannotMutateOnPostgres:
    def test_mutations_are_refused_and_change_nothing(self, seeded):
        before = _snapshot(seeded)

        assert seeded.delete_component("agent-a", tenant_id=TENANT_B) is False
        assert seeded.delete_component("agent-a", hard_delete=True, tenant_id=TENANT_B) is False
        assert seeded.delete_config("agent-a", version=2, tenant_id=TENANT_B) is False
        assert seeded.set_current_version("agent-a", version=1, tenant_id=TENANT_B) is False
        with pytest.raises(ValueError):
            seeded.upsert_config("agent-a", config={"model": "evil"}, tenant_id=TENANT_B)

        assert _snapshot(seeded) == before

    def test_upsert_component_does_not_edit_a_foreign_row(self, seeded):
        a_before = seeded.get_component("agent-a", tenant_id=TENANT_A)
        try:
            seeded.upsert_component(component_id="agent-a", name="Hijacked", tenant_id=TENANT_B)
        except Exception:
            pass  # an IntegrityError on the shared primary key is an acceptable outcome
        assert seeded.get_component("agent-a", tenant_id=TENANT_A) == a_before


def test_tenant_a_data_survives_the_full_sweep_on_postgres(seeded):
    """The acceptance assertion, on the backend a deployment actually runs."""
    before = _snapshot(seeded)

    _sweep_every_method_as(seeded, TENANT_B)

    after = _snapshot(seeded)
    for table in _component_table_names(seeded):
        assert after[table] == before[table], f"tenant B altered {table}"


def test_tenant_a_can_still_operate_after_the_sweep(seeded):
    """Isolation must not have been achieved by breaking the database."""
    _sweep_every_method_as(seeded, TENANT_B)

    config = seeded.upsert_config("agent-a", config=dict(AGENT_CONFIG, note="v3"), tenant_id=TENANT_A)
    assert config["version"] == 3
    assert seeded.delete_component("agent-a", tenant_id=TENANT_A) is True


# ============================================================================
# 3. STRICT MODE ON POSTGRES
# ============================================================================


class TestStrictModeOnPostgres:
    def test_unscoped_access_is_refused(self, postgres_engine, seeded):
        """A strict db refuses ``tenant_id=None`` rather than serving every tenant's rows."""
        from egpt.integrations.db.postgres import PostgresDb  # noqa: PLC0415

        strict = PostgresDb(
            db_engine=postgres_engine,
            db_schema="test_schema",
            components_table=seeded.components_table_name,
            component_configs_table=seeded.component_configs_table_name,
            component_links_table=seeded.component_links_table_name,
            component_audit_table=seeded.component_audit_table_name,
            require_tenant_id=True,
        )
        with pytest.raises(TenantScopeRequiredError):
            strict.get_component("agent-a", tenant_id=None)
        with pytest.raises(TenantScopeRequiredError):
            strict.list_components(tenant_id=None)
        # ...but a scoped read on the same db is served normally.
        assert strict.get_component("agent-a", tenant_id=TENANT_A) is not None

    def test_egpt_os_enables_strict_mode_when_a_tenant_claim_is_configured(self, adversarial_db):
        """The wiring in ``EgptOS``: opting into tenancy at the edge opts in at the DB.

        Without this, ``Agent.save`` and the studio toolkit keep unscoped access to the same rows
        the router is busy protecting.
        """
        assert adversarial_db.require_tenant_id is False
        EgptOS(
            id=OS_ID,
            db=adversarial_db,
            authorization=True,
            authorization_config=AuthorizationConfig(
                verification_keys=[JWT_SECRET],
                algorithm="HS256",
                audience=OS_ID,
                tenant_id_claim=TENANT_CLAIM,
            ),
        ).get_app()
        assert adversarial_db.require_tenant_id is True

    def test_no_tenant_claim_leaves_the_db_permissive(self, adversarial_db):
        """A single-tenant deployment must keep working exactly as before."""
        EgptOS(id=OS_ID, db=adversarial_db).get_app()
        assert adversarial_db.require_tenant_id is False
