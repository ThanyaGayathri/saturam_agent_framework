"""v2_3_0.py's ``up``/``down`` must report real work only for the table type they actually have
DDL for (aicoe-tracker#178, round-3 review).

``supports()`` declares six table types on every backend it recognizes, but only ``"memories"``
has DDL beyond a table-exists probe on non-Postgres backends -- Postgres genuinely converts
JSON to JSONB for all six, but MySQL, SQLite and SingleStore have no JSON/JSONB distinction to
convert, and the revert helpers on every backend only ever undo the "memories" column additions,
never the JSON->JSONB conversion. Before this fix, every migrate/revert helper except Postgres's
and SQLite's revert helpers ended with an unconditional ``sess.commit(); return True`` regardless
of ``table_type`` -- so calling ``down()`` for e.g. "sessions" reported success and caused
``MigrationManager.down()`` to write a downgrade stamp with nothing actually reverted (harmless
for ``up()``, since ``MigrationManager.up()`` stamps the walked-to version unconditionally
regardless of what a migration module returns -- but very much not for ``down()``, which only
stamps when a migration module reports ``True``).

This file pins that every migrate/revert helper touched by that fix now returns ``True`` only
for ``"memories"`` and ``False`` for the other five table types in ``_TABLE_TYPES``, given a
table that exists. It intentionally exercises the private helpers directly (with a fake session)
rather than only the public dispatch, since the bug lived entirely inside those helper bodies and
was invisible to dispatch-level mocking (see ``test_migration_v2_5_0_dispatch.py`` for that
style, and ``test_schema_version_contract.py``'s truth table, which only pins ``supports()``'s
return value, not that a ``True`` corresponds to real DDL).
"""

from unittest.mock import MagicMock

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")

from egpt.db.migrations.versions import v2_3_0  # noqa: E402
from egpt.integrations.db.sqlite.sqlite import SqliteDb  # noqa: E402

NON_MEMORIES_TABLE_TYPES = ["sessions", "evals", "metrics", "knowledge", "culture"]


# ---------------------------------------------------------------------------
# Real backend: SQLite (no DB driver required)
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_db(tmp_path):
    db = SqliteDb(db_file=str(tmp_path / "v2_3_0_scoping.db"))
    db._get_table(table_type="memories", create_table_if_not_found=True)
    db._get_table(table_type="sessions", create_table_if_not_found=True)
    return db


def test_sqlite_up_reports_work_only_for_memories(sqlite_db):
    assert v2_3_0.up(sqlite_db, "memories", sqlite_db.memory_table_name) is True


@pytest.mark.parametrize("table_type", NON_MEMORIES_TABLE_TYPES)
def test_sqlite_up_reports_no_work_for_other_table_types(sqlite_db, table_type):
    # "sessions" is the only one of these with a real table in the fixture; the others correctly
    # return False for the (different, table-does-not-exist) reason `_migrate_sqlite` already
    # handled before this fix -- covered here for completeness alongside the fixed table type.
    table_name = sqlite_db.session_table_name if table_type == "sessions" else "does_not_exist"
    assert v2_3_0.up(sqlite_db, table_type, table_name) is False


# ---------------------------------------------------------------------------
# Fake session harness for backends with no local driver (MySQL, SingleStore) and for
# Postgres's revert path (its up() path already correctly handles all six table types and is
# unaffected by this fix).
# ---------------------------------------------------------------------------


class _FakeCtx:
    def __init__(self, value):
        self._value = value

    def __enter__(self):
        return self._value

    def __exit__(self, *exc):
        return False

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


class _FakeResult:
    """Stands in for a SQLAlchemy Result: `.scalar()` for table/index-exists probes, `.fetchall()`
    and direct iteration for column-listing probes."""

    def __init__(self, scalar=True, rows=()):
        self._scalar = scalar
        self._rows = list(rows)

    def scalar(self):
        return self._scalar

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    """A no-op session: `table_exists`-style probes report True, every other probe reports an
    empty result, and DDL statements are simply recorded rather than executed against a real
    database. Sufficient to drive a helper past its table-exists guard into (or around) its
    "memories"-only DDL branch."""

    def __init__(self):
        self.executed = []

    def begin(self):
        return _FakeCtx(self)

    def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        return _FakeResult(scalar=True, rows=())

    def commit(self):
        pass


class _FakeAsyncSession(_FakeSession):
    async def execute(self, stmt, params=None):  # type: ignore[override]
        self.executed.append((str(stmt), params))
        return _FakeResult(scalar=True, rows=())

    async def commit(self):
        pass


def _make_sync_db(class_name: str):
    sess = _FakeSession()

    class FakeDb:
        db_schema = "agno"

        def Session(self):
            return _FakeCtx(sess)

    FakeDb.__name__ = class_name
    FakeDb.__qualname__ = class_name
    return FakeDb()


def _make_async_db(class_name: str):
    sess = _FakeAsyncSession()

    class FakeDb:
        db_schema = "agno"

        def async_session_factory(self):
            return _FakeCtx(sess)

    FakeDb.__name__ = class_name
    FakeDb.__qualname__ = class_name
    return FakeDb()


@pytest.mark.parametrize(
    "fn_name, db_factory, db_type",
    [
        ("_migrate_mysql", _make_sync_db, "MySQLDb"),
        ("_migrate_singlestore", _make_sync_db, "SingleStoreDb"),
        ("_revert_mysql", _make_sync_db, "MySQLDb"),
        ("_revert_singlestore", _make_sync_db, "SingleStoreDb"),
        ("_revert_postgres", _make_sync_db, "PostgresDb"),
    ],
)
def test_sync_helper_reports_work_only_for_memories(fn_name, db_factory, db_type):
    fn = getattr(v2_3_0, fn_name)
    db = db_factory(db_type)

    assert fn(db, "memories", "some_table") is True
    for table_type in NON_MEMORIES_TABLE_TYPES:
        assert fn(db_factory(db_type), table_type, "some_table") is False, (
            f"{fn_name} must return False for table_type={table_type!r} -- it has no DDL for it"
        )


@pytest.mark.asyncio
async def test_revert_async_postgres_reports_work_only_for_memories():
    db = _make_async_db("AsyncPostgresDb")
    assert await v2_3_0._revert_async_postgres(db, "memories", "some_table") is True

    for table_type in NON_MEMORIES_TABLE_TYPES:
        db = _make_async_db("AsyncPostgresDb")
        assert await v2_3_0._revert_async_postgres(db, table_type, "some_table") is False, (
            f"_revert_async_postgres must return False for table_type={table_type!r} -- the "
            "JSON->JSONB conversion is never reverted for any table type"
        )


def test_migrate_postgres_still_reports_work_for_every_table_type():
    """Unlike the helpers above, `_migrate_postgres` genuinely converts JSON to JSONB for all
    six table types in `_TABLE_TYPES` -- its unconditional `return True` was correct before this
    fix and remains correct; this is the control case."""
    db = MagicMock()
    db.db_schema = "agno"
    sess = _FakeSession()
    db.Session.return_value = _FakeCtx(sess)

    for table_type in ["memories", *NON_MEMORIES_TABLE_TYPES]:
        assert v2_3_0._migrate_postgres(db, table_type, "some_table") is True
