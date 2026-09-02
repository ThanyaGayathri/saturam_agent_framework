"""What ``MigrationManager.up()`` records, and for which tables (aicoe-tracker#178).

Two branches that ran uncovered until SM-C3's mutation sweep found them: the ``None``-version
skip, and the version stamp written for a table no migration touched. The first is pinned as it
has always behaved -- see ``TestTheNoneVersionSkip``. The second was a wart: a backend with no
migration code for a table type got stamped anyway. ``TestStampingOnBackendsWithoutSupport``
records how #178 fixed it -- via a declarative ``supports(table_type, db_type)`` predicate that
each ``versions/vX_Y_Z`` module exposes and ``MigrationManager._supports`` checks across the
whole catalogue -- and keeps the two rejected candidates on record so the fix is not re-litigated.
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

    def test_the_floor_is_treated_as_unstamped_and_does_migrate(self, tmp_path):
        """The deliberate asymmetry the manager's docstring describes: 2.0.0 is not None.

        Needs a backend at least one shipped migration actually supports -- ``_UnsupportedDb``
        would never be stamped regardless of this branch, since #178, which would make this
        pass for the wrong reason. ``_create_all_tables()`` alone is not enough either: SQLite
        stamps every table it creates at ``latest_schema_version`` in ``_get_or_create_table``,
        so the version has to be forced back down to the floor first, or the assertion below
        would pass even if this branch were broken.
        """
        db = SqliteDb(db_file=str(tmp_path / "floor.db"))
        db._create_all_tables()
        db.upsert_schema_version(db.session_table_name, "2.0.0")

        asyncio.run(MigrationManager(db).up())

        assert db.get_latest_schema_version(db.session_table_name) == LATEST, (
            "the 2.0.0 floor must be migrated from, unlike None"
        )


class TestStampingOnBackendsWithoutSupport:
    """**aicoe-tracker#178, fixed.** A backend no shipped migration recognizes for a table type
    is left unstamped for it, rather than being stamped as though the (nonexistent) work were
    done.

    The fix is candidate 3 from the earlier investigation: each ``versions/vX_Y_Z`` module now
    declares ``supports(table_type, db_type)`` -- the same predicate that gates its own
    ``up``/``down`` dispatch, unifying both the *table_type* and *db_type* halves of that check
    into one declaration per module (per-backend-family tuples the dispatch branches read
    directly, not a second hand-maintained set) -- and ``MigrationManager._supports`` checks it
    across the whole catalogue before ``up()`` will stamp a table.
    ``test_schema_version_contract.py``'s per-``(version, table_type, db_type)`` truth table pins
    that each module's ``supports()`` answers correctly on its own, not just as part of the
    aggregate this class exercises. Two other candidates were tried first and rejected:

    1. Stamp only when a migration executed. Regresses supported backends: a SQLite ``sessions``
       table ends up stamped 2.3.0 (the last version that touched it) instead of the target, so
       every later run re-walks the tail of the chain. ``TestSupportedBackends`` below is the
       regression guard for this.
    2. Skip the stamp when the table does not exist. Regresses a fresh SQLite database whose
       tables are not built yet -- ``test_schema_version_roundtrip`` pins that it is stamped, and
       the manager cannot tell that case from an unsupported backend: both are "table absent, no
       migration ran". The declarative predicate sidesteps this because it never looks at
       ``table_exists`` at all.
    """

    def test_an_unsupported_backend_is_left_unstamped(self):
        db = _UnsupportedDb()

        asyncio.run(MigrationManager(db).up())

        assert db.table_exists(db.memory_episodic_table_name) is False
        assert db.memory_episodic_table_name not in db.versions

    def test_the_fix_covers_backends_predating_the_memory_tiers_too(self):
        """Sessions was mis-stamped identically before the fix, so this covers the same
        mechanism SM-C3 merely widened rather than introduced."""
        db = _UnsupportedDb()

        asyncio.run(MigrationManager(db).up())

        assert db.session_table_name not in db.versions


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


class TestATableTypeWithNoMigrationsOfItsOwn:
    """``component_audit`` (EGPT-2.6): a table type born fully-specified, with no migration that
    ever needs to touch it.

    This is a third shape the declarative model has to handle, distinct from both cases above:
    unlike ``memory_episodic`` on an unsupported backend, ``v2_6_0.supports()`` returns ``True``
    for ``component_audit`` (it was flagged in review as a table with zero supporting versions,
    which would leave it permanently unstamped on every backend); unlike ``sessions``, no
    migration module ever runs DDL against it, because ``tenant_id`` and its index already exist
    at creation. ``v2_6_0.up``/``down`` special-case it into a true no-op rather than folding it
    into ``COMPONENT_TABLE_TYPES``, since -- unlike the other three component tables --
    ``component_audit``'s ``tenant_id`` is not optional and must never be reverted.
    """

    @pytest.fixture
    def sqlite_db(self, tmp_path):
        return SqliteDb(db_file=str(tmp_path / "component_audit_stamping.db"))

    def test_it_is_stamped_to_latest_despite_no_migration_touching_it(self, sqlite_db):
        sqlite_db._create_all_tables()
        sqlite_db.upsert_schema_version(sqlite_db.component_audit_table_name, "2.0.0")

        asyncio.run(MigrationManager(sqlite_db).up())

        assert sqlite_db.get_latest_schema_version(sqlite_db.component_audit_table_name) == LATEST

    def test_down_never_touches_its_structural_tenant_id(self, sqlite_db):
        """tenant_id on component_audit predates every migration in the catalogue -- dropping it
        would break the table's own write path, which assumes the column always exists."""
        from sqlalchemy import text

        sqlite_db._create_all_tables()
        table_name = sqlite_db.component_audit_table_name

        asyncio.run(MigrationManager(sqlite_db).down("2.0.0", table_type="component_audit", force=True))

        with sqlite_db.Session() as sess:
            columns = {row[1] for row in sess.execute(text(f"PRAGMA table_info({table_name})")).fetchall()}
        assert "tenant_id" in columns


def test_the_async_abc_reaches_the_same_code_path():
    """The manager branches on the ABC for every read and write it makes, so an async backend
    that started being wrongly stamped again would be invisible to every test above."""

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

    assert db.memory_episodic_table_name not in db.versions


def test_a_supported_async_backend_is_still_stamped(tmp_path):
    """The async mirror of ``TestSupportedBackends``: the fix must not make async backends
    ``MigrationManager`` genuinely supports stop being stamped."""
    pytest.importorskip("aiosqlite", reason="AsyncSqliteDb needs a real async engine")
    from egpt.integrations.db.sqlite.async_sqlite import AsyncSqliteDb

    db = AsyncSqliteDb(db_file=str(tmp_path / "async_stamping.db"))

    asyncio.run(MigrationManager(db).up())

    assert asyncio.run(db.get_latest_schema_version(db.session_table_name)) == LATEST
