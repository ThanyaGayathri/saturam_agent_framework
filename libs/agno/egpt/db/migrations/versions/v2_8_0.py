"""Migration v2.8.0: memory tier tables and a tenant column on memories (SM-C3 #91)

Changes:
- Create the six SM-R12 memory tier tables (episodic, semantic fact, entity, relation,
  procedural, link).
- Add a nullable ``tenant_id`` column and an ``idx_{table}_tenant_id`` index to the memories
  table.

The tier tables are created through the backend's own table builder rather than DDL written
here, so ``integrations/db/{postgres,sqlite}/schemas.py`` stays the single definition and a
migrated database cannot drift from a freshly created one. ``down`` drops them, which is a
clean revert because the tables are new in this version.

``tenant_id`` is nullable and is not backfilled: rows written before multi-tenancy mean
"unscoped". Nothing reads it yet -- ``MemoryManager`` still partitions on ``user_id`` alone,
which is why ``SeedMemoryProvider`` keeps refusing multi-tenant deployments. The read/write
path is aicoe-tracker#175.

**Postgres and SQLite only.** MySQL, SingleStore and DynamoDB define a memories table but get
neither the column nor the tier tables, so on those backends the column does not exist at all
rather than existing on fresh installs with no upgrade path. Their memory schemas are
deliberately unchanged; #175 must either extend them or refuse them.

.. warning::
   Deploy order matters, and the blast radius is larger than v2.7.0's. ``_get_or_create_table``
   validates a live table against the schema definition and **raises** when a declared column is
   missing, so a deployment that ships this code before running ``MigrationManager(db).up()``
   fails every read and write against an existing ``memories`` table -- which is every
   ``MemoryManager`` call, not one auth path. Only upgrades are affected; a fresh install builds
   the column. Nothing migrates on startup: the only call sites are the explicit
   ``/databases/all/migrate`` endpoints. Run the migration first, or in the same maintenance
   step.

**Partial runs are safe to retry.** Each table is migrated independently and stamped
independently, and every step is guarded by an existence check, so a process killed after three
tier tables exist resumes from there rather than starting over or double-creating. The tenant
column and the tier tables are independent of each other, so a database caught between the two
is queryable either way.

Every decision about *what* SQL to run lives in the ``_plan_*`` functions below, which are pure
and shared by the sync and async halves. Only the I/O differs between the two, so they cannot
diverge on a decision -- the failure mode where one half persists different state than the
other.
"""

from typing import Any, List, Optional, Tuple

from egpt.db.base import AsyncBaseDb, BaseDb
from egpt.db.migrations.utils import quote_db_identifier
from egpt.db.table_names import MEMORY_TIER_TABLE_TYPES
from egpt.utils.log import log_error, log_info

try:
    from sqlalchemy import text
except ImportError:
    raise ImportError("`sqlalchemy` not installed. Please install it using `pip install sqlalchemy`")


MEMORY_TABLE_TYPE = "memories"

_POSTGRES_DB_TYPES = ("PostgresDb", "AsyncPostgresDb")
_SQLITE_DB_TYPES = ("SqliteDb", "AsyncSqliteDb")

# SQLite gained ALTER TABLE ... DROP COLUMN in 3.35.0.
_SQLITE_DROP_COLUMN_MIN_VERSION = (3, 35, 0)

_PG_TABLE_EXISTS = text(
    "SELECT EXISTS (  SELECT FROM information_schema.tables  WHERE table_schema = :schema AND table_name = :table_name)"
)

_PG_COLUMN_EXISTS = text(
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_schema = :schema AND table_name = :table AND column_name = :column"
)

_PG_INDEX_EXISTS = text(
    "SELECT 1 FROM pg_indexes WHERE schemaname = :schema AND tablename = :table AND indexname = :index"
)

_SQLITE_TABLE_EXISTS = text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:table_name")


