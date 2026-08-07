"""Constructor contracts for DB backends: what the id is seeded from, and what a bad call raises.

Two defects of one family, found by sweeping for siblings of the id-seed precedence bug
(the DynamoDb sibling lives in ``test_dynamo_id_seed.py`` behind a boto3 skip, so this
file stays collectable on an environment without the AWS SDK):

* ``AsyncPostgresDb`` passed no seed at all, falling through to ``AsyncBaseDb``'s
  ``id or str(uuid4())``. Every construction got a fresh random id, so
  ``resolve_db_from_config`` -- which keys component lookup on the id -- could never match
  a stored config across a restart.
* ``MySQLDb`` dereferenced ``db_engine.url`` while seeding, *above* the guard that raises
  ``ValueError`` for a missing connection, so ``MySQLDb()`` died with ``AttributeError``
  instead of the error its own docstring promises. ``PostgresDb`` orders these correctly
  and is the shape the fix restores.

The shared consequence is the one ``Registry`` + ``resolve_db_from_config`` care about: ids
that collide across distinct targets, or never match the same target twice.

No server or live connection is touched, and no optional DB driver is needed: the engine
factories are stubbed below because ``create_engine`` / ``create_async_engine`` resolve
their dialect's driver package (``psycopg``, ``pymysql``) eagerly, and the seed logic under
test never touches the engine. What the stub preserves is the contract that matters here:
the factory is still called and its result assigned, and the guards fire *before* any
factory call on the no-connection path.
"""

from types import SimpleNamespace

import pytest

from egpt.integrations.db.mysql.mysql import MySQLDb
from egpt.integrations.db.postgres.async_postgres import AsyncPostgresDb
from egpt.integrations.db.postgres.postgres import PostgresDb

PG_URL = "postgresql+psycopg://u:p@host-a:5432/db"

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _fake_engine(url, *args, **kwargs):
    """Stand-in for create_engine / create_async_engine that skips dialect resolution.

    The real factories import the dialect's driver package (psycopg, pymysql) before
    returning; none of that is under test here, and requiring the drivers made these
    tests error rather than run on a driverless environment.
    """
    return SimpleNamespace(url=url, dispose=lambda: None)


@pytest.fixture(autouse=True)
def stub_engines(monkeypatch):
    monkeypatch.setattr("egpt.integrations.db.postgres.postgres.create_engine", _fake_engine)
    monkeypatch.setattr("egpt.integrations.db.postgres.async_postgres.create_async_engine", _fake_engine)
    monkeypatch.setattr("egpt.integrations.db.mysql.mysql.create_engine", _fake_engine)


# --------------------------------------------------------------------------- #
# AsyncPostgresDb -- an id that survives a restart
# --------------------------------------------------------------------------- #


class TestAsyncPostgresIdSeed:
    def test_id_is_deterministic(self):
        """The regression: this was a fresh uuid4 on every construction."""
        assert AsyncPostgresDb(db_url=PG_URL).id == AsyncPostgresDb(db_url=PG_URL).id, (
            "a random id can never match a stored component config, so resolve_db_from_config "
            "always falls through to building a fresh instance with its own engine"
        )

    def test_different_urls_get_different_ids(self):
        other = "postgresql+psycopg://u:p@host-b:5432/db"
        assert AsyncPostgresDb(db_url=PG_URL).id != AsyncPostgresDb(db_url=other).id

    def test_schema_contributes_to_the_seed(self):
        assert AsyncPostgresDb(db_url=PG_URL).id != AsyncPostgresDb(db_url=PG_URL, db_schema="other").id

    def test_explicit_id_wins(self):
        assert AsyncPostgresDb(db_url=PG_URL, id="pinned").id == "pinned"

    def test_matches_the_sync_backend_for_the_same_target(self):
        """Sync and async on one target share an id, as sqlite and mysql already do.

        Harmless because ``Registry.add_db`` tracks only sync ``BaseDb`` instances, so the
        two can never contend for the same registry slot.
        """
        sync_db = PostgresDb(db_url=PG_URL)

        assert AsyncPostgresDb(db_url=PG_URL).id == sync_db.id


# --------------------------------------------------------------------------- #
# Constructor guards -- the documented error, not an AttributeError
# --------------------------------------------------------------------------- #


class TestConstructorGuards:
    """A missing connection must raise the ValueError each docstring promises.

    Mirrors ``tests/unit/vectordb/test_pgvector.py``, which asserts this contract for
    PgVector and passes only because that class already guards before it seeds.
    """

    @pytest.mark.parametrize("cls", [MySQLDb, PostgresDb, AsyncPostgresDb], ids=["mysql", "postgres", "async_postgres"])
    def test_no_connection_raises_value_error(self, cls):
        with pytest.raises(ValueError, match="db_url or db_engine"):
            cls()

    def test_mysql_seed_is_unchanged_on_paths_that_already_worked(self):
        """Reordering the guard above the seed must not shift any existing id."""
        a = MySQLDb(db_url="mysql+pymysql://u:p@host-a:3306/db")
        b = MySQLDb(db_url="mysql+pymysql://u:p@host-b:3306/db")

        assert a.id != b.id
        assert a.id == MySQLDb(db_url="mysql+pymysql://u:p@host-a:3306/db").id
        assert a.db_engine is not None, "engine must still be built and assigned"
        assert a.db_schema == "ai"
