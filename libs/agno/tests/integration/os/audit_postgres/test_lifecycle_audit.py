"""The lifecycle audit trail over HTTP, against a real Postgres with real JWTs (EGPT-2.6).

The unit suites prove the pieces: ``tests/unit/db/test_component_audit.py`` drives the DB layer
against real SQLite, and ``tests/unit/os/routers/test_components_audit.py`` drives the routes
against a mocked db. What only this level can show is the whole chain holding together at once
-- that the ``sub`` claim on a JWT ends up in the ``actor`` column of a Postgres row, that the
tenant predicate excluding another tenant's history is a real ``WHERE``, and that the DDL
Postgres actually creates makes ``audit_id`` a sequence rather than a column somebody has to
fill in.

Gated on ``EGPT_INTEGRATION_POSTGRES``. Bring the database up with::

    ./cookbook/scripts/run_pgvector.sh
    EGPT_INTEGRATION_POSTGRES=1 pytest libs/agno/tests/integration/os/audit_postgres -v
"""

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from egpt.db.base import ComponentType
from egpt.db.component_audit import ComponentAuditAction
from egpt.os import EgptOS
from egpt.os.config import AuthorizationConfig
from egpt.os.settings import EgptServingSettings

jwt = pytest.importorskip("jwt", reason="pyjwt is required for the audit integration suite")

JWT_SECRET = "test-secret-key-for-audit-tests-0123456789"
OS_ID = "audit-test-os"
TENANT_CLAIM = "tid"

TENANT_A = "acme"
TENANT_B = "globex"

ALL_SCOPES = ["components:read", "components:write", "components:delete"]

AGENT_CONFIG = {"model": {"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"}}


def make_token(scopes, tenant: Optional[str], user_id: str = "alice") -> str:
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


def auth(scopes=ALL_SCOPES, tenant: Optional[str] = TENANT_A, user_id: str = "alice") -> Dict[str, str]:
    return {"Authorization": f"Bearer {make_token(scopes, tenant, user_id)}"}


@pytest.fixture
def audit_db(postgres_engine):
    """A PostgresDb whose component tables are unique to this run.

    The component tables get a suffix here, unlike the tenancy suite: these tests assert on
    absolute audit contents, so a table shared with another run would make them order-dependent.
    """
    from egpt.integrations.db.postgres import PostgresDb  # noqa: PLC0415

    suffix = uuid4().hex[:8]
    return PostgresDb(
        db_engine=postgres_engine,
        db_schema="test_schema",
        session_table=f"a_sessions_{suffix}",
        memory_table=f"a_memories_{suffix}",
        metrics_table=f"a_metrics_{suffix}",
        eval_table=f"a_evals_{suffix}",
        knowledge_table=f"a_knowledge_{suffix}",
        components_table=f"a_components_{suffix}",
        component_configs_table=f"a_configs_{suffix}",
        component_links_table=f"a_links_{suffix}",
        component_audit_table=f"a_audit_{suffix}",
    )