def _index_name(table_name: str) -> str:
    """Mirror the ``idx_{table_name}_{column}`` convention the table builders derive, so a
    migrated table and a freshly created one end up with the same index."""
    return f"idx_{table_name}_tenant_id"


def _forget_table(db: Any, table_name: str) -> None:
    """Drop a dropped table out of the backend's SQLAlchemy ``MetaData``.

    The DDL is only half of a revert. ``MetaData`` outlives the table, so re-applying the
    migration in the same process rebuilds the same ``Table`` object and SQLAlchemy raises
    "already defined for this MetaData instance" -- a down followed by an up dies rather than
    recreating. The migration dropped the table, so it owns forgetting it.
    """
    metadata = getattr(db, "metadata", None)
    if metadata is None:
        return
    for table in [t for t in metadata.tables.values() if t.name == table_name]:
        metadata.remove(table)


def _sqlite_supports_drop_column(sqlite_version: str) -> bool:
    try:
        parts = tuple(int(part) for part in sqlite_version.split(".")[:3])
    except ValueError:
        return False
    return parts >= _SQLITE_DROP_COLUMN_MIN_VERSION


def _qualified_table(db_type: str, db_schema: Optional[str], table_name: str) -> str:
    quoted_table = quote_db_identifier(db_type, table_name)
    if db_type in _POSTGRES_DB_TYPES:
        return f"{quote_db_identifier(db_type, db_schema or 'public')}.{quoted_table}"
    return quoted_table


# ---------------------------------------------------------------------------
# Planners -- pure, and the only place a decision is made
# ---------------------------------------------------------------------------


def _plan_tenant_up(db_type: str, full_table: str, index_name: str, has_column: bool, has_index: bool) -> List[str]:
    """SQL to add the tenant column and its index. Empty when both already exist."""
    statements: List[str] = []
    if not has_column:
        column_type = "VARCHAR" if db_type in _POSTGRES_DB_TYPES else "TEXT"
        statements.append(f"ALTER TABLE {full_table} ADD COLUMN tenant_id {column_type}")
    if not has_index:
        statements.append(f"CREATE INDEX {quote_db_identifier(db_type, index_name)} ON {full_table} (tenant_id)")
    return statements


def _plan_tenant_down(
    db_type: str,
    full_table: str,
    index_name: str,
    has_column: bool,
    has_index: bool,
    can_drop_column: bool,
    db_schema: Optional[str] = None,
) -> List[str]:
    """SQL to revert. The index goes first: both engines refuse to drop a column an index
    still references."""
    statements: List[str] = []
    if has_index:
        quoted_index = quote_db_identifier(db_type, index_name)
        if db_type in _POSTGRES_DB_TYPES:
            quoted_index = f"{quote_db_identifier(db_type, db_schema or 'public')}.{quoted_index}"
        statements.append(f"DROP INDEX {quoted_index}")
    if has_column and can_drop_column:
        statements.append(f"ALTER TABLE {full_table} DROP COLUMN tenant_id")
    return statements


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def _pg_params(db: Any, table_name: str) -> Tuple[str, dict, dict, dict]:
    db_schema = db.db_schema or "public"
    return (
        db_schema,
        {"schema": db_schema, "table_name": table_name},
        {"schema": db_schema, "table": table_name, "column": "tenant_id"},
        {"schema": db_schema, "table": table_name, "index": _index_name(table_name)},
    )


def _sqlite_state(rows_columns: Any, rows_indexes: Any) -> Tuple[set, set]:
    return ({col[1] for col in rows_columns}, {idx[1] for idx in rows_indexes})


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def _run(sess: Any, statements: List[str], table_name: str) -> bool:
    for statement in statements:
        log_info(f"-- {statement} (on {table_name})")
        sess.execute(text(statement))
    return bool(statements)


