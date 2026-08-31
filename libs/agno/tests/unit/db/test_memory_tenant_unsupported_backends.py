"""Backends that cannot enforce ``require_memory_tenant_id`` refuse to construct with it on
(aicoe-tracker#175).

Mirrors ``test_session_tenant_unsupported_backends.py`` (aicoe-tracker#197) for the memory flag.
A PR review of this ticket found that ``test_memory_tenant_scoping.py`` only exercised the free
function ``require_unsupported_memory_tenant_scope`` directly with a string backend name -- no
test instantiated any of the actual "unsupported" backend classes with
``require_memory_tenant_id=True`` and asserted the class itself refuses to construct. That gap is
exactly what let ``ClickhouseDb``'s ``__init__`` ship without a call to the guard at all: the
flag would have silently reported ``True`` with zero enforcement on that backend, and no test
caught it because none of them instantiated the real class.
"""

import pytest

from egpt.db.memory_scope import MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS


@pytest.fixture(autouse=True)
def _no_ambient_strict_mode(monkeypatch):
    monkeypatch.delenv("EGPT_REQUIRE_TENANT_ID", raising=False)
    monkeypatch.delenv("AGNO_REQUIRE_TENANT_ID", raising=False)
    monkeypatch.delenv("EGPT_REQUIRE_MEMORY_TENANT_ID", raising=False)
    monkeypatch.delenv("AGNO_REQUIRE_MEMORY_TENANT_ID", raising=False)


class TestConstructionRefusesUnsupportedBackends:
    """Every backend outside MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS must refuse to construct
    with require_memory_tenant_id=True -- explicitly, or via the env var -- before it ever
    touches a real network client, so none of these need live credentials to test."""

    def test_json_db_refuses(self):
        from egpt.integrations.db.json.json_db import JsonDb

        JsonDb(db_path="/tmp/agno_test_json_db_memory_tenant")  # flag off: constructs fine
        with pytest.raises(NotImplementedError, match="JsonDb"):
            JsonDb(db_path="/tmp/agno_test_json_db_memory_tenant", require_memory_tenant_id=True)

    def test_gcs_json_db_refuses(self):
        pytest.importorskip("google.cloud.storage", reason="GcsJsonDb imports google-cloud-storage at module level")
        from egpt.integrations.db.gcs_json.gcs_json_db import GcsJsonDb

        with pytest.raises(NotImplementedError, match="GcsJsonDb"):
            GcsJsonDb(bucket_name="test-bucket", require_memory_tenant_id=True)

    def test_dynamo_db_refuses(self):
        pytest.importorskip("boto3", reason="DynamoDb imports boto3 at module level")
        from egpt.integrations.db.dynamo.dynamo import DynamoDb

        with pytest.raises(NotImplementedError, match="DynamoDb"):
            DynamoDb(require_memory_tenant_id=True)

    def test_firestore_db_refuses(self):
        pytest.importorskip(
            "google.cloud.firestore", reason="FirestoreDb imports google-cloud-firestore at module level"
        )
        from egpt.integrations.db.firestore.firestore import FirestoreDb

        with pytest.raises(NotImplementedError, match="FirestoreDb"):
            FirestoreDb(require_memory_tenant_id=True)

    def test_mongo_db_refuses(self):
        pytest.importorskip("pymongo", reason="MongoDb imports pymongo at module level")
        from egpt.integrations.db.mongo.mongo import MongoDb

        with pytest.raises(NotImplementedError, match="MongoDb"):
            MongoDb(require_memory_tenant_id=True)

    def test_async_mongo_db_refuses(self):
        pytest.importorskip("pymongo", reason="AsyncMongoDb imports pymongo at module level")
        from egpt.integrations.db.mongo.async_mongo import AsyncMongoDb

        with pytest.raises(NotImplementedError, match="AsyncMongoDb"):
            AsyncMongoDb(require_memory_tenant_id=True)

    def test_redis_db_refuses(self):
        pytest.importorskip("redis", reason="RedisDb imports the redis SDK at module level")
        from egpt.integrations.db.redis.redis import RedisDb

        with pytest.raises(NotImplementedError, match="RedisDb"):
            RedisDb(require_memory_tenant_id=True)

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
                require_memory_tenant_id=True,
            )

    def test_mysql_db_refuses(self):
        pytest.importorskip("pymysql", reason="MySQLDb needs a pymysql-based engine URL to resolve")
        from egpt.integrations.db.mysql.mysql import MySQLDb

        with pytest.raises(NotImplementedError, match="MySQLDb"):
            MySQLDb(db_url="mysql+pymysql://user:pass@localhost:3306/agno_test", require_memory_tenant_id=True)

    def test_singlestore_db_refuses(self):
        pytest.importorskip("pymysql", reason="SingleStoreDb needs a pymysql-based engine URL to resolve")
        from egpt.integrations.db.singlestore.singlestore import SingleStoreDb

        with pytest.raises(NotImplementedError, match="SingleStoreDb"):
            SingleStoreDb(db_url="mysql+pymysql://user:pass@localhost:3306/agno_test", require_memory_tenant_id=True)

    def test_clickhouse_db_refuses(self):
        """Regression guard for the exact gap this test file was added to close: ClickhouseDb's
        __init__ never called the guard at all, so this flag was previously a silent no-op."""
        pytest.importorskip("clickhouse_connect", reason="ClickhouseDb imports clickhouse-connect at module level")
        from egpt.integrations.db.clickhouse.clickhouse import ClickhouseDb

        with pytest.raises(NotImplementedError, match="ClickhouseDb"):
            ClickhouseDb(require_memory_tenant_id=True)

    def test_env_var_also_triggers_refusal(self, monkeypatch):
        """The construction-time refusal reads the same resolved flag as everything else, so an
        operator setting only the env var (never the constructor arg) is caught too."""
        monkeypatch.setenv("EGPT_REQUIRE_MEMORY_TENANT_ID", "true")
        from egpt.integrations.db.json.json_db import JsonDb

        with pytest.raises(NotImplementedError, match="JsonDb"):
            JsonDb(db_path="/tmp/agno_test_json_db_memory_tenant_env")


class TestSupportedBackendsMatrixNamesRealClasses:
    def test_matrix_includes_the_backends_this_pr_added_enforcement_to(self):
        assert "InMemoryDb" in MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS
        assert "SqliteDb" in MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS
        assert "AsyncSqliteDb" in MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS
        assert "PostgresDb" in MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS
        assert "AsyncPostgresDb" in MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS
        # MySQL/SingleStore are session-tenant-supported but deliberately absent here: their
        # memories table has no tenant column at all (see memory_scope.py's module comment).
        assert "MySQLDb" not in MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS
        assert "SingleStoreDb" not in MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS
