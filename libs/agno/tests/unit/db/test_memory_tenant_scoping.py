"""Tenant scoping for the memory table (aicoe-tracker#175).

Exercises a real SQLite database rather than mocks, so the assertions cover the actual DDL
(the ``tenant_id`` column SM-C3 (#91) added) and the actual SQL predicates each memory method
now applies -- mirroring ``test_component_tenant_scoping.py``'s approach for the component
tables, per this ticket's own testing notes.

Two backends are exercised: sync ``SqliteDb`` and async ``AsyncSqliteDb``, both listed in
``egpt.db.memory_scope.MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS`` -- the two backends whose memory
methods actually filter on ``tenant_id`` rather than merely accepting it for signature parity.
Postgres and InMemoryDb are the other two supported backends; Postgres needs a running server
(covered separately, if at all, by an integration suite) and InMemoryDb has no SQL to inspect, so
neither is duplicated here.
"""

import asyncio
import importlib.util
import os
import tempfile

import pytest

from egpt.db.component_scope import TenantScopeRequiredError
from egpt.db.memory_scope import (
    MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS,
    TENANT_COLUMN,
    require_unsupported_memory_tenant_scope,
)
from egpt.db.schemas import UserMemory
from egpt.integrations.db.sqlite.async_sqlite import AsyncSqliteDb
from egpt.integrations.db.sqlite.sqlite import SqliteDb

# The async lane resolves SQLAlchemy's aiosqlite dialect, which imports the driver at connect
# time. The sync tests in this module need no driver, so gate only the async ones rather than
# skipping the whole file (CONTRIBUTING.md, "Optional dependencies").
requires_aiosqlite = pytest.mark.skipif(
    importlib.util.find_spec("aiosqlite") is None,
    reason="AsyncSqliteDb's dialect imports aiosqlite at connect time",
)

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def sqlite_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SqliteDb(db_file=path)
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def async_sqlite_db():
    # `upsert_user_memory` does not auto-provision the table the way SqliteDb's does (it calls
    # `_get_table` with the default `create_table_if_not_found=False`) -- a pre-existing quirk,
    # not something aicoe-tracker#175 changed. `test_memory_migration.py` works around it the
    # same way: provision explicitly before exercising memory methods on a fresh db.
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = AsyncSqliteDb(db_file=path)
    asyncio.run(db._create_all_tables())
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass


def run(coro):
    """Run one coroutine to completion -- these tests are sync, matching the async_sqlite
    backend's other unit tests in this directory, none of which use ``pytest.mark.asyncio``."""
    return asyncio.run(coro)


# ============================================================================
# SCHEMA
# ============================================================================


def test_memories_table_has_a_tenant_id_column(sqlite_db):
    """The column SM-C3 (#91) added is real DDL, not a JSON key -- pinned the same way
    ``test_component_tenant_scoping.py`` pins it for the component tables."""
    from sqlalchemy import text

    sqlite_db.upsert_user_memory(UserMemory(memory="seed"), tenant_id=TENANT_A)
    with sqlite_db.Session() as sess:
        columns = {row[1] for row in sess.execute(text("PRAGMA table_info(egpt_memories)")).fetchall()}
    assert TENANT_COLUMN in columns


def test_sqlite_and_postgres_are_the_only_migrated_backends():
    """MySQL, SingleStore and DynamoDB define a memories table but never got the column
    (db/migrations/versions/v2_8_0.py) -- pinning the supported set here catches a future backend
    added to one list and not the other."""
    assert set(MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS) == {
        "SqliteDb",
        "AsyncSqliteDb",
        "PostgresDb",
        "AsyncPostgresDb",
        "InMemoryDb",
    }


# ============================================================================
# READ SCOPING (sync)
# ============================================================================


def test_get_user_memories_is_tenant_scoped(sqlite_db):
    sqlite_db.upsert_user_memory(UserMemory(memory="acme secret", user_id="u1"), tenant_id=TENANT_A)
    sqlite_db.upsert_user_memory(UserMemory(memory="globex secret", user_id="u1"), tenant_id=TENANT_B)

    a = sqlite_db.get_user_memories(user_id="u1", tenant_id=TENANT_A)
    b = sqlite_db.get_user_memories(user_id="u1", tenant_id=TENANT_B)

    assert [m.memory for m in a] == ["acme secret"]
    assert [m.memory for m in b] == ["globex secret"]


def test_get_user_memories_without_a_tenant_applies_no_filter(sqlite_db):
    """tenant_id=None means "no tenant predicate", which keeps existing single-tenant callers
    working -- the same contract session_scope/component_scope already carry."""
    sqlite_db.upsert_user_memory(UserMemory(memory="acme secret", user_id="u1"), tenant_id=TENANT_A)
    sqlite_db.upsert_user_memory(UserMemory(memory="globex secret", user_id="u1"), tenant_id=TENANT_B)

    assert {m.memory for m in sqlite_db.get_user_memories(user_id="u1")} == {"acme secret", "globex secret"}


def test_get_user_memory_is_tenant_scoped(sqlite_db):
    memory = sqlite_db.upsert_user_memory(UserMemory(memory="acme secret", user_id="u1"), tenant_id=TENANT_A)

    assert sqlite_db.get_user_memory(memory.memory_id, tenant_id=TENANT_A) is not None
    assert sqlite_db.get_user_memory(memory.memory_id, tenant_id=TENANT_B) is None


def test_get_all_memory_topics_is_tenant_scoped(sqlite_db):
    sqlite_db.upsert_user_memory(UserMemory(memory="a", topics=["billing"]), tenant_id=TENANT_A)
    sqlite_db.upsert_user_memory(UserMemory(memory="b", topics=["support"]), tenant_id=TENANT_B)

    assert sqlite_db.get_all_memory_topics(tenant_id=TENANT_A) == ["billing"]
    assert sqlite_db.get_all_memory_topics(tenant_id=TENANT_B) == ["support"]


def test_get_user_memory_stats_is_tenant_scoped(sqlite_db):
    sqlite_db.upsert_user_memory(UserMemory(memory="a", user_id="u1"), tenant_id=TENANT_A)
    sqlite_db.upsert_user_memory(UserMemory(memory="b", user_id="u1"), tenant_id=TENANT_A)
    sqlite_db.upsert_user_memory(UserMemory(memory="c", user_id="u1"), tenant_id=TENANT_B)

    stats_a, total_a = sqlite_db.get_user_memory_stats(tenant_id=TENANT_A)
    assert total_a == 1
    assert stats_a[0]["total_memories"] == 2

    stats_b, total_b = sqlite_db.get_user_memory_stats(tenant_id=TENANT_B)
    assert total_b == 1
    assert stats_b[0]["total_memories"] == 1


# ============================================================================
# WRITE SCOPING (sync)
# ============================================================================


def test_upsert_stamps_tenant_id_on_insert(sqlite_db):
    memory = sqlite_db.upsert_user_memory(UserMemory(memory="acme secret"), tenant_id=TENANT_A)
    assert memory.tenant_id == TENANT_A


def test_upsert_falls_back_to_the_row_s_own_tenant_id_when_not_passed(sqlite_db):
    """upsert_user_memory's tenant_id kwarg is optional -- a caller that already set
    UserMemory.tenant_id directly (rather than through the kwarg) still gets it persisted."""
    memory = sqlite_db.upsert_user_memory(UserMemory(memory="acme secret", tenant_id=TENANT_A))
    assert memory.tenant_id == TENANT_A
    assert sqlite_db.get_user_memories(tenant_id=TENANT_A)


def test_upsert_never_changes_an_existing_row_s_tenant(sqlite_db):
    """Only applied on insert -- the same rule upsert_session follows. An update naming a
    different tenant_id must not move the row."""
    memory = sqlite_db.upsert_user_memory(UserMemory(memory="v1"), tenant_id=TENANT_A)

    memory.memory = "v2"
    updated = sqlite_db.upsert_user_memory(memory, tenant_id=TENANT_B)

    assert updated.tenant_id == TENANT_A
    assert sqlite_db.get_user_memory(memory.memory_id, tenant_id=TENANT_A).memory == "v2"
    assert sqlite_db.get_user_memory(memory.memory_id, tenant_id=TENANT_B) is None


def test_delete_user_memory_does_not_cross_tenants(sqlite_db):
    memory = sqlite_db.upsert_user_memory(UserMemory(memory="acme secret"), tenant_id=TENANT_A)

    sqlite_db.delete_user_memory(memory.memory_id, tenant_id=TENANT_B)
    assert sqlite_db.get_user_memory(memory.memory_id, tenant_id=TENANT_A) is not None

    sqlite_db.delete_user_memory(memory.memory_id, tenant_id=TENANT_A)
    assert sqlite_db.get_user_memory(memory.memory_id, tenant_id=TENANT_A) is None


def test_delete_user_memories_does_not_cross_tenants(sqlite_db):
    a = sqlite_db.upsert_user_memory(UserMemory(memory="a"), tenant_id=TENANT_A)
    b = sqlite_db.upsert_user_memory(UserMemory(memory="b"), tenant_id=TENANT_B)

    sqlite_db.delete_user_memories([a.memory_id, b.memory_id], tenant_id=TENANT_A)

    assert sqlite_db.get_user_memory(a.memory_id, tenant_id=TENANT_A) is None
    assert sqlite_db.get_user_memory(b.memory_id, tenant_id=TENANT_B) is not None


def test_clear_memories_scoped_to_one_tenant_leaves_the_other_intact(sqlite_db):
    sqlite_db.upsert_user_memory(UserMemory(memory="a"), tenant_id=TENANT_A)
    sqlite_db.upsert_user_memory(UserMemory(memory="b"), tenant_id=TENANT_B)

    sqlite_db.clear_memories(tenant_id=TENANT_A)

    assert sqlite_db.get_user_memories(tenant_id=TENANT_A) == []
    assert len(sqlite_db.get_user_memories(tenant_id=TENANT_B)) == 1


def test_upsert_memories_bulk_stamps_and_preserves_tenant_id(sqlite_db):
    sqlite_db.upsert_memories([UserMemory(memory="a"), UserMemory(memory="b")], tenant_id=TENANT_A)

    assert len(sqlite_db.get_user_memories(tenant_id=TENANT_A)) == 2
    assert sqlite_db.get_user_memories(tenant_id=TENANT_B) == []


# ============================================================================
# FAIL-CLOSED (require_memory_tenant_id)
# ============================================================================


def test_require_memory_tenant_id_refuses_an_unscoped_read():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db = SqliteDb(db_file=path, require_memory_tenant_id=True)
        db.upsert_user_memory(UserMemory(memory="a"), tenant_id=TENANT_A)

        with pytest.raises(TenantScopeRequiredError, match="memory"):
            db.get_user_memories()

        # Scoped calls still work -- the flag refuses *unscoped* access, not all access.
        assert len(db.get_user_memories(tenant_id=TENANT_A)) == 1
    finally:
        os.unlink(path)


def test_require_memory_tenant_id_refuses_an_unscoped_write():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db = SqliteDb(db_file=path, require_memory_tenant_id=True)
        with pytest.raises(TenantScopeRequiredError):
            db.upsert_user_memory(UserMemory(memory="a"))
    finally:
        os.unlink(path)


def test_require_unsupported_memory_tenant_scope_refuses_when_enabled():
    """The guard every backend outside MEMORY_TENANT_SCOPE_SUPPORTED_BACKENDS calls at the end
    of __init__ (e.g. RedisDb, MongoDb, MySQLDb) -- exercised directly here rather than through
    one specific backend's constructor, so this test needs no optional driver installed."""
    require_unsupported_memory_tenant_scope("RedisDb", False)  # does not raise

    with pytest.raises(NotImplementedError, match="RedisDb"):
        require_unsupported_memory_tenant_scope("RedisDb", True)


# ============================================================================
# ASYNC PARITY
# ============================================================================


@requires_aiosqlite
def test_async_sqlite_agrees_with_sync_sqlite_on_cross_tenant_isolation(async_sqlite_db):
    """Sync and async halves scope identically, compared by committed state (SPEC 11.2b) --
    the same property the seed-bridge conformance tests already pin for the manager path."""

    async def scenario():
        await async_sqlite_db.upsert_user_memory(UserMemory(memory="acme secret", user_id="u1"), tenant_id=TENANT_A)
        await async_sqlite_db.upsert_user_memory(UserMemory(memory="globex secret", user_id="u1"), tenant_id=TENANT_B)
        return (
            await async_sqlite_db.get_user_memories(user_id="u1", tenant_id=TENANT_A),
            await async_sqlite_db.get_user_memories(user_id="u1", tenant_id=TENANT_B),
        )

    a, b = run(scenario())
    assert [m.memory for m in a] == ["acme secret"]
    assert [m.memory for m in b] == ["globex secret"]


@requires_aiosqlite
def test_async_sqlite_delete_does_not_cross_tenants(async_sqlite_db):
    async def scenario():
        memory = await async_sqlite_db.upsert_user_memory(UserMemory(memory="acme secret"), tenant_id=TENANT_A)
        await async_sqlite_db.delete_user_memory(memory.memory_id, tenant_id=TENANT_B)
        still_there = await async_sqlite_db.get_user_memory(memory.memory_id, tenant_id=TENANT_A)
        await async_sqlite_db.delete_user_memory(memory.memory_id, tenant_id=TENANT_A)
        gone = await async_sqlite_db.get_user_memory(memory.memory_id, tenant_id=TENANT_A)
        return still_there, gone

    still_there, gone = run(scenario())
    assert still_there is not None
    assert gone is None


@requires_aiosqlite
def test_async_sqlite_require_memory_tenant_id_refuses_an_unscoped_read():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db = AsyncSqliteDb(db_file=path, require_memory_tenant_id=True)

        async def scenario():
            await db.upsert_user_memory(UserMemory(memory="a"), tenant_id=TENANT_A)
            return await db.get_user_memories()

        with pytest.raises(TenantScopeRequiredError):
            run(scenario())
    finally:
        os.unlink(path)


# ============================================================================
# Agentic memory tools degrade rather than crash the run (round-2 review, finding #2)
# ============================================================================


def _agentic_tools(db):
    """The four closures `enable_agentic_memory=True` hands the model."""
    from egpt.memory.manager import MemoryManager

    return {f.__name__: f for f in MemoryManager(db=db)._get_db_tools(user_id="u1", db=db, input_string="hi")}


def _call(tool, name):
    if name == "add_memory":
        return tool("hello")
    if name == "update_memory":
        return tool("mem-1", "updated")
    if name == "delete_memory":
        return tool("mem-1")
    return tool()


@pytest.mark.parametrize("tool_name", ["add_memory", "update_memory", "delete_memory", "clear_memory"])
def test_agentic_memory_tools_degrade_when_tenant_scope_is_required(tool_name):
    """None of these closures can supply a tenant_id -- nothing threads one through
    `_get_db_tools`/`run_memory_task`/`create_user_memories`. So under
    ``require_memory_tenant_id=True`` they must return an error string rather than let
    ``TenantScopeRequiredError`` propagate: the model calls these directly, and an uncaught
    raise crashes the whole agent run on the first agentic memory call (aicoe-tracker#175
    round-2 review, finding #2). `clear_memory` was the one closure still missing the guard.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db = SqliteDb(db_file=path, require_memory_tenant_id=True)
        result = _call(_agentic_tools(db)[tool_name], tool_name)
        assert isinstance(result, str)
        assert "tenant scope required" in result
    finally:
        os.unlink(path)


@requires_aiosqlite
@pytest.mark.parametrize("tool_name", ["add_memory", "update_memory", "delete_memory", "clear_memory"])
def test_async_agentic_memory_tools_degrade_when_tenant_scope_is_required(tool_name):
    """Async twin of the test above -- the async closures are a separate code path
    (`_aget_db_tools`), and `clear_memory` was unguarded on both."""
    from egpt.memory.manager import MemoryManager

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db = AsyncSqliteDb(db_file=path, require_memory_tenant_id=True)

        async def scenario():
            tools = {
                f.__name__: f for f in await MemoryManager(db=db)._aget_db_tools(user_id="u1", db=db, input_string="hi")
            }
            return await _call(tools[tool_name], tool_name)

        result = asyncio.run(scenario())
        assert isinstance(result, str)
        assert "tenant scope required" in result
    finally:
        os.unlink(path)


# ============================================================================
# The unsupported backends refuse to construct (round-1 review, finding #4)
# ============================================================================


# (module path, class name, optional driver to gate on, ctor kwargs)
_UNSUPPORTED_BACKENDS = [
    ("egpt.integrations.db.clickhouse.clickhouse", "ClickhouseDb", "clickhouse_connect", {}),
    ("egpt.integrations.db.dynamo.dynamo", "DynamoDb", "boto3", {}),
    ("egpt.integrations.db.firestore.firestore", "FirestoreDb", "google.cloud.firestore", {}),
    ("egpt.integrations.db.json.json_db", "JsonDb", None, {}),
    ("egpt.integrations.db.mongo.mongo", "MongoDb", "pymongo", {"db_url": "mongodb://localhost:27017"}),
    ("egpt.integrations.db.mysql.mysql", "MySQLDb", "pymysql", {"db_url": "mysql+pymysql://u:p@localhost/d"}),
    ("egpt.integrations.db.redis.redis", "RedisDb", "redis", {}),
    (
        "egpt.integrations.db.singlestore.singlestore",
        "SingleStoreDb",
        "pymysql",
        {"db_url": "mysql+pymysql://u:p@localhost/d"},
    ),
    # SurrealDb takes five required positional args; the guard runs at the end of __init__,
    # so placeholders are enough to reach it.
    (
        "egpt.integrations.db.surrealdb.surrealdb",
        "SurrealDb",
        "surrealdb",
        {"client": None, "db_url": "ws://localhost:8000", "db_creds": {}, "db_ns": "ns", "db_db": "db"},
    ),
]


@pytest.mark.parametrize(
    "module_path,class_name,driver,kwargs",
    _UNSUPPORTED_BACKENDS,
    ids=[b[1] for b in _UNSUPPORTED_BACKENDS],
)
def test_unsupported_backends_refuse_to_construct_with_enforcement_on(module_path, class_name, driver, kwargs):
    """Constructing the *real class* is the point.

    The rest of this file exercises ``require_unsupported_memory_tenant_scope`` as a free function
    with a string name, which cannot catch a backend whose ``__init__`` never calls it -- exactly
    how ``ClickhouseDb`` shipped unwired in an earlier round of this PR (round-1 review,
    finding #4). These construct each class for real, so a future backend that forgets the call
    fails here.
    """
    if driver is not None:
        pytest.importorskip(driver, reason=f"{class_name} imports its driver at module scope")
    module = pytest.importorskip(module_path, reason=f"{class_name} requires its optional dependency")
    backend = getattr(module, class_name)

    with pytest.raises(NotImplementedError) as exc_info:
        backend(require_memory_tenant_id=True, **kwargs)
    assert class_name in str(exc_info.value)


@pytest.mark.parametrize(
    "module_path,class_name,driver,kwargs",
    _UNSUPPORTED_BACKENDS,
    ids=[b[1] for b in _UNSUPPORTED_BACKENDS],
)
def test_unsupported_backends_construct_normally_without_enforcement(module_path, class_name, driver, kwargs):
    """The refusal must be conditional on the flag -- a guard that always raised would break
    every existing deployment on these backends."""
    if driver is not None:
        pytest.importorskip(driver, reason=f"{class_name} imports its driver at module scope")
    module = pytest.importorskip(module_path, reason=f"{class_name} requires its optional dependency")
    backend = getattr(module, class_name)

    try:
        backend(**kwargs)
    except NotImplementedError as e:  # pragma: no cover - the assertion below reports it
        pytest.fail(f"{class_name} raised NotImplementedError with the flag off: {e}")
    except Exception:
        # Connection/credential failures are fine and out of scope: the guard runs at the end of
        # __init__, so anything that gets far enough to fail elsewhere already passed it.
        pass