def _tenant_column_sync(db: BaseDb, db_type: str, table_name: str, revert: bool) -> bool:
    if db_type in _POSTGRES_DB_TYPES:
        db_schema, table_params, column_params, index_params = _pg_params(db, table_name)
        with db.Session() as sess, sess.begin():  # type: ignore[attr-defined]
            if not sess.execute(_PG_TABLE_EXISTS, table_params).scalar():
                log_info(f"Table {table_name} does not exist, skipping")
                return False
            has_column = sess.execute(_PG_COLUMN_EXISTS, column_params).scalar() is not None
            has_index = sess.execute(_PG_INDEX_EXISTS, index_params).scalar() is not None
            full_table = _qualified_table(db_type, db_schema, table_name)
            statements = (
                _plan_tenant_down(db_type, full_table, _index_name(table_name), has_column, has_index, True, db_schema)
                if revert
                else _plan_tenant_up(db_type, full_table, _index_name(table_name), has_column, has_index)
            )
            return _run(sess, statements, table_name)

    with db.Session() as sess, sess.begin():  # type: ignore[attr-defined]
        if not sess.execute(_SQLITE_TABLE_EXISTS, {"table_name": table_name}).scalar():
            log_info(f"Table {table_name} does not exist, skipping")
            return False
        columns, indexes = _sqlite_state(
            sess.execute(text(f"PRAGMA table_info({table_name})")).fetchall(),
            sess.execute(text(f"PRAGMA index_list({table_name})")).fetchall(),
        )
        has_column = "tenant_id" in columns
        has_index = _index_name(table_name) in indexes
        full_table = _qualified_table(db_type, None, table_name)
        if revert:
            version = str(sess.execute(text("SELECT sqlite_version()")).scalar() or "")
            can_drop = _sqlite_supports_drop_column(version)
            if has_column and not can_drop:
                log_info(f"SQLite {version} does not support DROP COLUMN (needs 3.35+); leaving tenant_id")
            statements = _plan_tenant_down(
                db_type, full_table, _index_name(table_name), has_column, has_index, can_drop
            )
        else:
            statements = _plan_tenant_up(db_type, full_table, _index_name(table_name), has_column, has_index)
        return _run(sess, statements, table_name)


def _tier_table_sync(db: BaseDb, db_type: str, table_type: str, table_name: str, revert: bool) -> bool:
    if revert:
        return _drop_tier_table_sync(db, db_type, table_name)
    if _table_exists_sync(db, db_type, table_name):
        log_info(f"Table {table_name} already exists, skipping creation")
        return False
    log_info(f"-- Creating memory tier table {table_name}")
    # The builder re-checks and creates with checkfirst, so a table appearing between the probe
    # above and this call is absorbed rather than raced.
    db._get_table(table_type=table_type, create_table_if_not_found=True)  # type: ignore[attr-defined]
    return True


def _drop_tier_table_sync(db: BaseDb, db_type: str, table_name: str) -> bool:
    """Check and drop inside one transaction.

    Probing in one session and dropping in another leaves a window where a concurrent DDL turns
    "already gone" into a raised error mid-migration, with some tables stamped and others not.
    ``IF EXISTS`` covers the same ground a second time, because SQLite takes no DDL lock that
    would make the transaction alone sufficient.
    """
    full_table = _qualified_table(db_type, getattr(db, "db_schema", None), table_name)
    with db.Session() as sess, sess.begin():  # type: ignore[attr-defined]
        if not _exists_in_session(sess, db_type, db, table_name):
            return False
        _run(sess, [f"DROP TABLE IF EXISTS {full_table}"], table_name)
    _forget_table(db, table_name)
    return True


def _exists_in_session(sess: Any, db_type: str, db: Any, table_name: str) -> bool:
    """The existence predicate, run on a caller-supplied session so it can share a transaction."""
    if db_type in _POSTGRES_DB_TYPES:
        _, table_params, _, _ = _pg_params(db, table_name)
        return bool(sess.execute(_PG_TABLE_EXISTS, table_params).scalar())
    return bool(sess.execute(_SQLITE_TABLE_EXISTS, {"table_name": table_name}).scalar())


