"""Migration v2.8.0 against a real SQLite database (SM-C3 #91).

A real database rather than mocks, because what is under test is the DDL and its revert -- a
mocked session asserts that a string was passed somewhere, not that the column exists
afterwards. Postgres runs the same code paths but is not exercised here: no server and no
driver in this lane, so its coverage is the shared planners below plus the schema parity
properties in ``test_memory_schemas.py``. Anything Postgres-specific beyond that is unverified,
and is stated as such rather than implied.
"""

import asyncio
import os
import tempfile

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from egpt.db.migrations.manager import MigrationManager
from egpt.db.migrations.versions.v2_8_0 import (
    _index_name,
    _plan_tenant_down,
    _plan_tenant_up,
    _sqlite_supports_drop_column,
    up,
)
from egpt.db.schemas.memory import (
    EpisodicRecord,
    MemoryLink,
    ProceduralRecord,
    SemanticEntity,
    SemanticFact,
    SemanticRelation,
)
from egpt.db.table_names import MEMORY_TIER_TABLE_TYPES, memory_tier_table_attr

from egpt.integrations.db.sqlite.sqlite import SqliteDb

PREVIOUS_VERSION = "2.7.0"
TENANT = "acme"
OTHER_TENANT = "globex"

# One populated row per tier, with every column family exercised: JSON collections, the
# temporal window, decay, and the embedding triple.
ROWS = {
    "memory_episodic": EpisodicRecord(
        tenant_id=TENANT,
        id="ep-1",
        content="the user shipped v2",
        occurred_at=1_700_000_000,
        location="berlin",
        cause="release",
        entity_ids=["e1", "e2"],
        embedding=[0.1, 0.2, 0.3],
        embedding_version="bge-base-en-v1.5/d3/cos/v1",
        importance=0.8,
        t_valid=1_700_000_000,
        revision=2,
        supersedes=["ep-0"],
        superseded_by=["ep-2", "ep-3"],
        boundary_method="time_gap",
        boundary_trigger=900.0,
        boundary_confidence=1.0,
        refined=True,
        metadata={"source": "chat"},
    ),
    "memory_semantic_fact": SemanticFact(
        tenant_id=TENANT,
        id="fact-1",
        content="acme runs postgres",
        subject="acme",
        predicate="runs",
        object="postgres",
        confidence=0.9,
        source_episode_ids=["ep-1"],
        derived_by="extract_v1",
        entity_ids=["e1"],
        embedding=[0.4, 0.5],
        t_valid=1_700_000_000,
        t_invalid=1_800_000_000,
    ),
    "memory_entity": SemanticEntity(
        tenant_id=TENANT,
        id="e1",
        entity_type="company",
        name="Acme",
        description="a customer",
        properties={"tier": "enterprise"},
        embedding=[0.6],
    ),
    "memory_relation": SemanticRelation(
        tenant_id=TENANT,
        id="rel-1",
        subject_entity_id="e1",
        predicate="works_at",
        object_entity_id="e2",
        confidence=0.75,
        source_episode_ids=["ep-1"],
        derived_by="extract_v1",
        t_valid=1_700_000_000,
    ),
    "memory_procedural": ProceduralRecord(
        tenant_id=TENANT,
        id="proc-1",
        kind="skill",
        title="cite sources",
        content="always cite the source",
        trigger="on answer",
        tags=["style", "rigour"],
        success_count=3,
        embedding=[0.7, 0.8],
    ),
    "memory_link": MemoryLink(
        tenant_id=TENANT,
        id="link-1",
        from_id="ep-1",
        from_tier="episodic",
        to_id="fact-1",
        to_tier="semantic",
        link_type="distilled_into",
        t_valid=1_700_000_000,
    ),
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def db(tmp_path):
    return SqliteDb(db_file=str(tmp_path / "memory.db"))


def migrate_up(database):
    asyncio.run(MigrationManager(database).up())


def migrate_down(database, target=PREVIOUS_VERSION):
    asyncio.run(MigrationManager(database).down(target_version=target))


def table_names(database):
    with database.Session() as sess:
        return {row[0] for row in sess.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}


def columns(database, table_name):
    with database.Session() as sess:
        return {row[1] for row in sess.execute(text(f"PRAGMA table_info({table_name})")).fetchall()}


def index_names(database, table_name):
    with database.Session() as sess:
        return {row[1] for row in sess.execute(text(f"PRAGMA index_list({table_name})")).fetchall()}


def insert(database, table_type, row):
    table = database._get_table(table_type=table_type, create_table_if_not_found=False)
    assert table is not None, f"{table_type} was not created by the migration"
    with database.Session() as sess, sess.begin():
        sess.execute(table.insert().values(**row.to_dict()))


def read(database, table_type, row_id, tenant=TENANT):
    table = database._get_table(table_type=table_type, create_table_if_not_found=False)
    with database.Session() as sess:
        result = sess.execute(table.select().where(table.c.id == row_id).where(table.c.tenant_id == tenant)).fetchone()
    return dict(result._mapping) if result is not None else None


def make_pre_2_8_memories_table(database):
    """Recreate the memories table as a pre-2.8 deployment has it: no tenant_id, no index, and
    stamped at the previous schema version.

    Two halves, both load-bearing. The builder now ships the column, so migrating a table it
    created would assert nothing. And a freshly built table is stamped at the *latest* version,
    so without rewinding the stamp the runner correctly skips it and the test would pass while
    exercising no migration at all.
    """
    database._get_table(table_type="memories", create_table_if_not_found=True)
    name = database.memory_table_name
    with database.Session() as sess, sess.begin():
        sess.execute(text(f"DROP INDEX IF EXISTS {_index_name(name)}"))
        sess.execute(text(f"ALTER TABLE {name} DROP COLUMN tenant_id"))
    database.upsert_schema_version(name, PREVIOUS_VERSION)
    database.metadata.clear()
    return name


# --------------------------------------------------------------------------- #
# The pure planners -- shared by the sync and async halves
# --------------------------------------------------------------------------- #


class TestPlanners:
    """These decide *what* runs; the sync and async halves only decide how. Postgres's SQL is
    only reachable here, so these are the whole of its coverage in this lane."""

    @pytest.mark.parametrize(
        "db_type,expected_type", [("PostgresDb", "VARCHAR"), ("AsyncPostgresDb", "VARCHAR"), ("SqliteDb", "TEXT")]
    )
    def test_the_column_type_follows_the_dialect(self, db_type, expected_type):
        statements = _plan_tenant_up(db_type, '"t"', "idx_t_tenant_id", has_column=False, has_index=False)

        assert f"ADD COLUMN tenant_id {expected_type}" in statements[0]

    def test_nothing_is_planned_when_the_column_and_index_are_present(self):
        assert _plan_tenant_up("SqliteDb", '"t"', "idx", has_column=True, has_index=True) == []

    def test_only_the_missing_half_is_planned(self):
        statements = _plan_tenant_up("SqliteDb", '"t"', "idx", has_column=True, has_index=False)

        assert len(statements) == 1 and statements[0].startswith("CREATE INDEX")

    def test_the_revert_drops_the_index_before_the_column(self):
        """Both engines refuse to drop a column an index still references, so the order is
        load-bearing rather than stylistic."""
        statements = _plan_tenant_down("SqliteDb", '"t"', "idx", has_column=True, has_index=True, can_drop_column=True)

        assert [s.split()[0:2] for s in statements] == [["DROP", "INDEX"], ["ALTER", "TABLE"]]

    def test_the_revert_keeps_the_column_when_the_engine_cannot_drop_it(self):
        statements = _plan_tenant_down("SqliteDb", '"t"', "idx", has_column=True, has_index=True, can_drop_column=False)

        assert statements == ['DROP INDEX "idx"']

    def test_the_postgres_revert_qualifies_the_index_with_its_schema(self):
        statements = _plan_tenant_down(
            "PostgresDb", '"s"."t"', "idx", has_column=True, has_index=True, can_drop_column=True, db_schema="s"
        )

        assert statements[0] == 'DROP INDEX "s"."idx"'

    @pytest.mark.parametrize(
        "version,supported", [("3.35.0", True), ("3.34.9", False), ("3.45.1", True), ("not.a.version", False)]
    )
    def test_the_sqlite_drop_column_floor(self, version, supported):
        assert _sqlite_supports_drop_column(version) is supported


# --------------------------------------------------------------------------- #
# The tier tables
# --------------------------------------------------------------------------- #


class TestTierTables:
    def test_up_creates_every_tier_table(self, db):
        migrate_up(db)

        created = table_names(db)
        for table_type in MEMORY_TIER_TABLE_TYPES:
            assert f"egpt_{table_type}" in created

    @pytest.mark.parametrize("table_type", MEMORY_TIER_TABLE_TYPES)
    def test_the_created_columns_match_the_schema_definition(self, db, table_type):
        from egpt.integrations.db.sqlite.schemas import get_table_schema_definition

        migrate_up(db)
        declared = {name for name in get_table_schema_definition(table_type) if not name.startswith("__")}

        assert columns(db, f"egpt_{table_type}") == declared

    @pytest.mark.parametrize("table_type", sorted(ROWS))
    def test_a_row_per_tier_inserts_and_reads_back_intact(self, db, table_type):
        """The acceptance criterion: insert/read verified per tier, on a real database."""
        migrate_up(db)
        row = ROWS[table_type]

        insert(db, table_type, row)
        stored = read(db, table_type, row.id)

        assert stored is not None
        assert stored == row.to_dict(), "a column dropped or coerced by the DDL shows up here"

    @pytest.mark.parametrize("table_type", sorted(ROWS))
    def test_collections_survive_the_round_trip_as_collections(self, db, table_type):
        """JSON columns standing in for the DDL's TEXT[] must come back as lists, not strings."""
        migrate_up(db)
        row = ROWS[table_type]
        insert(db, table_type, row)

        stored = read(db, table_type, row.id)

        assert isinstance(stored["metadata"], dict)
        for name in ("entity_ids", "supersedes", "superseded_by", "source_episode_ids", "tags", "embedding"):
            if name in stored and stored[name] is not None:
                assert isinstance(stored[name], list), name

    def test_the_episodic_supersede_graph_reads_back_as_a_real_list(self, db):
        """SM-R6 re-segmentation can merge or split an episode, so this column is an array
        where every other tier carries a single id."""
        migrate_up(db)
        insert(db, "memory_episodic", ROWS["memory_episodic"])

        assert read(db, "memory_episodic", "ep-1")["superseded_by"] == ["ep-2", "ep-3"]

    @pytest.mark.parametrize("table_type", sorted(ROWS))
    def test_one_id_can_exist_under_two_tenants(self, db, table_type):
        """tenant_id is half the primary key, so identity is per-tenant. Without it the second
        write would collide with the first tenant's row."""
        migrate_up(db)
        row = ROWS[table_type]
        insert(db, table_type, row)
        insert(db, table_type, dataclasses_replace(row, tenant_id=OTHER_TENANT))

        assert read(db, table_type, row.id, tenant=TENANT)["tenant_id"] == TENANT
        assert read(db, table_type, row.id, tenant=OTHER_TENANT)["tenant_id"] == OTHER_TENANT

    @pytest.mark.parametrize("table_type", MEMORY_TIER_TABLE_TYPES)
    def test_down_drops_the_tier_table(self, db, table_type):
        migrate_up(db)
        assert f"egpt_{table_type}" in table_names(db)

        migrate_down(db)

        assert f"egpt_{table_type}" not in table_names(db)

    def test_up_down_up_leaves_the_same_schema(self, db):
        migrate_up(db)
        before = {t: columns(db, f"egpt_{t}") for t in MEMORY_TIER_TABLE_TYPES}
        indexes_before = {t: index_names(db, f"egpt_{t}") for t in MEMORY_TIER_TABLE_TYPES}

        migrate_down(db)
        migrate_up(db)

        assert {t: columns(db, f"egpt_{t}") for t in MEMORY_TIER_TABLE_TYPES} == before
        assert {t: index_names(db, f"egpt_{t}") for t in MEMORY_TIER_TABLE_TYPES} == indexes_before

    def test_down_then_up_works_in_one_process(self, db):
        """Re-applying after a revert must recreate the tables, not raise.

        Dropping the table is only half a revert: the backend's SQLAlchemy ``MetaData``
        outlives it, so rebuilding the same ``Table`` raises "already defined for this MetaData
        instance". Nothing about the on-disk state shows this -- only re-applying in the same
        process does, which is what a rollback-then-retry actually is.
        """
        migrate_up(db)
        migrate_down(db)

        migrate_up(db)

        assert len([name for name in table_names(db) if name.startswith("egpt_memory_")]) == len(
            MEMORY_TIER_TABLE_TYPES
        )

    def test_a_second_up_is_a_no_op_and_keeps_the_data(self, db):
        migrate_up(db)
        insert(db, "memory_entity", ROWS["memory_entity"])

        migrate_up(db)

        assert read(db, "memory_entity", "e1") is not None, "a re-run must not recreate the table"

    @pytest.mark.parametrize("table_type", MEMORY_TIER_TABLE_TYPES)
    def test_a_second_up_reports_that_it_did_nothing(self, db, table_type):
        """Asserted on the return value, not just on the surviving data: the builder is itself
        idempotent, so a migration that re-enters it leaves the data intact while still telling
        the runner it applied something -- and the runner stamps a version off that answer.
        """
        migrate_up(db)

        assert up(db, table_type, getattr(db, memory_tier_table_attr(table_type))) is False


# --------------------------------------------------------------------------- #
# The memories table's tenant column
# --------------------------------------------------------------------------- #


class TestMemoriesTenantColumn:
    def test_up_adds_the_column_and_its_index(self, db):
        name = make_pre_2_8_memories_table(db)
        assert "tenant_id" not in columns(db, name)

        migrate_up(db)

        assert "tenant_id" in columns(db, name)
        assert _index_name(name) in index_names(db, name)

    def test_down_removes_both(self, db):
        name = make_pre_2_8_memories_table(db)
        migrate_up(db)

        migrate_down(db)

        assert "tenant_id" not in columns(db, name)
        assert _index_name(name) not in index_names(db, name)

    def test_existing_rows_survive_in_both_directions(self, db):
        """Nullable and un-backfilled is what makes this reversible: a pre-multi-tenancy row
        must come back unchanged after up and after down."""
        name = make_pre_2_8_memories_table(db)
        with db.Session() as sess, sess.begin():
            sess.execute(
                text(f"INSERT INTO {name} (memory_id, memory, created_at) VALUES ('m1', '\"hello\"', 1700000000)")
            )

        migrate_up(db)
        with db.Session() as sess:
            after_up = sess.execute(text(f"SELECT memory_id, tenant_id FROM {name}")).fetchone()
        assert after_up[0] == "m1"
        assert after_up[1] is None, "an existing row means 'unscoped', not a made-up tenant"

        migrate_down(db)
        with db.Session() as sess:
            assert sess.execute(text(f"SELECT memory_id FROM {name}")).fetchone()[0] == "m1"

    def test_the_unmigrated_table_hard_fails_until_the_migration_runs(self, tmp_path):
        """Pins the deploy-order trap the module docstring warns about.

        Shipping this code against an existing memories table without running the migration is
        not a degraded read -- ``_get_or_create_table`` validates the live table against the
        schema definition and raises, so every ``MemoryManager`` call dies. Only an upgrade hits
        it; a fresh install builds the column. If a future change makes the validator tolerant,
        this test fails and the warning should come out with it.
        """
        path = str(tmp_path / "pre_2_8.db")
        make_pre_2_8_memories_table(SqliteDb(db_file=path))

        deployed_without_migrating = SqliteDb(db_file=path)

        with pytest.raises(ValueError, match="invalid schema"):
            deployed_without_migrating._get_table(table_type="memories", create_table_if_not_found=True)

    def test_running_the_migration_first_clears_the_failure(self, tmp_path):
        """The other half: the documented remedy actually works, so the warning is actionable
        rather than just alarming."""
        path = str(tmp_path / "pre_2_8.db")
        make_pre_2_8_memories_table(SqliteDb(db_file=path))

        migrating = SqliteDb(db_file=path)
        migrate_up(migrating)

        assert SqliteDb(db_file=path)._get_table(table_type="memories", create_table_if_not_found=True) is not None

    def test_a_freshly_built_table_already_matches_the_migrated_one(self, db):
        """The builder and the migration must agree, or a fresh install and an upgraded one
        drift apart on the same version."""
        migrated = make_pre_2_8_memories_table(db)
        migrate_up(db)

        fresh = SqliteDb(db_file=os.path.join(tempfile.mkdtemp(), "fresh.db"))
        fresh._get_table(table_type="memories", create_table_if_not_found=True)

        assert columns(db, migrated) == columns(fresh, fresh.memory_table_name)
        assert index_names(db, migrated) == index_names(fresh, fresh.memory_table_name)


# --------------------------------------------------------------------------- #
# Database-level invariants
# --------------------------------------------------------------------------- #

BI_TEMPORAL_TABLES = ["memory_episodic", "memory_semantic_fact", "memory_relation", "memory_link"]


class TestCheckConstraints:
    """SM-R12 enforces the bi-temporal ordering in the database, not only in the row models.

    Every insert here goes through raw ``Table.insert()``, deliberately bypassing
    ``egpt.db.schemas.memory`` -- that is the path a bulk loader or a raw-SQL writer takes, and
    the reason a Python-only guard is not enough.
    """

    @pytest.mark.parametrize("table_type", BI_TEMPORAL_TABLES)
    def test_an_inverted_valid_window_is_refused_by_the_database(self, db, table_type):
        migrate_up(db)

        with pytest.raises(IntegrityError):
            _raw_insert(db, table_type, created_at=100, t_valid=200, t_invalid=100)

    @pytest.mark.parametrize("table_type", BI_TEMPORAL_TABLES)
    def test_expiry_before_creation_is_refused_by_the_database(self, db, table_type):
        migrate_up(db)

        with pytest.raises(IntegrityError):
            _raw_insert(db, table_type, created_at=200, expired_at=100)

    @pytest.mark.parametrize("table_type", BI_TEMPORAL_TABLES)
    def test_an_open_ended_window_is_accepted(self, db, table_type):
        """The NULL guards are what let a still-true, still-held row through -- the common case.
        A constraint that rejected these would be worse than none."""
        migrate_up(db)

        _raw_insert(db, table_type, created_at=100)

    @pytest.mark.parametrize("table_type", ["memory_entity", "memory_procedural"])
    def test_non_temporal_tiers_carry_no_temporal_constraints(self, db, table_type):
        """SM-R12 puts the CHECKs on the four bi-temporal tables only; entity and procedural have
        no window to constrain."""
        migrate_up(db)

        assert "CHECK" not in _table_ddl(db, f"egpt_{table_type}")


class TestPartialIndexes:
    @pytest.mark.parametrize("table_type", ["memory_semantic_fact", "memory_relation"])
    def test_the_temporal_index_is_scoped_to_current_rows(self, db, table_type):
        """SM-R12 scopes these with ``WHERE expired_at IS NULL`` so a current-belief lookup does
        not walk superseded rows."""
        migrate_up(db)

        temporal = _index_sql(db, f"egpt_{table_type}", "t_valid")

        assert temporal, f"no temporal index on {table_type}"
        assert "expired_at IS NULL" in temporal

    def test_the_episodic_temporal_index_is_deliberately_unfiltered(self, db):
        """Episodic's index is unfiltered in SM-R12's DDL -- decay archival reads expired rows,
        so scoping it would hide exactly what that reader wants."""
        migrate_up(db)

        assert "WHERE" not in (_index_sql(db, "egpt_memory_episodic", "t_valid") or "")

    def test_the_predicate_is_found_even_when_the_index_name_is_hashed(self, tmp_path):
        """At a long table prefix ``derive_index_name`` truncates and hashes, so the column no
        longer appears in the index name. A name-based lookup returns None here and every
        assertion in this class goes vacuous -- which is why the helper matches the SQL body."""
        long_prefix = SqliteDb(db_file=str(tmp_path / "long.db"), table_prefix="a" * 31 + "_")
        long_prefix._create_all_tables()
        table = long_prefix.memory_semantic_fact_table_name

        assert "t_valid" not in "".join(index_names(long_prefix, table)), "prefix is not long enough to hash"
        assert "expired_at IS NULL" in (_index_sql(long_prefix, table, "t_valid") or "")


def _raw_insert(database, table_type, **temporal):
    """Insert the minimal legal row plus the temporal values under test, via raw SQL."""
    table = database._get_table(table_type=table_type, create_table_if_not_found=False)
    values = {**_minimal_row(table_type), **temporal}
    values["id"] = f"raw-{'-'.join(str(v) for v in temporal.values())}"
    with database.Session() as sess, sess.begin():
        sess.execute(table.insert().values(**values))


def _table_ddl(database, table_name):
    with database.Session() as sess:
        return sess.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"), {"n": table_name}
        ).scalar()


