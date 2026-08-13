"""Seeding and portable import/export against a real Postgres (EGPT-2.8).

The unit suites under ``tests/unit/registry/`` drive all of this against SQLite and cover the
logic. What only Postgres can show is the thing SQLite structurally cannot:

**jsonb does not preserve key order.** SQLite stores a config as TEXT and hands back the bytes
it was given, so a round-trip assertion written as string equality passes there and fails here.
Postgres normalizes, so this suite is what pins that ``configs_equal`` and the round-trip
comparisons are dict-based -- and, more importantly, that *seeding stays idempotent* when the
stored config comes back with its keys rearranged. A key-order-sensitive comparison would make
every re-seed append a version, and nothing at the SQLite level would notice.

The rest is the same shape as the sibling Postgres suites: real ``EgptOS``, real HS256 JWTs,
per-run table suffixes.

Gated on ``EGPT_INTEGRATION_POSTGRES``. Bring the database up with::

    ./cookbook/scripts/run_pgvector.sh
    EGPT_INTEGRATION_POSTGRES=1 pytest libs/agno/tests/integration/os/seeding_postgres -v
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from egpt.components.io import import_component, seed_from_directory
from egpt.registry import Registry
from egpt.registry.io import ComponentIdConflictError, export_component
from egpt.team import get_team_by_id

jwt = pytest.importorskip("jwt", reason="pyjwt is required for the seeding integration suite")

JWT_SECRET = "test-secret-key-for-seeding-tests-0123456789"
OS_ID = "seeding-test-os"
TENANT_CLAIM = "tid"

TENANT_A = "acme"
TENANT_B = "globex"

ALL_SCOPES = ["components:read", "components:write", "components:delete"]

MODEL = {"id": "gpt-5.5", "provider": "OpenAI", "name": "OpenAIResponses"}

# Keys deliberately out of alphabetical order: jsonb will rearrange them on the way back, which
# is the entire reason this suite exists.
TEAM_BUNDLE: Dict[str, Any] = {
    "format": "egpt.component-bundle",
    "format_version": 1,
    "schema_version": 1,
    "root": "support-team",
    "components": [
        {
            "component_id": "faq-agent",
            "component_type": "agent",
            "name": "FAQ Agent",
            "stage": "published",
            "config": {
                "name": "FAQ Agent",
                "model": MODEL,
                "id": "faq-agent",
                "markdown": True,
                "instructions": ["Answer from the FAQ only."],
                "description": "Answers frequently asked questions.",
            },
            "links": [],
        },
        {
            "component_id": "support-team",
            "component_type": "team",
            "name": "Support Team",
            "stage": "published",
            "config": {
                "name": "Support Team",
                "model": MODEL,
                "members": [{"type": "agent", "agent_id": "faq-agent"}],
                "id": "support-team",
            },
            "links": [
                {
                    "link_kind": "member",
                    "link_key": "member_0",
                    "child_component_id": "faq-agent",
                    "position": 0,
                    "meta": {"type": "agent"},
                }
            ],
        },
    ],
}


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
def seeding_db(postgres_engine):
    """A PostgresDb whose component tables are unique to this run."""
    from egpt.integrations.db.postgres import PostgresDb  # noqa: PLC0415

    suffix = uuid4().hex[:8]
    return PostgresDb(
        db_engine=postgres_engine,
        db_schema="test_schema",
        id="os-db",
        session_table=f"s_sessions_{suffix}",
        memory_table=f"s_memories_{suffix}",
        metrics_table=f"s_metrics_{suffix}",
        eval_table=f"s_evals_{suffix}",
        knowledge_table=f"s_knowledge_{suffix}",
        components_table=f"s_components_{suffix}",
        component_configs_table=f"s_configs_{suffix}",
        component_links_table=f"s_links_{suffix}",
        component_audit_table=f"s_audit_{suffix}",
    )


@pytest.fixture
def registry(seeding_db):
    reg = Registry()
    reg.add_db(seeding_db)
    return reg


@pytest.fixture
def templates(tmp_path):
    (tmp_path / "team.support.json").write_text(json.dumps(TEAM_BUNDLE), encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(seeding_db) -> TestClient:
    from egpt.os import EgptOS
    from egpt.os.config import AuthorizationConfig

    os_app = EgptOS(
        id=OS_ID,
        db=seeding_db,
        authorization=True,
        authorization_config=AuthorizationConfig(
            verification_keys=[JWT_SECRET],
            algorithm="HS256",
            audience=OS_ID,
            tenant_id_claim=TENANT_CLAIM,
        ),
    )
    return TestClient(os_app.get_app())


def _audit_total(db, component_id, tenant_id):
    _records, total = db.list_component_audit(component_id, tenant_id=tenant_id, limit=1)
    return total


class TestJsonbKeyOrderDoesNotBreakIdempotency:
    """The property this whole suite exists for.

    Postgres stores jsonb normalized, so what comes back out of ``get_config`` is not the dict
    that went in -- the keys are rearranged. A comparison that serialized without sorting, or
    compared strings, would call every stored config "changed" and append a version on every
    single seed run. On SQLite this is invisible.
    """

    def test_the_stored_config_really_does_come_back_reordered(self, seeding_db, templates, registry):
        seed_from_directory(seeding_db, templates, tenant_id=TENANT_A, registry=registry)

        stored = seeding_db.get_config("faq-agent", tenant_id=TENANT_A)["config"]
        source = TEAM_BUNDLE["components"][0]["config"]

        assert stored == {**source, "schema_version": 1}
        # If this ever stops holding, the suite has lost its teeth rather than found a bug.
        assert list(stored) != list(source), "jsonb no longer reorders; this suite proves less than it claims"

    def test_re_seeding_writes_nothing(self, seeding_db, templates, registry):
        seed_from_directory(seeding_db, templates, tenant_id=TENANT_A, registry=registry)
        before = _audit_total(seeding_db, "faq-agent", TENANT_A)

        results = seed_from_directory(seeding_db, templates, tenant_id=TENANT_A, registry=registry)

        assert {r.action for r in results} == {"unchanged"}
        assert _audit_total(seeding_db, "faq-agent", TENANT_A) == before
        assert len(seeding_db.list_configs("faq-agent", tenant_id=TENANT_A)) == 1

    def test_a_genuine_change_is_still_detected(self, seeding_db, templates, registry):
        """The other half: idempotency must not be achieved by never noticing anything."""
        seed_from_directory(seeding_db, templates, tenant_id=TENANT_A, registry=registry)
        changed = json.loads(json.dumps(TEAM_BUNDLE))
        changed["components"][0]["config"]["instructions"] = ["Escalate billing questions."]
        (templates / "team.support.json").write_text(json.dumps(changed), encoding="utf-8")

        results = seed_from_directory(seeding_db, templates, tenant_id=TENANT_A, registry=registry)

        actions = {r.component_id: r.action for r in results}
        assert actions == {"faq-agent": "new_version", "support-team": "unchanged"}


class TestCrossTenantSeedingOnOneRealDatabase:
    def test_the_same_corpus_needs_distinct_ids(self, seeding_db, templates, registry):
        seed_from_directory(seeding_db, templates, tenant_id=TENANT_A, registry=registry)

        with pytest.raises(ComponentIdConflictError):
            seed_from_directory(seeding_db, templates, tenant_id=TENANT_B, registry=registry)

        seed_from_directory(seeding_db, templates, tenant_id=TENANT_B, registry=registry, id_prefix=f"{TENANT_B}-")

        mine, _ = seeding_db.list_components(tenant_id=TENANT_A)
        theirs, _ = seeding_db.list_components(tenant_id=TENANT_B)
        assert {row["component_id"] for row in mine} == {"faq-agent", "support-team"}
        assert {row["component_id"] for row in theirs} == {"globex-faq-agent", "globex-support-team"}

    def test_both_copies_hydrate_against_the_composite_indexes(self, seeding_db, templates, registry):
        seed_from_directory(seeding_db, templates, tenant_id=TENANT_A, registry=registry)
        seed_from_directory(seeding_db, templates, tenant_id=TENANT_B, registry=registry, id_prefix=f"{TENANT_B}-")

        mine = get_team_by_id(seeding_db, "support-team", registry=registry, tenant_id=TENANT_A)
        theirs = get_team_by_id(seeding_db, "globex-support-team", registry=registry, tenant_id=TENANT_B)

        assert [m.id for m in mine.members] == ["faq-agent"]
        assert [m.id for m in theirs.members] == ["globex-faq-agent"]
        assert get_team_by_id(seeding_db, "support-team", registry=registry, tenant_id=TENANT_B) is None


class TestRoundTripThroughPostgres:
    def test_export_then_import_into_a_second_tenant(self, seeding_db, templates, registry):
        seed_from_directory(seeding_db, templates, tenant_id=TENANT_A, registry=registry)

        document = export_component(seeding_db, "support-team", tenant_id=TENANT_A)
        import_component(
            seeding_db,
            document,
            tenant_id=TENANT_B,
            registry=registry,
            id_prefix="copy-",
        )

        team = get_team_by_id(seeding_db, "copy-support-team", registry=registry, tenant_id=TENANT_B)
        assert [m.id for m in team.members] == ["copy-faq-agent"]

        links = seeding_db.get_links("copy-support-team", 1, tenant_id=TENANT_B)
        child = seeding_db.get_config("copy-faq-agent", tenant_id=TENANT_B)
        assert links[0]["child_version"] == child["version"]


class TestOverHttpAgainstPostgres:
    def test_export_and_import_round_trip_with_real_jwts(self, client, seeding_db, templates, registry):
        seed_from_directory(seeding_db, templates, tenant_id=TENANT_A, registry=registry)

        exported = client.get("/components/support-team/export", headers=auth(tenant=TENANT_A))
        assert exported.status_code == 200, exported.text

        imported = client.post(
            "/components/import",
            json={"document": exported.json(), "id_prefix": "copy-"},
            headers=auth(tenant=TENANT_A),
        )

        assert imported.status_code == 201, imported.text
        assert imported.json()["created"] == 2

    def test_another_tenant_cannot_export_it(self, client, seeding_db, templates, registry):
        seed_from_directory(seeding_db, templates, tenant_id=TENANT_A, registry=registry)

        response = client.get("/components/support-team/export", headers=auth(tenant=TENANT_B))

        assert response.status_code == 404

    def test_export_requires_the_read_scope(self, client, seeding_db, templates, registry):
        seed_from_directory(seeding_db, templates, tenant_id=TENANT_A, registry=registry)

        response = client.get(
            "/components/support-team/export", headers=auth(scopes=["components:write"], tenant=TENANT_A)
        )

        assert response.status_code == 403

    def test_import_requires_the_write_scope(self, client, seeding_db, templates, registry):
        seed_from_directory(seeding_db, templates, tenant_id=TENANT_A, registry=registry)
        document = client.get("/components/support-team/export", headers=auth(tenant=TENANT_A)).json()

        response = client.post(
            "/components/import",
            json={"document": document, "id_prefix": "copy-"},
            headers=auth(scopes=["components:read"], tenant=TENANT_A),
        )

        assert response.status_code == 403
