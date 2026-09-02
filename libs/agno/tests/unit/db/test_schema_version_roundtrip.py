"""Schema versions must actually persist, per table (aicoe-tracker#160).

The signature contract lives in `test_schema_version_contract.py` and needs no database.
This file checks the behaviour behind it on the backends that can run without a container:
a version written for one table is readable back, does not leak into another table, and an
unwritten table reports the "2.0.0" floor rather than None.

The "2.0.0" floor matters more than it looks. `MigrationManager` feeds this straight into
`packaging.version.parse`, so None raises; and a backend that forgets what it stored would
silently re-run every migration on every startup.

Redis can forget on a delay rather than outright, so it gets its own section at the bottom:
a version record written under the instance's configured `expire` reads back as the floor
once the TTL lapses, which is the same silent re-run arriving hours later. Those tests drive
a mock client, so they need no container either.
"""

from unittest.mock import MagicMock

import pytest

from egpt.db.migrations.manager import MigrationManager

TABLE_A = "egpt_sessions"
TABLE_B = "egpt_memories"

LATEST = MigrationManager.available_versions[-1][1].public


@pytest.fixture
def in_memory_db():
    from egpt.integrations.db.in_memory.in_memory_db import InMemoryDb

    return InMemoryDb()


@pytest.fixture
def json_db(tmp_path):
    from egpt.integrations.db.json.json_db import JsonDb

    return JsonDb(db_path=str(tmp_path))


@pytest.fixture
def sqlite_db(tmp_path):
    pytest.importorskip("sqlalchemy", reason="SqliteDb needs a real engine")
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    return SqliteDb(db_file=str(tmp_path / "versions.db"))


BACKEND_FIXTURES = ["in_memory_db", "json_db", "sqlite_db"]

# aicoe-tracker#178: MigrationManager only stamps a table when at least one shipped migration
# declares it can handle (table_type, db_type). No migration module has ever recognized
# InMemoryDb or JsonDb -- they are not SQL backends any ``versions/vX_Y_Z`` module dispatches
# on -- so up() correctly leaves them at the "2.0.0" floor rather than claiming work that never
# ran. SqliteDb is a real, recognized backend and is stamped to the latest version as before.
STAMPED_TO_LATEST = {"sqlite_db"}


@pytest.mark.parametrize("db_fixture", BACKEND_FIXTURES)
def test_unwritten_table_reports_the_floor_not_none(db_fixture, request):
    """None would reach packaging.version.parse and raise, taking the whole run down."""
    db = request.getfixturevalue(db_fixture)

    version = db.get_latest_schema_version(TABLE_A)

    assert version is not None
    assert version == "2.0.0"


@pytest.mark.parametrize("db_fixture", BACKEND_FIXTURES)
def test_version_round_trips(db_fixture, request):
    db = request.getfixturevalue(db_fixture)

    db.upsert_schema_version(TABLE_A, "2.5.6")

    assert db.get_latest_schema_version(TABLE_A) == "2.5.6"


@pytest.mark.parametrize("db_fixture", BACKEND_FIXTURES)
def test_versions_are_per_table(db_fixture, request):
    """The whole reason the contract passes a table_name."""
    db = request.getfixturevalue(db_fixture)

    db.upsert_schema_version(TABLE_A, "2.5.6")

    assert db.get_latest_schema_version(TABLE_A) == "2.5.6"
    assert db.get_latest_schema_version(TABLE_B) == "2.0.0"

    db.upsert_schema_version(TABLE_B, "2.3.0")

    assert db.get_latest_schema_version(TABLE_A) == "2.5.6"
    assert db.get_latest_schema_version(TABLE_B) == "2.3.0"


@pytest.mark.parametrize("db_fixture", BACKEND_FIXTURES)
def test_upsert_overwrites_rather_than_duplicating(db_fixture, request):
    db = request.getfixturevalue(db_fixture)

    db.upsert_schema_version(TABLE_A, "2.3.0")
    db.upsert_schema_version(TABLE_A, "2.5.0")
    db.upsert_schema_version(TABLE_A, "2.5.6")

    assert db.get_latest_schema_version(TABLE_A) == "2.5.6"