def _index_sql(database, table_name, column):
    """The CREATE INDEX statement covering ``column``, matched on the SQL body.

    Deliberately not matched on the index *name*: ``derive_index_name`` hashes names past 63
    bytes, so at a long table prefix the column string is not in the name and a name-based lookup
    quietly returns None -- which turns a "no WHERE clause" assertion into a vacuous pass.
    """
    with database.Session() as sess:
        rows = sess.execute(
            text("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=:n"), {"n": table_name}
        ).fetchall()
    return next((sql for _, sql in rows if sql and column in sql), None)


# --------------------------------------------------------------------------- #
# Auto-provisioning
# --------------------------------------------------------------------------- #


class TestAutoProvisioning:
    """``AgentOS`` calls ``_create_all_tables`` on startup when ``auto_provision_dbs`` is on (the
    default), and nothing runs migrations automatically -- the only call sites are the explicit
    ``/databases/all/migrate`` endpoints. A tier missing from that list is therefore a tier a
    fresh deployment never gets, migration or no migration.
    """

    def test_a_fresh_deployment_gets_every_tier_table(self, db):
        db._create_all_tables()

        created = table_names(db)
        assert [t for t in MEMORY_TIER_TABLE_TYPES if f"egpt_{t}" not in created] == []

    def test_auto_provisioned_tables_match_the_migrated_ones(self, db, tmp_path):
        """Provisioning and migrating must converge, or a fresh install and an upgraded one
        disagree on the same version."""
        db._create_all_tables()
        migrated = SqliteDb(db_file=str(tmp_path / "migrated.db"))
        migrate_up(migrated)

        for table_type in MEMORY_TIER_TABLE_TYPES:
            name = f"egpt_{table_type}"
            assert columns(db, name) == columns(migrated, name), table_type
            assert index_names(db, name) == index_names(migrated, name), table_type
            # The CHECK constraints live in the table DDL, so comparing columns alone would let
            # one path ship them and the other not.
            assert _table_ddl(db, name) == _table_ddl(migrated, name), table_type

    async def test_the_async_backend_provisions_them_too(self, async_db):
        await async_db._create_all_tables()

        created = {row[0] for row in await _fetch(async_db, "SELECT name FROM sqlite_master WHERE type='table'")}

        assert [t for t in MEMORY_TIER_TABLE_TYPES if f"egpt_{t}" not in created] == []

    @pytest.fixture
    def async_db(self, tmp_path):
        pytest.importorskip("aiosqlite", reason="AsyncSqliteDb resolves the dialect in __init__")
        from egpt.integrations.db.sqlite.async_sqlite import AsyncSqliteDb

        return AsyncSqliteDb(db_file=str(tmp_path / "async_provision.db"))