def _table_exists_sync(db: BaseDb, db_type: str, table_name: str) -> bool:
    with db.Session() as sess:  # type: ignore[attr-defined]
        return _exists_in_session(sess, db_type, db, table_name)


# ---------------------------------------------------------------------------
# Async
# ---------------------------------------------------------------------------


async def _run_async(sess: Any, statements: List[str], table_name: str) -> bool:
    for statement in statements:
        log_info(f"-- {statement} (on {table_name})")
        await sess.execute(text(statement))
    return bool(statements)


async def _tenant_column_async(db: AsyncBaseDb, db_type: str, table_name: str, revert: bool) -> bool:
    if db_type in _POSTGRES_DB_TYPES:
        db_schema, table_params, column_params, index_params = _pg_params(db, table_name)
        async with db.async_session_factory() as sess, sess.begin():  # type: ignore[attr-defined]
            result = await sess.execute(_PG_TABLE_EXISTS, table_params)
            if not result.scalar():
                log_info(f"Table {table_name} does not exist, skipping")
                return False
            has_column = (await sess.execute(_PG_COLUMN_EXISTS, column_params)).scalar() is not None
            has_index = (await sess.execute(_PG_INDEX_EXISTS, index_params)).scalar() is not None
            full_table = _qualified_table(db_type, db_schema, table_name)
            statements = (
                _plan_tenant_down(db_type, full_table, _index_name(table_name), has_column, has_index, True, db_schema)
                if revert
                else _plan_tenant_up(db_type, full_table, _index_name(table_name), has_column, has_index)
            )
            return await _run_async(sess, statements, table_name)

    async with db.async_session_factory() as sess, sess.begin():  # type: ignore[attr-defined]
        result = await sess.execute(_SQLITE_TABLE_EXISTS, {"table_name": table_name})
        if not result.scalar():
            log_info(f"Table {table_name} does not exist, skipping")
            return False
        columns, indexes = _sqlite_state(
            (await sess.execute(text(f"PRAGMA table_info({table_name})"))).fetchall(),
            (await sess.execute(text(f"PRAGMA index_list({table_name})"))).fetchall(),
        )
        has_column = "tenant_id" in columns
        has_index = _index_name(table_name) in indexes
        full_table = _qualified_table(db_type, None, table_name)
        if revert:
            version = str((await sess.execute(text("SELECT sqlite_version()"))).scalar() or "")
            can_drop = _sqlite_supports_drop_column(version)
            if has_column and not can_drop:
                log_info(f"SQLite {version} does not support DROP COLUMN (needs 3.35+); leaving tenant_id")
            statements = _plan_tenant_down(
                db_type, full_table, _index_name(table_name), has_column, has_index, can_drop
            )
        else:
            statements = _plan_tenant_up(db_type, full_table, _index_name(table_name), has_column, has_index)
        return await _run_async(sess, statements, table_name)


async def _exists_in_session_async(sess: Any, db_type: str, db: Any, table_name: str) -> bool:
    """The async twin of :func:`_exists_in_session` -- same predicate, same sharing."""
    if db_type in _POSTGRES_DB_TYPES:
        _, table_params, _, _ = _pg_params(db, table_name)
        return bool((await sess.execute(_PG_TABLE_EXISTS, table_params)).scalar())
    return bool((await sess.execute(_SQLITE_TABLE_EXISTS, {"table_name": table_name})).scalar())


async def _table_exists_async(db: AsyncBaseDb, db_type: str, table_name: str) -> bool:
    async with db.async_session_factory() as sess:  # type: ignore[attr-defined]
        return await _exists_in_session_async(sess, db_type, db, table_name)