@pytest.fixture
def client(audit_db) -> TestClient:
    """A real EgptOS with RBAC and tenancy both switched on."""
    os_app = EgptOS(
        id=OS_ID,
        db=audit_db,
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
def client_with_audit_scope_split(audit_db) -> TestClient:
    """The same real EgptOS as `client`, but with the aicoe-tracker#170 split turned on."""
    os_app = EgptOS(
        id=OS_ID,
        db=audit_db,
        authorization=True,
        authorization_config=AuthorizationConfig(
            verification_keys=[JWT_SECRET],
            algorithm="HS256",
            audience=OS_ID,
            tenant_id_claim=TENANT_CLAIM,
        ),
        settings=EgptServingSettings(components_audit_scope_enabled=True),
    )
    return TestClient(os_app.get_app())


# --- helpers ---------------------------------------------------------------


def create_component(client, component_id="agent-a", tenant=TENANT_A, user_id="alice", stage="draft"):
    return client.post(
        "/components",
        json={
            "name": component_id,
            "component_id": component_id,
            "component_type": "agent",
            "config": AGENT_CONFIG,
            "stage": stage,
        },
        headers=auth(tenant=tenant, user_id=user_id),
    )


def history(client, component_id="agent-a", tenant=TENANT_A, user_id="alice", query="") -> List[Dict[str, Any]]:
    """The component's history oldest-first, so assertions read in the order things happened."""
    response = client.get(
        f"/components/{component_id}/audit?limit=100{query}",
        headers=auth(tenant=tenant, user_id=user_id),
    )
    assert response.status_code == 200, response.text
    return list(reversed(response.json()["data"]))


def actions(client, **kwargs) -> List[str]:
    return [row["action"] for row in history(client, **kwargs)]


def walk_the_lifecycle(client):
    """draft -> update -> publish -> new version -> publish -> rollback, all as alice."""
    create_component(client)
    client.patch("/components/agent-a/configs/1", json={"notes": "tweaked"}, headers=auth())
    client.patch("/components/agent-a/configs/1", json={"stage": "published"}, headers=auth())
    client.post("/components/agent-a/configs", json={"config": AGENT_CONFIG}, headers=auth())
    client.patch("/components/agent-a/configs/2", json={"stage": "published"}, headers=auth())
    client.post("/components/agent-a/configs/1/set-current", headers=auth())


# =============================================================================
# EVERY MUTATION IS AUDITED
# =============================================================================


class TestTheWholeLifecycleIsAudited:
    def test_the_actions_come_back_in_the_order_they_happened(self, client):
        walk_the_lifecycle(client)

        assert actions(client) == [
            ComponentAuditAction.COMPONENT_CREATE.value,
            ComponentAuditAction.CONFIG_CREATE.value,
            ComponentAuditAction.CONFIG_UPDATE.value,
            ComponentAuditAction.CONFIG_PUBLISH.value,
            ComponentAuditAction.CONFIG_CREATE.value,
            ComponentAuditAction.CONFIG_PUBLISH.value,
            ComponentAuditAction.CONFIG_ROLLBACK.value,
        ]

    def test_the_rollback_records_both_ends_of_the_move(self, client):
        walk_the_lifecycle(client)

        rollback = history(client)[-1]
        assert rollback["details"] == {"from_version": 2, "to_version": 1}
        assert rollback["version"] == 1

    def test_deleting_a_component_is_audited(self, client, audit_db):
        create_component(client)
        assert client.delete("/components/agent-a", headers=auth()).status_code == 204

        # Read at the db layer, not through the route: the audit endpoint's existence gate
        # 404s for a deleted component, so going through it would prove nothing about whether
        # the rows are still there -- which is the whole point of the table having no foreign
        # key to components.
        rows, _ = audit_db.list_component_audit("agent-a", tenant_id=TENANT_A, limit=100)
        assert rows[0]["action"] == ComponentAuditAction.COMPONENT_DELETE.value

    def test_updating_the_component_itself_is_audited(self, client):
        create_component(client)
        assert client.patch("/components/agent-a", json={"name": "Renamed"}, headers=auth()).status_code == 200

        assert actions(client)[-1] == ComponentAuditAction.COMPONENT_UPDATE.value

    def test_deleting_a_draft_version_is_audited(self, client):
        create_component(client, stage="published")
        client.post("/components/agent-a/configs", json={"config": AGENT_CONFIG}, headers=auth())
        assert client.delete("/components/agent-a/configs/2", headers=auth()).status_code == 204

        assert actions(client)[-1] == ComponentAuditAction.CONFIG_DELETE.value


# =============================================================================
# READING PAST A DELETE (aicoe-tracker#167)
# =============================================================================


class TestReadingPastADelete:
    def test_the_default_still_404s_after_a_soft_delete(self, client):
        create_component(client)
        assert client.delete("/components/agent-a", headers=auth()).status_code == 204

        assert client.get("/components/agent-a/audit", headers=auth()).status_code == 404

    def test_include_deleted_reaches_history_after_a_soft_delete(self, client):
        create_component(client)
        assert client.delete("/components/agent-a", headers=auth()).status_code == 204

        assert actions(client, query="&include_deleted=true")[-1] == ComponentAuditAction.COMPONENT_DELETE.value

    def test_the_default_still_404s_after_a_hard_delete(self, client, audit_db):
        create_component(client)
        audit_db.delete_component("agent-a", hard_delete=True, tenant_id=TENANT_A, actor="alice")

        assert client.get("/components/agent-a/audit", headers=auth()).status_code == 404

    def test_include_deleted_reaches_history_after_a_hard_delete(self, client, audit_db):
        """No components row survives a hard delete, unlike a soft delete -- so this proves
        existence through the audit trail rather than through get_component."""
        create_component(client)
        audit_db.delete_component("agent-a", hard_delete=True, tenant_id=TENANT_A, actor="alice")

        rows = history(client, query="&include_deleted=true")
        assert rows[-1]["action"] == ComponentAuditAction.COMPONENT_DELETE.value
        assert rows[-1]["details"] == {"hard": True}

    def test_include_deleted_with_a_filter_matching_nothing_is_a_200_not_a_404(self, client, audit_db):
        """Existence must not depend on the caller's filters: a hard-deleted component with real
        history still 200s an empty page for a valid action that legitimately matches nothing,
        rather than reading as "never existed" -- exactly like a live component would."""
        create_component(client)
        audit_db.delete_component("agent-a", hard_delete=True, tenant_id=TENANT_A, actor="alice")

        response = client.get(
            f"/components/agent-a/audit?include_deleted=true&action={ComponentAuditAction.CONFIG_ROLLBACK.value}",
            headers=auth(),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total_count"] == 0

    def test_include_deleted_does_not_cross_tenants(self, client, audit_db):
        create_component(client, tenant=TENANT_A)
        audit_db.delete_component("agent-a", hard_delete=True, tenant_id=TENANT_A, actor="alice")

        response = client.get("/components/agent-a/audit?include_deleted=true", headers=auth(tenant=TENANT_B))

        assert response.status_code == 404

    def test_include_deleted_404s_for_a_component_that_never_existed(self, client):
        response = client.get("/components/nonexistent/audit?include_deleted=true", headers=auth())

        assert response.status_code == 404


# =============================================================================
# THE ACTOR IS THE AUTHENTICATED PRINCIPAL
# =============================================================================


class TestAttribution:
    def test_the_jwt_subject_becomes_the_actor(self, client):
        """The chain this suite exists for: JWT ``sub`` -> request.state.user_id -> the db."""
        create_component(client, user_id="alice")

        assert {row["actor"] for row in history(client)} == {"alice"}

    def test_each_change_is_attributed_to_whoever_made_it(self, client):
        create_component(client, user_id="alice")
        client.patch("/components/agent-a/configs/1", json={"stage": "published"}, headers=auth(user_id="bob"))
        client.post("/components/agent-a/configs", json={"config": AGENT_CONFIG}, headers=auth(user_id="carol"))

        assert [row["actor"] for row in history(client)] == ["alice", "alice", "bob", "carol"]

    def test_a_client_cannot_supply_its_own_actor(self, client):
        """Attribution comes from the verified token, never the request body -- otherwise the
        trail would record whoever the caller claimed to be."""
        client.post(
            "/components",
            json={
                "name": "agent-a",
                "component_id": "agent-a",
                "component_type": "agent",
                "config": AGENT_CONFIG,
                "actor": "someone-else",
            },
            headers=auth(user_id="alice"),
        )

        assert {row["actor"] for row in history(client)} == {"alice"}

    def test_the_response_never_exposes_the_tenant(self, client):
        create_component(client)

        assert all("tenant_id" not in row for row in history(client))


# =============================================================================
# TENANT SCOPING
# =============================================================================


class TestTheHistoryIsTenantScoped:
    def test_another_tenant_gets_the_same_404_as_for_a_missing_component(self, client):
        create_component(client, tenant=TENANT_A)

        foreign = client.get("/components/agent-a/audit", headers=auth(tenant=TENANT_B))
        missing = client.get("/components/nonexistent/audit", headers=auth(tenant=TENANT_B))

        assert foreign.status_code == missing.status_code == 404

    def test_each_tenant_sees_only_its_own_history(self, client):
        create_component(client, component_id="agent-a", tenant=TENANT_A, user_id="alice")
        create_component(client, component_id="agent-b", tenant=TENANT_B, user_id="bob")

        assert {r["actor"] for r in history(client, component_id="agent-a", tenant=TENANT_A)} == {"alice"}
        assert {r["actor"] for r in history(client, component_id="agent-b", tenant=TENANT_B, user_id="bob")} == {"bob"}

    def test_a_credential_with_no_tenant_is_refused(self, client):
        create_component(client, tenant=TENANT_A)

        response = client.get("/components/agent-a/audit", headers=auth(tenant=None))

        assert response.status_code == 403


# =============================================================================
# RBAC
# =============================================================================


class TestScopes:
    def test_components_read_is_enough(self, client):
        create_component(client)

        response = client.get("/components/agent-a/audit", headers=auth(scopes=["components:read"]))

        assert response.status_code == 200

    def test_a_write_only_token_is_refused(self, client):
        """The mapping is required, not decorative: without the scopes.py entry this route
        would be an unmapped route under /components, which is a hard 403 -- so this test also
        confirms the entry resolves rather than the route being denied by accident."""
        create_component(client)

        response = client.get("/components/agent-a/audit", headers=auth(scopes=["components:write"]))

        assert response.status_code == 403


class TestScopesWithAuditSplitEnabled:
    """aicoe-tracker#170: with the split turned on, components:read alone no longer suffices."""

    def test_components_read_alone_is_refused(self, client_with_audit_scope_split):
        create_component(client_with_audit_scope_split)

        response = client_with_audit_scope_split.get(
            "/components/agent-a/audit", headers=auth(scopes=["components:read"])
        )

        assert response.status_code == 403

    def test_components_audit_is_enough(self, client_with_audit_scope_split):
        create_component(client_with_audit_scope_split)

        response = client_with_audit_scope_split.get(
            "/components/agent-a/audit", headers=auth(scopes=["components:audit"])
        )

        assert response.status_code == 200


# =============================================================================
# IMMUTABILITY, AND THAT REFUSALS LEAVE NO TRACE
# =============================================================================


class TestRefusalsAreNotAudited:
    def test_editing_a_published_version_is_refused_and_unaudited(self, client):
        create_component(client, stage="published")
        before = len(history(client))

        response = client.patch("/components/agent-a/configs/1", json={"config": AGENT_CONFIG}, headers=auth())

        assert response.status_code == 400
        assert len(history(client)) == before

    def test_deleting_a_published_version_is_refused_and_unaudited(self, client):
        """The gap EGPT-2.6 closed: v2 is published but no longer current, so before the fix
        this returned 204 and destroyed the version."""
        create_component(client, stage="published")
        client.post("/components/agent-a/configs", json={"config": AGENT_CONFIG, "stage": "published"}, headers=auth())
        client.post("/components/agent-a/configs/1/set-current", headers=auth())
        before = len(history(client))

        response = client.delete("/components/agent-a/configs/2", headers=auth())

        assert response.status_code == 400
        assert len(history(client)) == before
        assert client.get("/components/agent-a/configs/2", headers=auth()).status_code == 200

    def test_a_cross_tenant_mutation_is_refused_and_unaudited(self, client):
        create_component(client, tenant=TENANT_A)
        before = len(history(client, tenant=TENANT_A))

        assert client.delete("/components/agent-a", headers=auth(tenant=TENANT_B)).status_code == 404

        assert len(history(client, tenant=TENANT_A)) == before


# =============================================================================
# READ FILTERS AND THE REAL POSTGRES DDL
# =============================================================================


class TestReadFilters:
    def test_the_history_can_be_filtered_by_action(self, client):
        walk_the_lifecycle(client)

        rows = history(client, query=f"&action={ComponentAuditAction.CONFIG_PUBLISH.value}")

        assert [r["action"] for r in rows] == [ComponentAuditAction.CONFIG_PUBLISH.value] * 2

    def test_an_unknown_action_is_rejected(self, client):
        create_component(client)

        response = client.get("/components/agent-a/audit?action=nope", headers=auth())

        assert response.status_code == 422

    def test_paging_reports_the_unpaged_total(self, client):
        walk_the_lifecycle(client)

        body = client.get("/components/agent-a/audit?limit=2&page=2", headers=auth()).json()

        assert len(body["data"]) == 2
        assert body["meta"]["total_count"] == 7


class TestThePostgresSchema:
    def test_audit_id_is_a_sequence_backed_integer(self, client, audit_db):
        """The DDL claim, on the real backend.

        An ``Integer`` primary key has to come out as ``SERIAL`` here -- if it did not, every
        insert would need the caller to supply the key and the whole trail would fail closed
        along with the mutation it was recording.
        """
        create_component(client)

        with audit_db.Session() as sess:
            row = sess.execute(
                text(
                    "SELECT data_type, column_default FROM information_schema.columns "
                    "WHERE table_schema = 'test_schema' AND table_name = :t AND column_name = 'audit_id'"
                ),
                {"t": audit_db.component_audit_table_name},
            ).fetchone()

        assert row is not None
        assert row[0] == "integer"
        assert "nextval" in row[1]

    def test_both_composite_indexes_exist(self, client, audit_db):
        create_component(client)

        with audit_db.Session() as sess:
            indexes = {
                r[0]
                for r in sess.execute(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname = 'test_schema' AND tablename = :t"),
                    {"t": audit_db.component_audit_table_name},
                ).fetchall()
            }

        name = audit_db.component_audit_table_name
        assert f"idx_{name}_tenant_id_component_id" in indexes
        assert f"idx_{name}_component_id_audit_id" in indexes

    def test_the_trail_outlives_a_hard_deleted_component(self, client, audit_db):
        """No foreign key to components, so a cascade cannot take the history with it."""
        create_component(client)
        audit_db.delete_component("agent-a", hard_delete=True, tenant_id=TENANT_A, actor="alice")

        with audit_db.Session() as sess:
            components = sess.execute(
                text(f"SELECT COUNT(*) FROM test_schema.{audit_db.components_table_name}")  # noqa: S608
            ).scalar()
            audit_rows = sess.execute(
                text(f"SELECT COUNT(*) FROM test_schema.{audit_db.component_audit_table_name}")  # noqa: S608
            ).scalar()

        assert components == 0
        assert audit_rows > 0


class TestThePostgresBackendDoesNotDriftFromSqlite:
    """The round-2 review found SQLite and Postgres disagreeing on a repeated soft delete, so
    every subsequent fix has to be shown landing on *both*. These are the Postgres twins of the
    two round-3 blockers, driven at the DB layer because that is where both defects live.
    """

    def test_deleting_a_config_records_the_components_tenant_not_the_config_rows(self, audit_db):
        """``config.delete`` sourced its tenant from the config row, which stores whatever tenant
        its writer passed. An unscoped write leaves NULL there, so the owning tenant could not
        see the deletion of its own component's config -- and a config row carrying a *foreign*
        tenant disclosed it to that tenant instead.
        """
        audit_db.create_component_with_config(
            component_id="agent-t",
            component_type=ComponentType.AGENT,
            name="agent-t",
            config=AGENT_CONFIG,
            stage="published",
            tenant_id=TENANT_A,
        )
        # Unscoped, the way Agent.save() writes: the config row itself gets a NULL tenant.
        audit_db.upsert_config("agent-t", config=AGENT_CONFIG, tenant_id=None)

        audit_db.delete_config("agent-t", version=2, tenant_id=None)

        scoped, scoped_total = audit_db.list_component_audit("agent-t", tenant_id=TENANT_A, limit=100)
        unscoped, unscoped_total = audit_db.list_component_audit("agent-t", limit=100, tenant_id=None)

        assert scoped_total == unscoped_total, "the owning tenant cannot see its own component's history"
        assert ComponentAuditAction.CONFIG_DELETE.value in [row["action"] for row in scoped]
        assert [row["tenant_id"] for row in unscoped] == [TENANT_A] * len(unscoped)

    def test_concurrent_publishes_never_claim_the_same_previous_current_version(self, audit_db):
        """``upsert_config`` read ``current_version`` at the top of the method and recorded that
        stale value as ``previous_current_version`` after moving the pointer unconditionally, so
        concurrent publishers produced rows all claiming to have replaced the same version --
        with no error to signal it. Fixed with the same compare-and-swap ``set_current_version``
        uses; this pins it on the backend the race was reproduced on.
        """
        audit_db.create_component_with_config(
            component_id="agent-c",
            component_type=ComponentType.AGENT,
            name="agent-c",
            config=AGENT_CONFIG,
            stage="published",
            tenant_id=TENANT_A,
        )
        for _ in range(2, 8):
            audit_db.upsert_config("agent-c", config=AGENT_CONFIG, tenant_id=TENANT_A)

        errors: List[Exception] = []

        def worker(target_version: int) -> None:
            try:
                audit_db.upsert_config("agent-c", version=target_version, stage="published", tenant_id=TENANT_A)
            except Exception as e:  # pragma: no cover - asserted via `errors`
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(v,)) for v in range(2, 8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

        rows, _ = audit_db.list_component_audit(
            "agent-c", tenant_id=TENANT_A, action=ComponentAuditAction.CONFIG_PUBLISH.value, limit=100
        )
        rows = list(reversed(rows))  # oldest first
        assert len(rows) == 7  # six from the threads, plus the publish the component was created with

        claims = [row["details"]["previous_current_version"] for row in rows]
        assert len(set(claims)) == len(claims), f"two publishes claim the same predecessor: {claims}"
        assert claims[0] is None

        for earlier, later in zip(rows, rows[1:]):
            assert earlier["version"] == later["details"]["previous_current_version"], (
                f"broken link: published v{earlier['version']} -> "
                f"claims v{later['details']['previous_current_version']}"
            )

    def test_setting_the_current_version_to_itself_records_nothing(self, audit_db):
        """A no-op wrote ``{'from_version': N, 'to_version': N}`` -- an event that did not happen,
        and the signature the concurrent-rollback corruption was identified by.
        """
        audit_db.create_component_with_config(
            component_id="agent-n",
            component_type=ComponentType.AGENT,
            name="agent-n",
            config=AGENT_CONFIG,
            stage="published",
            tenant_id=TENANT_A,
        )
        _, before = audit_db.list_component_audit("agent-n", tenant_id=TENANT_A, limit=100)

        assert audit_db.set_current_version("agent-n", version=1, tenant_id=TENANT_A) is True

        rows, after = audit_db.list_component_audit("agent-n", tenant_id=TENANT_A, limit=100)
        assert after == before
        assert ComponentAuditAction.CONFIG_ROLLBACK.value not in [row["action"] for row in rows]