# --------------------------------------------------------------------------- #
# Sync / async parity
# --------------------------------------------------------------------------- #


class TestSyncAsyncParity:
    """Compared by the durable state each half leaves behind, not by the calls it made."""

    @pytest.fixture
    def async_db(self, tmp_path):
        pytest.importorskip("aiosqlite", reason="AsyncSqliteDb resolves the dialect in __init__")
        from egpt.integrations.db.sqlite.async_sqlite import AsyncSqliteDb

        return AsyncSqliteDb(db_file=str(tmp_path / "async_memory.db"))

    async def test_the_async_half_creates_the_same_tables(self, db, async_db):
        await MigrationManager(db).up()
        await MigrationManager(async_db).up()

        sync_tables = {t for t in table_names(db) if "memory" in t}
        async_tables = {
            row[0]
            for row in (await _fetch(async_db, "SELECT name FROM sqlite_master WHERE type='table'"))
            if "memory" in row[0]
        }

        assert async_tables == sync_tables

    @pytest.mark.parametrize("table_type", MEMORY_TIER_TABLE_TYPES)
    async def test_the_async_half_creates_the_same_columns(self, db, async_db, table_type):
        await MigrationManager(db).up()
        await MigrationManager(async_db).up()

        async_columns = {row[1] for row in await _fetch(async_db, f"PRAGMA table_info(egpt_{table_type})")}

        assert async_columns == columns(db, f"egpt_{table_type}")

    @pytest.mark.parametrize("table_type", MEMORY_TIER_TABLE_TYPES)
    async def test_the_async_half_creates_the_same_primary_key(self, db, async_db, table_type):
        """Compared separately from the column set because a table can carry every column and
        still have no primary key: the async builders had to learn ``__primary_key__`` for these
        tables, and losing it again is silent -- same columns, no identity, duplicate rows.
        """
        await MigrationManager(db).up()
        await MigrationManager(async_db).up()

        async_pk = _primary_key(await _fetch(async_db, f"PRAGMA table_info(egpt_{table_type})"))
        sync_pk = _primary_key(_table_info(db, f"egpt_{table_type}"))

        assert async_pk == sync_pk == ["tenant_id", "id"]

    @pytest.mark.parametrize("table_type", MEMORY_TIER_TABLE_TYPES)
    async def test_the_async_half_agrees_on_nullability(self, db, async_db, table_type):
        await MigrationManager(db).up()
        await MigrationManager(async_db).up()

        async_notnull = {row[1]: row[3] for row in await _fetch(async_db, f"PRAGMA table_info(egpt_{table_type})")}
        sync_notnull = {row[1]: row[3] for row in _table_info(db, f"egpt_{table_type}")}

        assert async_notnull == sync_notnull

    @pytest.mark.parametrize("table_type", MEMORY_TIER_TABLE_TYPES)
    async def test_a_partial_insert_behaves_the_same_on_both_halves(self, db, async_db, table_type):
        """Column *defaults*, not just column presence.

        Nullability parity passes even when one builder drops ``default=``: the column is NOT
        NULL on both, and only an insert that omits it tells them apart. Every tier carries at
        least ``metadata`` as NOT NULL with a default, so a writer that omits it -- which
        aicoe-tracker#175's write path may well do -- would work sync and raise async.
        """
        await MigrationManager(db).up()
        await MigrationManager(async_db).up()
        minimal = _minimal_row(table_type)

        sync_table = db._get_table(table_type=table_type, create_table_if_not_found=False)
        with db.Session() as sess, sess.begin():
            sess.execute(sync_table.insert().values(**minimal))

        async_table = await async_db._get_table(table_type=table_type, create_table_if_not_found=False)
        async with async_db.async_session_factory() as sess, sess.begin():
            await sess.execute(async_table.insert().values(**minimal))

    async def test_the_async_half_also_survives_down_then_up(self, async_db):
        """The async twin of the MetaData cleanup. Asserted separately because "an async half
        exists" is not the same as "it makes the same decisions": the drop and the forget are
        two statements, and only one of them is SQL."""
        await MigrationManager(async_db).up()
        await MigrationManager(async_db).down(target_version=PREVIOUS_VERSION)

        await MigrationManager(async_db).up()

        remaining = {row[0] for row in await _fetch(async_db, "SELECT name FROM sqlite_master WHERE type='table'")}

        assert len([name for name in remaining if name.startswith("egpt_memory_")]) == len(MEMORY_TIER_TABLE_TYPES)

    async def test_the_async_revert_leaves_the_same_state(self, db, async_db):
        await MigrationManager(db).up()
        await MigrationManager(db).down(target_version=PREVIOUS_VERSION)
        await MigrationManager(async_db).up()
        await MigrationManager(async_db).down(target_version=PREVIOUS_VERSION)

        remaining = {row[0] for row in await _fetch(async_db, "SELECT name FROM sqlite_master WHERE type='table'")}

        assert not [name for name in remaining if name.startswith("egpt_memory_")]
        assert not [name for name in table_names(db) if name.startswith("egpt_memory_")]