async def _drop_tier_table_async(db: AsyncBaseDb, db_type: str, table_name: str) -> bool:
    """The async twin of :func:`_drop_tier_table_sync`, down to the single transaction."""
    full_table = _qualified_table(db_type, getattr(db, "db_schema", None), table_name)
    async with db.async_session_factory() as sess, sess.begin():  # type: ignore[attr-defined]
        if not await _exists_in_session_async(sess, db_type, db, table_name):
            return False
        await _run_async(sess, [f"DROP TABLE IF EXISTS {full_table}"], table_name)
    _forget_table(db, table_name)
    return True


async def _tier_table_async(db: AsyncBaseDb, db_type: str, table_type: str, table_name: str, revert: bool) -> bool:
    if revert:
        return await _drop_tier_table_async(db, db_type, table_name)
    if await _table_exists_async(db, db_type, table_name):
        log_info(f"Table {table_name} already exists, skipping creation")
        return False
    log_info(f"-- Creating memory tier table {table_name}")
    await db._get_table(table_type=table_type, create_table_if_not_found=True)  # type: ignore[attr-defined]
    return True


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _supported(db_type: str) -> bool:
    return db_type in _POSTGRES_DB_TYPES or db_type in _SQLITE_DB_TYPES


def up(db: BaseDb, table_type: str, table_name: str) -> bool:
    """Create a memory tier table, or add tenant_id to the memories table."""
    db_type = type(db).__name__
    try:
        if not _supported(db_type):
            log_info(f"{db_type} does not require schema migrations")
            return False
        if table_type == MEMORY_TABLE_TYPE:
            return _tenant_column_sync(db, db_type, table_name, revert=False)
        if table_type in MEMORY_TIER_TABLE_TYPES:
            return _tier_table_sync(db, db_type, table_type, table_name, revert=False)
        return False
    except Exception as e:
        log_error(f"Error running migration v2.8.0 for {db_type} on table {table_name}: {str(e)}")
        raise


async def async_up(db: AsyncBaseDb, table_type: str, table_name: str) -> bool:
    """Create a memory tier table, or add tenant_id to the memories table."""
    db_type = type(db).__name__
    try:
        if not _supported(db_type):
            log_info(f"{db_type} does not require schema migrations")
            return False
        if table_type == MEMORY_TABLE_TYPE:
            return await _tenant_column_async(db, db_type, table_name, revert=False)
        if table_type in MEMORY_TIER_TABLE_TYPES:
            return await _tier_table_async(db, db_type, table_type, table_name, revert=False)
        return False
    except Exception as e:
        log_error(f"Error running migration v2.8.0 for {db_type} on table {table_name}: {str(e)}")
        raise


def down(db: BaseDb, table_type: str, table_name: str) -> bool:
    """Drop a memory tier table, or remove tenant_id from the memories table."""
    db_type = type(db).__name__
    try:
        if not _supported(db_type):
            log_info(f"Revert not implemented for {db_type}")
            return False
        if table_type == MEMORY_TABLE_TYPE:
            return _tenant_column_sync(db, db_type, table_name, revert=True)
        if table_type in MEMORY_TIER_TABLE_TYPES:
            return _tier_table_sync(db, db_type, table_type, table_name, revert=True)
        return False
    except Exception as e:
        log_error(f"Error reverting migration v2.8.0 for {db_type} on table {table_name}: {str(e)}")
        raise


async def async_down(db: AsyncBaseDb, table_type: str, table_name: str) -> bool:
    """Drop a memory tier table, or remove tenant_id from the memories table."""
    db_type = type(db).__name__
    try:
        if not _supported(db_type):
            log_info(f"Revert not implemented for {db_type}")
            return False
        if table_type == MEMORY_TABLE_TYPE:
            return await _tenant_column_async(db, db_type, table_name, revert=True)
        if table_type in MEMORY_TIER_TABLE_TYPES:
            return await _tier_table_async(db, db_type, table_type, table_name, revert=True)
        return False
    except Exception as e:
        log_error(f"Error reverting migration v2.8.0 for {db_type} on table {table_name}: {str(e)}")
        raise
