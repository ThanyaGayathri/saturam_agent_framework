"""Backends that cannot enforce ``require_session_tenant_id`` refuse to construct with it on,
and ``InMemoryDb`` -- the one NoSQL/file backend that does enforce it -- behaves adversarially
correctly (aicoe-tracker#197).

A PR review of this ticket found that eight NoSQL/file backends (``JsonDb``, ``GcsJsonDb``,
``DynamoDb``, ``FirestoreDb``, ``MongoDb``, ``AsyncMongoDb``, ``RedisDb``, ``SurrealDb``) accepted
the new ``tenant_id`` keyword on every session method for signature parity with ``BaseDb``, but
had no guard call, filter, or stamp behind it -- so a deployment that set
``require_session_tenant_id=True`` (or the env var) on one of them got an affirmative "enforcement
is on" signal while nothing was actually enforced. That is worse than no flag at all. Each of
those eight backends now refuses to construct with the flag on
(``egpt.db.session_scope.require_unsupported_session_tenant_scope``), so the flag can never lie.
``InMemoryDb`` -- heavily used by tests and cookbooks -- got the real filter/stamp behaviour
instead, since raising there would have been a worse trade.
"""

import pytest

from egpt.db.component_scope import TenantScopeRequiredError
from egpt.db.session_scope import SESSION_TENANT_SCOPE_SUPPORTED_BACKENDS
from egpt.integrations.db.in_memory.in_memory_db import InMemoryDb
from egpt.session.agent import AgentSession

TENANT_A = "acme"
TENANT_B = "globex"


@pytest.fixture(autouse=True)
def _no_ambient_strict_mode(monkeypatch):
    monkeypatch.delenv("EGPT_REQUIRE_TENANT_ID", raising=False)
    monkeypatch.delenv("AGNO_REQUIRE_TENANT_ID", raising=False)
    monkeypatch.delenv("EGPT_REQUIRE_SESSION_TENANT_ID", raising=False)
    monkeypatch.delenv("AGNO_REQUIRE_SESSION_TENANT_ID", raising=False)


class TestConstructionRefusesUnsupportedBackends:
    """Every backend outside SESSION_TENANT_SCOPE_SUPPORTED_BACKENDS must refuse to construct
    with require_session_tenant_id=True -- explicitly, or via the env var -- before it ever
    touches a real network client, so none of these need live credentials to test."""

    def test_json_db_refuses(self):
        from egpt.integrations.db.json.json_db import JsonDb

        JsonDb(db_path="/tmp/agno_test_json_db_tenant")  # flag off: constructs fine
        with pytest.raises(NotImplementedError, match="JsonDb"):
            JsonDb(db_path="/tmp/agno_test_json_db_tenant", require_session_tenant_id=True)

    def test_gcs_json_db_refuses(self):
        pytest.importorskip("google.cloud.storage", reason="GcsJsonDb imports google-cloud-storage at module level")
        from egpt.integrations.db.gcs_json.gcs_json_db import GcsJsonDb

        with pytest.raises(NotImplementedError, match="GcsJsonDb"):
            GcsJsonDb(bucket_name="test-bucket", require_session_tenant_id=True)

    def test_dynamo_db_refuses(self):
        pytest.importorskip("boto3", reason="DynamoDb imports boto3 at module level")
        from egpt.integrations.db.dynamo.dynamo import DynamoDb

        with pytest.raises(NotImplementedError, match="DynamoDb"):
            DynamoDb(require_session_tenant_id=True)

    def test_firestore_db_refuses(self):
        pytest.importorskip(
            "google.cloud.firestore", reason="FirestoreDb imports google-cloud-firestore at module level"
        )
        from egpt.integrations.db.firestore.firestore import FirestoreDb

        with pytest.raises(NotImplementedError, match="FirestoreDb"):
            FirestoreDb(require_session_tenant_id=True)

    def test_mongo_db_refuses(self):
        pytest.importorskip("pymongo", reason="MongoDb imports pymongo at module level")
        from egpt.integrations.db.mongo.mongo import MongoDb

        with pytest.raises(NotImplementedError, match="MongoDb"):
            MongoDb(require_session_tenant_id=True)

    def test_async_mongo_db_refuses(self):
        pytest.importorskip("pymongo", reason="AsyncMongoDb imports pymongo at module level")
        from egpt.integrations.db.mongo.async_mongo import AsyncMongoDb

        with pytest.raises(NotImplementedError, match="AsyncMongoDb"):
            AsyncMongoDb(require_session_tenant_id=True)

    def test_redis_db_refuses(self):
        pytest.importorskip("redis", reason="RedisDb imports the redis SDK at module level")
        from egpt.integrations.db.redis.redis import RedisDb

        with pytest.raises(NotImplementedError, match="RedisDb"):
            RedisDb(require_session_tenant_id=True)

    def test_surrealdb_refuses(self):
        pytest.importorskip("surrealdb", reason="SurrealDb imports the surrealdb SDK at module level")
        from egpt.integrations.db.surrealdb.surrealdb import SurrealDb

        with pytest.raises(NotImplementedError, match="SurrealDb"):
            SurrealDb(
                client=None,
                db_url="ws://localhost:8000",
                db_creds={},
                db_ns="ns",
                db_db="db",
                require_session_tenant_id=True,
            )

    def test_env_var_also_triggers_refusal(self, monkeypatch):
        """The construction-time refusal reads the same resolved flag as everything else, so an
        operator setting only the env var (never the constructor arg) is caught too."""
        monkeypatch.setenv("EGPT_REQUIRE_SESSION_TENANT_ID", "true")
        from egpt.integrations.db.json.json_db import JsonDb

        with pytest.raises(NotImplementedError, match="JsonDb"):
            JsonDb(db_path="/tmp/agno_test_json_db_tenant_env")


