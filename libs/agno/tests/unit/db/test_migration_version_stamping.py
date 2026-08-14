"""What ``MigrationManager.up()`` records, and for which tables (aicoe-tracker#178).

Two branches that ran uncovered until SM-C3's mutation sweep found them: the ``None``-version
skip, and the version stamp written for a table no migration touched. Both are pinned here as
they behave **today**, including the wart, so #178 has a test to flip rather than a description
to argue with.

Three candidate fixes for the stamping wart have now been tested and rejected -- see
``TestStampingOnBackendsWithoutSupport``. The rejections are the useful part; without them #178
reads like a one-line change.
"""

import asyncio

import pytest

# See the note in test_memory_migration.py: SqliteDb pulls in sqlalchemy transitively.
pytest.importorskip("sqlalchemy")

from egpt.db.base import AsyncBaseDb, BaseDb  # noqa: E402
from egpt.db.migrations.manager import MigrationManager

from egpt.integrations.db.sqlite.sqlite import SqliteDb

LATEST = MigrationManager.available_versions[-1][1].public


class _UnsupportedDb(BaseDb):
    """A backend no migration version handles, and whose tables therefore never exist -- MySQL,
    DynamoDB and Mongo all behave this way for every table type added since v2.6.0."""

    def __init__(self):
        super().__init__()
        self.versions = {}

    def table_exists(self, table_name):
        return False

    def get_latest_schema_version(self, table_name):
        return self.versions.get(table_name, "2.0.0")

    def upsert_schema_version(self, table_name, version):
        self.versions[table_name] = version


class _BrokenVersionDb(_UnsupportedDb):
    """A backend whose version probe returns None -- "we do not know what state this is in"."""

    def get_latest_schema_version(self, table_name):
        return None


for _cls in (_UnsupportedDb, _BrokenVersionDb):
    _cls.__abstractmethods__ = frozenset()


class TestTheNoneVersionSkip:
    """``None`` means the backend's override is broken, not "unstamped" -- that is ``2.0.0``.

    Skipping is the conservative reading: migrating from an assumed floor would re-run
    migrations against a table that may already be current. Nothing covered this branch, so
    replacing the skip with a floor assumption passed the entire db suite.
    """

    def test_a_table_with_no_version_is_skipped_not_migrated_from_the_floor(self):
        db = _BrokenVersionDb()

        asyncio.run(MigrationManager(db).up())

        assert db.versions == {}, "a stamp here means the table was migrated from an assumed floor"

    def test_the_floor_is_treated_as_unstamped_and_does_migrate(self):
        """The deliberate asymmetry the manager's docstring describes: 2.0.0 is not None."""
        db = _UnsupportedDb()

        asyncio.run(MigrationManager(db).up())

        assert db.versions, "the 2.0.0 floor must be migrated from, unlike None"


class TestStampingOnBackendsWithoutSupport:
    """**Known wart, tracked as aicoe-tracker#178.** A backend that can never have these tables
    is still stamped as though it had them.

    This test passes *because* the behaviour is wrong; it fails when #178 fixes it, which is the
    signal that the fix landed. Do not "fix" the test.

    Why it is not fixed here -- three candidates, all tested against real code:

    1. Stamp only when a migration executed. Regresses supported backends: a SQLite ``sessions``
       table ends up stamped 2.3.0 (the last version that touched it) instead of the target, so
       every later run re-walks the tail of the chain.
    2. Skip the stamp when the table does not exist. Regresses a fresh SQLite database whose
       tables are not built yet -- ``test_schema_version_roundtrip`` pins that it is stamped, and
       the manager cannot tell that case from an unsupported backend: both are "table absent, no
       migration ran".
    3. Per-(version, backend, table type) support declarations. Correct, and a contract change
       across all six shipped migration modules -- which is #178's scope, not a patch bolted onto
       a schema PR.
    """

    def test_an_unsupported_backend_is_stamped_for_tables_it_never_created(self):
        db = _UnsupportedDb()

        asyncio.run(MigrationManager(db).up())

        assert db.table_exists(db.memory_episodic_table_name) is False
        assert db.versions[db.memory_episodic_table_name] == LATEST

    def test_the_wart_predates_the_memory_tiers(self):
        """Sessions is mis-stamped identically, so SM-C3 widened an existing behaviour rather
        than introducing one."""
        db = _UnsupportedDb()

        asyncio.run(MigrationManager(db).up())

        assert db.versions[db.session_table_name] == LATEST


class TestSupportedBackends:
    @pytest.fixture
    def sqlite_db(self, tmp_path):
        return SqliteDb(db_file=str(tmp_path / "stamping.db"))

    def test_a_table_with_nothing_to_migrate_is_still_stamped(self, sqlite_db):
        """The regression guard for candidate 1 above."""
        sqlite_db._get_table(table_type="sessions", create_table_if_not_found=True)
        sqlite_db.upsert_schema_version(sqlite_db.session_table_name, "2.0.0")

        asyncio.run(MigrationManager(sqlite_db).up())

        assert sqlite_db.get_latest_schema_version(sqlite_db.session_table_name) == LATEST

    def test_a_second_run_leaves_the_stamp_where_it_was(self, sqlite_db):
        sqlite_db._create_all_tables()
        asyncio.run(MigrationManager(sqlite_db).up())

        asyncio.run(MigrationManager(sqlite_db).up())

        assert sqlite_db.get_latest_schema_version(sqlite_db.memory_episodic_table_name) == LATEST


def test_the_async_abc_reaches_the_same_code_path():
    """The manager branches on the ABC for every read and write it makes, so an async backend
    that stopped being stamped would be invisible to every test above."""

    class _AsyncDb(AsyncBaseDb):
        def __init__(self):
            super().__init__()
            self.versions = {}

        async def table_exists(self, table_name):
            return False

        async def get_latest_schema_version(self, table_name):
            return self.versions.get(table_name, "2.0.0")

        async def upsert_schema_version(self, table_name, version):
            self.versions[table_name] = version

    _AsyncDb.__abstractmethods__ = frozenset()
    db = _AsyncDb()

    asyncio.run(MigrationManager(db).up())

    assert db.versions[db.memory_episodic_table_name] == LATEST
