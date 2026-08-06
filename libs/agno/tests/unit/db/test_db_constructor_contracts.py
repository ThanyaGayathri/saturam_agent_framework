"""Constructor contracts for DB backends: what the id is seeded from, and what a bad call raises.

Three defects of one family, found by sweeping for siblings of the id-seed precedence bug:

* ``DynamoDb`` seeded from its *arguments* only, never the ``AWS_REGION`` /
  ``AWS_ACCESS_KEY_ID`` environment fallbacks the client is actually built from. So
  ``DynamoDb()`` under env credentials seeded the constant ``"None_None"`` -- every
  env-configured instance colliding on one id -- and got a *different* id from
  ``DynamoDb(region_name=..., aws_access_key_id=...)`` naming the same table set.
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

No server or live connection is touched -- SQLAlchemy engine creation is lazy, and the
DynamoDb client is either injected as a mock or built from fake env credentials.
"""

from unittest.mock import MagicMock, patch

import pytest

from egpt.integrations.db.dynamo.dynamo import DynamoDb
from egpt.integrations.db.mysql.mysql import MySQLDb
from egpt.integrations.db.postgres.async_postgres import AsyncPostgresDb
from egpt.integrations.db.postgres.postgres import PostgresDb

PG_URL = "postgresql+psycopg://u:p@host-a:5432/db"

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def aws_env(monkeypatch):
    """Fake AWS credentials in the environment, as a deployed service would have."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")


# --------------------------------------------------------------------------- #
# DynamoDb -- the seed must describe what the client actually connects to
# --------------------------------------------------------------------------- #


class TestDynamoIdSeed:
    def test_env_and_argument_configuration_agree(self, aws_env):
        """The regression: same target, two ways of naming it, must be one id.

        The client is built from ``region_name or getenv("AWS_REGION")``, so an instance
        configured by environment and one configured by argument are the same database.
        Seeding from the raw arguments made them disagree.
        """
        from_env = DynamoDb()
        from_args = DynamoDb(region_name="us-east-1", aws_access_key_id="AKIAEXAMPLE")

        assert from_env.id == from_args.id, (
            "env-configured and argument-configured instances point at the same DynamoDB; "
            "giving them different ids splits one database into two registry entries"
        )

    def test_different_regions_get_different_ids(self, aws_env):
        assert DynamoDb(region_name="us-east-1").id != DynamoDb(region_name="eu-west-1").id

    def test_different_access_keys_get_different_ids(self, aws_env):
        assert DynamoDb(aws_access_key_id="AKIA_ONE").id != DynamoDb(aws_access_key_id="AKIA_TWO").id

    def test_env_only_instances_are_not_all_identical(self, monkeypatch):
        """Two deployments with different env credentials must not collide.

        Pre-fix both seeded the literal ``"None_None"``.
        """
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_ONE")
        one = DynamoDb()
        monkeypatch.setenv("AWS_REGION", "eu-west-1")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TWO")
        two = DynamoDb()

        assert one.id != two.id

    def test_stable_across_constructions(self, aws_env):
        assert DynamoDb().id == DynamoDb().id

    def test_injected_client_still_seeds_from_the_client(self):
        client = MagicMock()
        assert DynamoDb(db_client=client).id == DynamoDb(db_client=client).id

    def test_explicit_id_wins(self, aws_env):
        assert DynamoDb(id="pinned").id == "pinned"

    def test_missing_region_still_raises_value_error(self, monkeypatch):
        """The guards must keep firing off the resolved values, not the raw arguments."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

        with pytest.raises(ValueError, match="AWS_REGION"):
            DynamoDb()


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
        with patch("egpt.integrations.db.postgres.postgres.create_engine", return_value=MagicMock()):
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