class TestSupportedBackendsMatrixNamesRealClasses:
    def test_matrix_includes_the_backends_this_pr_added_enforcement_to(self):
        assert "InMemoryDb" in SESSION_TENANT_SCOPE_SUPPORTED_BACKENDS
        assert "SqliteDb" in SESSION_TENANT_SCOPE_SUPPORTED_BACKENDS
        assert "AsyncSqliteDb" in SESSION_TENANT_SCOPE_SUPPORTED_BACKENDS
        assert "PostgresDb" in SESSION_TENANT_SCOPE_SUPPORTED_BACKENDS
        assert "MySQLDb" in SESSION_TENANT_SCOPE_SUPPORTED_BACKENDS
        assert "SingleStoreDb" in SESSION_TENANT_SCOPE_SUPPORTED_BACKENDS


class TestInMemoryDbEnforcesTenantScoping:
    """InMemoryDb got the real filter/stamp behaviour (unlike the eight refuse-to-construct
    backends above) because it is what most tests and cookbooks default to."""

    @pytest.fixture
    def seeded(self):
        db = InMemoryDb(require_session_tenant_id=True)
        session = AgentSession(session_id="session-a", agent_id="agent-1", user_id="user-1", created_at=1)
        db.upsert_session(session, tenant_id=TENANT_A)
        return db

    def test_cross_tenant_read_is_blocked(self, seeded):
        assert seeded.get_session("session-a", tenant_id=TENANT_B) is None
        assert seeded.get_session("session-a", tenant_id=TENANT_A) is not None

    def test_cross_tenant_delete_is_blocked(self, seeded):
        assert seeded.delete_session("session-a", tenant_id=TENANT_B) is False
        assert seeded.get_session("session-a", tenant_id=TENANT_A) is not None

    def test_cross_tenant_upsert_does_not_overwrite(self, seeded):
        result = seeded.upsert_session(
            AgentSession(session_id="session-a", agent_id="agent-1", user_id="mallory", created_at=1),
            tenant_id=TENANT_B,
        )
        assert result is None
        raw = seeded.get_session("session-a", tenant_id=TENANT_A, deserialize=False)
        assert raw["user_id"] == "user-1"
        assert raw["tenant_id"] == TENANT_A

    def test_unscoped_access_is_refused_under_the_flag(self, seeded):
        with pytest.raises(TenantScopeRequiredError):
            seeded.get_session("session-a")

    def test_unscoped_access_is_allowed_without_the_flag(self):
        db = InMemoryDb()
        session = AgentSession(session_id="session-b", agent_id="agent-1", user_id="user-1", created_at=1)
        db.upsert_session(session)
        assert db.get_session("session-b") is not None