async def _fetch(async_db, sql):
    async with async_db.async_session_factory() as sess:
        return (await sess.execute(text(sql))).fetchall()


def _minimal_row(table_type):
    """The smallest legal insert: every NOT NULL column that has no default, and nothing else.

    Derived from the schema definition rather than hand-listed, so a new NOT NULL column joins
    the fixture automatically instead of quietly widening what "partial" means.
    """
    from sqlalchemy.types import BigInteger, Integer

    from egpt.integrations.db.sqlite.schemas import get_table_schema_definition

    schema = get_table_schema_definition(table_type)
    required = {
        name: config
        for name, config in schema.items()
        if not name.startswith("__") and config.get("nullable") is False and "default" not in config
    }
    values = {}
    for name, config in required.items():
        values[name] = 1_700_000_000 if config["type"] in (BigInteger, Integer) else f"v-{name}"
    values["from_tier"] = values.get("from_tier") and "episodic"
    values["to_tier"] = values.get("to_tier") and "semantic"
    return {name: value for name, value in values.items() if value is not None}


def _table_info(database, table_name):
    with database.Session() as sess:
        return sess.execute(text(f"PRAGMA table_info({table_name})")).fetchall()


def _primary_key(table_info_rows):
    """Column names in primary-key order. ``PRAGMA table_info`` reports the 1-based position in
    the key as the last field, and 0 for a column outside it."""
    return [row[1] for row in sorted((row for row in table_info_rows if row[5]), key=lambda row: row[5])]


def dataclasses_replace(row, **changes):
    import dataclasses

    return dataclasses.replace(row, **changes)