@pytest.mark.parametrize("db_fixture", BACKEND_FIXTURES)
@pytest.mark.asyncio
async def test_migration_manager_runs_and_records(db_fixture, request):
    """End to end: the manager reads, migrates and writes the version back.

    Only for a backend at least one shipped migration recognizes -- see ``STAMPED_TO_LATEST``.
    """
    db = request.getfixturevalue(db_fixture)

    await MigrationManager(db).up()

    expected = LATEST if db_fixture in STAMPED_TO_LATEST else "2.0.0"
    assert db.get_latest_schema_version(TABLE_A) == expected


@pytest.mark.parametrize("db_fixture", BACKEND_FIXTURES)
@pytest.mark.asyncio
async def test_second_up_is_a_no_op(db_fixture, request):
    """A backend that forgets its version would re-run every migration, every start."""
    db = request.getfixturevalue(db_fixture)

    await MigrationManager(db).up()
    stamped = db.get_latest_schema_version(TABLE_A)
    await MigrationManager(db).up()

    expected = LATEST if db_fixture in STAMPED_TO_LATEST else "2.0.0"
    assert db.get_latest_schema_version(TABLE_A) == stamped == expected


def test_json_versions_survive_a_new_instance(tmp_path):
    """File-backed backends must persist across process restarts, not just in memory."""
    from egpt.integrations.db.json.json_db import JsonDb

    JsonDb(db_path=str(tmp_path)).upsert_schema_version(TABLE_A, "2.5.6")

    assert JsonDb(db_path=str(tmp_path)).get_latest_schema_version(TABLE_A) == "2.5.6"


def test_sqlite_versions_survive_a_new_instance(tmp_path):
    pytest.importorskip("sqlalchemy", reason="SqliteDb needs a real engine")
    from egpt.integrations.db.sqlite.sqlite import SqliteDb

    db_file = str(tmp_path / "versions.db")
    SqliteDb(db_file=db_file).upsert_schema_version(TABLE_A, "2.5.6")

    assert SqliteDb(db_file=db_file).get_latest_schema_version(TABLE_A) == "2.5.6"


# -- Redis: the version record must outlive the data it describes --

TTL_SECONDS = 60


@pytest.fixture
def redis_db_with_ttl():
    """A RedisDb with `expire` configured, driven by a mock client -- no container needed."""
    pytest.importorskip("redis", reason="RedisDb imports the redis SDK at module level")
    from egpt.integrations.db.redis.redis import RedisDb

    client = MagicMock()
    client.get.return_value = None  # every table starts unwritten

    return RedisDb(redis_client=client, expire=TTL_SECONDS), client


def test_version_records_are_exempt_from_the_configured_ttl(redis_db_with_ttl):
    """A version record that expires reads back as the floor and re-runs every migration.

    `_store_record` applies `self.expire` by default, so `upsert_schema_version` has to opt
    out via `apply_expiry=False`. This pins that opt-out: drop it in a refactor, or forget it
    at a new call site, and the bug this suite exists to kill comes back on a TTL delay.
    """
    db, client = redis_db_with_ttl

    db.upsert_schema_version(TABLE_A, "2.5.6")

    key, _ = client.set.call_args.args
    assert "versions" in key
    assert client.set.call_args.kwargs.get("ex") is None, (
        "a version record with a TTL expires into the 2.0.0 floor and re-runs every migration"
    )


def test_the_ttl_exemption_is_targeted_not_a_broken_expire(redis_db_with_ttl):
    """The control for the test above: ordinary records on the same instance still expire.

    Without this, `expire` being ignored everywhere would pass the exemption test too.
    """
    from egpt.session import AgentSession

    db, client = redis_db_with_ttl

    db.upsert_session(AgentSession(session_id="s1", runs=[]))

    key, _ = client.set.call_args.args
    assert "sessions" in key
    assert client.set.call_args.kwargs.get("ex") == TTL_SECONDS
