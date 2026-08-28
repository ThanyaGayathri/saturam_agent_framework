"""Migration v2.9.0: Add tenant_id to the sessions table (aicoe-tracker#197)

Changes:
- Add a nullable ``tenant_id`` column to the sessions table.
- Add an ``idx_{table}_tenant_id`` index for tenant-scoped listings.

The column is nullable and is not backfilled: sessions written before this column existed keep a
NULL ``tenant_id``, which means "unscoped" -- reachable with ``tenant_id=None`` on a non-strict
db, and refused (like every other unscoped session read) once ``require_tenant_id`` is turned on.
Nothing about existing session, run, summary or metadata data is touched in either direction,
which is what makes this reversible. See ``egpt/db/session_scope.py`` for why the session
accessors keep ``tenant_id`` optional -- unlike the component tables' migration (``v2_6_0.py``),
this column backs a parameter with a default, not a required one.

.. warning::
   Deploy order matters for this one. ``_get_table`` validates a live table against the schema
   definition and raises when a declared column is missing -- so a deployment that ships this
   code before running ``MigrationManager(db).up()`` will fail every session read/write until the
   migration runs. Run the migration first, or in the same maintenance step.

Index name matches the one the backend table builders derive (``idx_{table}_tenant_id``) so a
freshly created table and a migrated table end up with the same schema. This mirrors
``v2_7_0.py`` (single nullable column + single index), not ``v2_6_0.py`` (composite indexes):
session reads are already dominated by the ``session_id`` primary key and a ``user_id`` filter,
so a plain ``tenant_id`` index is what a tenant-scoped listing needs.
"""

from egpt.db.base import AsyncBaseDb, BaseDb
from egpt.db.migrations.utils import quote_db_identifier
from egpt.utils.log import log_error, log_info

try:
    from sqlalchemy import text
except ImportError:
    raise ImportError("`sqlalchemy` not installed. Please install it using `pip install sqlalchemy`")


SESSION_TABLE_TYPE = "sessions"

# SQLite gained ALTER TABLE ... DROP COLUMN in 3.35.0.
_SQLITE_DROP_COLUMN_MIN_VERSION = (3, 35, 0)


def _index_name(table_name: str) -> str:
    """Derive the index name, mirroring the ``idx_{table_name}_{column}`` convention used by
    the Postgres and SQLite table builders."""
    return f"idx_{table_name}_tenant_id"


def up(db: BaseDb, table_type: str, table_name: str) -> bool:
    """Add tenant_id and its index to the sessions table.

    Returns:
        bool: True if any migration was applied, False otherwise.
    """
    db_type = type(db).__name__

    try:
        if table_type != SESSION_TABLE_TYPE:
            return False

        if db_type == "PostgresDb":
            return _migrate_postgres(db, table_name)
        elif db_type == "SqliteDb":
            return _migrate_sqlite(db, table_name)
        elif db_type in ("MySQLDb", "SingleStoreDb"):
            return _migrate_mysql(db, table_name)
        else:
            log_info(f"{db_type} does not require schema migrations")
        return False
    except Exception as e:
        log_error(f"Error running migration v2.9.0 for {db_type} on table {table_name}: {str(e)}")
        raise


async def async_up(db: AsyncBaseDb, table_type: str, table_name: str) -> bool:
    """Add tenant_id and its index to the sessions table.

    Returns:
        bool: True if any migration was applied, False otherwise.
    """
    db_type = type(db).__name__

    try:
        if table_type != SESSION_TABLE_TYPE:
            return False

        if db_type == "AsyncPostgresDb":
            return await _migrate_async_postgres(db, table_name)
        elif db_type == "AsyncSqliteDb":
            return await _migrate_async_sqlite(db, table_name)
        elif db_type == "AsyncMySQLDb":
            return await _migrate_async_mysql(db, table_name)
        else:
            log_info(f"{db_type} does not require schema migrations")
        return False
    except Exception as e:
        log_error(f"Error running migration v2.9.0 for {db_type} on table {table_name}: {str(e)}")
        raise


def down(db: BaseDb, table_type: str, table_name: str) -> bool:
    """Revert: drop the index and the tenant_id column.

    Returns:
        bool: True if any migration was reverted, False otherwise.
    """
    db_type = type(db).__name__

    try:
        if table_type != SESSION_TABLE_TYPE:
            return False

        if db_type == "PostgresDb":
            return _revert_postgres(db, table_name)
        elif db_type == "SqliteDb":
            return _revert_sqlite(db, table_name)
        elif db_type in ("MySQLDb", "SingleStoreDb"):
            return _revert_mysql(db, table_name)
        else:
            log_info(f"Revert not implemented for {db_type}")
        return False
    except Exception as e:
        log_error(f"Error reverting migration v2.9.0 for {db_type} on table {table_name}: {str(e)}")
        raise


async def async_down(db: AsyncBaseDb, table_type: str, table_name: str) -> bool:
    """Revert: drop the index and the tenant_id column.

    Returns:
        bool: True if any migration was reverted, False otherwise.
    """
    db_type = type(db).__name__

    try:
        if table_type != SESSION_TABLE_TYPE:
            return False

        if db_type == "AsyncPostgresDb":
            return await _revert_async_postgres(db, table_name)
        elif db_type == "AsyncSqliteDb":
            return await _revert_async_sqlite(db, table_name)
        elif db_type == "AsyncMySQLDb":
            return await _revert_async_mysql(db, table_name)
        else:
            log_info(f"Revert not implemented for {db_type}")
        return False
    except Exception as e:
        log_error(f"Error reverting migration v2.9.0 for {db_type} on table {table_name} asynchronously: {str(e)}")
        raise


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


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


def _sqlite_supports_drop_column(sqlite_version: str) -> bool:
    """Return True if the running SQLite build supports ALTER TABLE ... DROP COLUMN."""
    try:
        parts = tuple(int(part) for part in sqlite_version.split(".")[:3])
    except ValueError:
        return False
    return parts >= _SQLITE_DROP_COLUMN_MIN_VERSION


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------


def _migrate_postgres(db: BaseDb, table_name: str) -> bool:
    """Add the tenant_id column and index for PostgreSQL."""
    db_schema = db.db_schema or "public"  # type: ignore
    db_type = type(db).__name__
    quoted_schema = quote_db_identifier(db_type, db_schema)
    quoted_table = quote_db_identifier(db_type, table_name)
    full_table = f"{quoted_schema}.{quoted_table}"
    index_name = _index_name(table_name)

    with db.Session() as sess, sess.begin():  # type: ignore
        table_exists = sess.execute(
            _PG_TABLE_EXISTS,
            {"schema": db_schema, "table_name": table_name},
        ).scalar()

        if not table_exists:
            log_info(f"Table {table_name} does not exist, skipping migration")
            return False

        applied = False

        has_column = sess.execute(
            _PG_COLUMN_EXISTS,
            {"schema": db_schema, "table": table_name, "column": "tenant_id"},
        ).scalar()
        if has_column is None:
            log_info(f"-- Adding tenant_id column to {table_name}")
            sess.execute(text(f"ALTER TABLE {full_table} ADD COLUMN tenant_id VARCHAR"))
            applied = True

        has_index = sess.execute(
            _PG_INDEX_EXISTS,
            {"schema": db_schema, "table": table_name, "index": index_name},
        ).scalar()
        if has_index is None:
            log_info(f"-- Adding index {index_name} on {table_name}")
            sess.execute(text(f"CREATE INDEX {quote_db_identifier(db_type, index_name)} ON {full_table} (tenant_id)"))
            applied = True

        return applied


async def _migrate_async_postgres(db: AsyncBaseDb, table_name: str) -> bool:
    """Add the tenant_id column and index for async PostgreSQL."""
    db_schema = db.db_schema or "public"  # type: ignore
    db_type = type(db).__name__
    quoted_schema = quote_db_identifier(db_type, db_schema)
    quoted_table = quote_db_identifier(db_type, table_name)
    full_table = f"{quoted_schema}.{quoted_table}"
    index_name = _index_name(table_name)

    async with db.async_session_factory() as sess, sess.begin():  # type: ignore
        result = await sess.execute(
            _PG_TABLE_EXISTS,
            {"schema": db_schema, "table_name": table_name},
        )
        if not result.scalar():
            log_info(f"Table {table_name} does not exist, skipping migration")
            return False

        applied = False

        result = await sess.execute(
            _PG_COLUMN_EXISTS,
            {"schema": db_schema, "table": table_name, "column": "tenant_id"},
        )
        if result.scalar() is None:
            log_info(f"-- Adding tenant_id column to {table_name}")
            await sess.execute(text(f"ALTER TABLE {full_table} ADD COLUMN tenant_id VARCHAR"))
            applied = True

        result = await sess.execute(
            _PG_INDEX_EXISTS,
            {"schema": db_schema, "table": table_name, "index": index_name},
        )
        if result.scalar() is None:
            log_info(f"-- Adding index {index_name} on {table_name}")
            await sess.execute(
                text(f"CREATE INDEX {quote_db_identifier(db_type, index_name)} ON {full_table} (tenant_id)")
            )
            applied = True

        return applied


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def _migrate_sqlite(db: BaseDb, table_name: str) -> bool:
    """Add the tenant_id column and index for SQLite."""
    index_name = _index_name(table_name)

    with db.Session() as sess, sess.begin():  # type: ignore
        table_exists = sess.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:table_name"),
            {"table_name": table_name},
        ).scalar()

        if not table_exists:
            log_info(f"Table {table_name} does not exist, skipping migration")
            return False

        applied = False

        columns_info = sess.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        existing_columns = {col[1] for col in columns_info}

        if "tenant_id" not in existing_columns:
            log_info(f"-- Adding tenant_id column to {table_name}")
            sess.execute(text(f"ALTER TABLE {table_name} ADD COLUMN tenant_id TEXT"))
            applied = True
        else:
            log_info(f"Column tenant_id already exists in {table_name}, skipping")

        indexes = sess.execute(text(f"PRAGMA index_list({table_name})")).fetchall()
        index_names = {idx[1] for idx in indexes}

        if index_name not in index_names:
            log_info(f"-- Adding index {index_name} on {table_name}")
            sess.execute(text(f"CREATE INDEX {index_name} ON {table_name} (tenant_id)"))
            applied = True

        return applied


async def _migrate_async_sqlite(db: AsyncBaseDb, table_name: str) -> bool:
    """Add the tenant_id column and index for async SQLite."""
    index_name = _index_name(table_name)

    async with db.async_session_factory() as sess, sess.begin():  # type: ignore
        result = await sess.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:table_name"),
            {"table_name": table_name},
        )
        if not result.scalar():
            log_info(f"Table {table_name} does not exist, skipping migration")
            return False

        applied = False

        result = await sess.execute(text(f"PRAGMA table_info({table_name})"))
        existing_columns = {col[1] for col in result.fetchall()}

        if "tenant_id" not in existing_columns:
            log_info(f"-- Adding tenant_id column to {table_name}")
            await sess.execute(text(f"ALTER TABLE {table_name} ADD COLUMN tenant_id TEXT"))
            applied = True
        else:
            log_info(f"Column tenant_id already exists in {table_name}, skipping")

        result = await sess.execute(text(f"PRAGMA index_list({table_name})"))
        index_names = {idx[1] for idx in result.fetchall()}

        if index_name not in index_names:
            log_info(f"-- Adding index {index_name} on {table_name}")
            await sess.execute(text(f"CREATE INDEX {index_name} ON {table_name} (tenant_id)"))
            applied = True

        return applied


# ---------------------------------------------------------------------------
# MySQL / SingleStore (wire-protocol compatible, share this implementation)
# ---------------------------------------------------------------------------

# `VARCHAR` needs an explicit length in MySQL, unlike Postgres -- matches the length declared for
# this column in `integrations/db/mysql/schemas.py` and `integrations/db/singlestore/schemas.py`,
# so a migrated table and a freshly created one agree exactly.
_MYSQL_TENANT_ID_COLUMN_TYPE = "VARCHAR(128)"

_MYSQL_TABLE_EXISTS = text(
    "SELECT 1 FROM information_schema.tables WHERE table_schema = :schema AND table_name = :table_name"
)

_MYSQL_COLUMN_EXISTS = text(
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_schema = :schema AND table_name = :table AND column_name = :column"
)

_MYSQL_INDEX_EXISTS = text(
    "SELECT 1 FROM information_schema.statistics "
    "WHERE table_schema = :schema AND table_name = :table AND index_name = :index"
)


def _migrate_mysql(db: BaseDb, table_name: str) -> bool:
    """Add the tenant_id column and index for MySQL/SingleStore."""
    db_type = type(db).__name__
    # The database name -- the same attribute MySQLDb/SingleStoreDb share with PostgresDb.
    db_schema = db.db_schema  # type: ignore
    quoted_table = quote_db_identifier(db_type, table_name)
    index_name = _index_name(table_name)

    with db.Session() as sess, sess.begin():  # type: ignore
        table_exists = sess.execute(
            _MYSQL_TABLE_EXISTS,
            {"schema": db_schema, "table_name": table_name},
        ).scalar()

        if not table_exists:
            log_info(f"Table {table_name} does not exist, skipping migration")
            return False

        applied = False

        has_column = sess.execute(
            _MYSQL_COLUMN_EXISTS,
            {"schema": db_schema, "table": table_name, "column": "tenant_id"},
        ).scalar()
        if has_column is None:
            log_info(f"-- Adding tenant_id column to {table_name}")
            sess.execute(text(f"ALTER TABLE {quoted_table} ADD COLUMN tenant_id {_MYSQL_TENANT_ID_COLUMN_TYPE}"))
            applied = True

        has_index = sess.execute(
            _MYSQL_INDEX_EXISTS,
            {"schema": db_schema, "table": table_name, "index": index_name},
        ).scalar()
        if has_index is None:
            log_info(f"-- Adding index {index_name} on {table_name}")
            sess.execute(text(f"CREATE INDEX {quote_db_identifier(db_type, index_name)} ON {quoted_table} (tenant_id)"))
            applied = True

        return applied


async def _migrate_async_mysql(db: AsyncBaseDb, table_name: str) -> bool:
    """Add the tenant_id column and index for async MySQL."""
    db_type = type(db).__name__
    db_schema = db.db_schema  # type: ignore
    quoted_table = quote_db_identifier(db_type, table_name)
    index_name = _index_name(table_name)

    async with db.async_session_factory() as sess, sess.begin():  # type: ignore
        result = await sess.execute(
            _MYSQL_TABLE_EXISTS,
            {"schema": db_schema, "table_name": table_name},
        )
        if not result.scalar():
            log_info(f"Table {table_name} does not exist, skipping migration")
            return False

        applied = False

        result = await sess.execute(
            _MYSQL_COLUMN_EXISTS,
            {"schema": db_schema, "table": table_name, "column": "tenant_id"},
        )
        if result.scalar() is None:
            log_info(f"-- Adding tenant_id column to {table_name}")
            await sess.execute(text(f"ALTER TABLE {quoted_table} ADD COLUMN tenant_id {_MYSQL_TENANT_ID_COLUMN_TYPE}"))
            applied = True

        result = await sess.execute(
            _MYSQL_INDEX_EXISTS,
            {"schema": db_schema, "table": table_name, "index": index_name},
        )
        if result.scalar() is None:
            log_info(f"-- Adding index {index_name} on {table_name}")
            await sess.execute(
                text(f"CREATE INDEX {quote_db_identifier(db_type, index_name)} ON {quoted_table} (tenant_id)")
            )
            applied = True

        return applied


# ---------------------------------------------------------------------------
# Revert functions
# ---------------------------------------------------------------------------


def _revert_postgres(db: BaseDb, table_name: str) -> bool:
    """Revert: drop the index and the tenant_id column for PostgreSQL."""
    db_schema = db.db_schema or "public"  # type: ignore
    db_type = type(db).__name__
    quoted_schema = quote_db_identifier(db_type, db_schema)
    quoted_table = quote_db_identifier(db_type, table_name)
    full_table = f"{quoted_schema}.{quoted_table}"
    index_name = _index_name(table_name)

    with db.Session() as sess, sess.begin():  # type: ignore
        table_exists = sess.execute(
            _PG_TABLE_EXISTS,
            {"schema": db_schema, "table_name": table_name},
        ).scalar()

        if not table_exists:
            log_info(f"Table {table_name} does not exist, skipping revert")
            return False

        applied = False

        # The index must go before the column it covers.
        has_index = sess.execute(
            _PG_INDEX_EXISTS,
            {"schema": db_schema, "table": table_name, "index": index_name},
        ).scalar()
        if has_index is not None:
            log_info(f"-- Dropping index {index_name} on {table_name}")
            sess.execute(text(f"DROP INDEX {quoted_schema}.{quote_db_identifier(db_type, index_name)}"))
            applied = True

        has_column = sess.execute(
            _PG_COLUMN_EXISTS,
            {"schema": db_schema, "table": table_name, "column": "tenant_id"},
        ).scalar()
        if has_column is not None:
            log_info(f"-- Dropping tenant_id column from {table_name}")
            sess.execute(text(f"ALTER TABLE {full_table} DROP COLUMN tenant_id"))
            applied = True

        return applied


async def _revert_async_postgres(db: AsyncBaseDb, table_name: str) -> bool:
    """Revert: drop the index and the tenant_id column for async PostgreSQL."""
    db_schema = db.db_schema or "public"  # type: ignore
    db_type = type(db).__name__
    quoted_schema = quote_db_identifier(db_type, db_schema)
    quoted_table = quote_db_identifier(db_type, table_name)
    full_table = f"{quoted_schema}.{quoted_table}"
    index_name = _index_name(table_name)

    async with db.async_session_factory() as sess, sess.begin():  # type: ignore
        result = await sess.execute(
            _PG_TABLE_EXISTS,
            {"schema": db_schema, "table_name": table_name},
        )
        if not result.scalar():
            log_info(f"Table {table_name} does not exist, skipping revert")
            return False

        applied = False

        result = await sess.execute(
            _PG_INDEX_EXISTS,
            {"schema": db_schema, "table": table_name, "index": index_name},
        )
        if result.scalar() is not None:
            log_info(f"-- Dropping index {index_name} on {table_name}")
            await sess.execute(text(f"DROP INDEX {quoted_schema}.{quote_db_identifier(db_type, index_name)}"))
            applied = True

        result = await sess.execute(
            _PG_COLUMN_EXISTS,
            {"schema": db_schema, "table": table_name, "column": "tenant_id"},
        )
        if result.scalar() is not None:
            log_info(f"-- Dropping tenant_id column from {table_name}")
            await sess.execute(text(f"ALTER TABLE {full_table} DROP COLUMN tenant_id"))
            applied = True

        return applied


def _revert_sqlite(db: BaseDb, table_name: str) -> bool:
    """Revert: drop the index and the tenant_id column for SQLite.

    ``ALTER TABLE ... DROP COLUMN`` needs SQLite 3.35+; on older builds the index is still
    dropped and the now-unused column is left in place with a log message.
    """
    index_name = _index_name(table_name)

    with db.Session() as sess, sess.begin():  # type: ignore
        table_exists = sess.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:table_name"),
            {"table_name": table_name},
        ).scalar()

        if not table_exists:
            log_info(f"Table {table_name} does not exist, skipping revert")
            return False

        applied = False

        indexes = sess.execute(text(f"PRAGMA index_list({table_name})")).fetchall()
        index_names = {idx[1] for idx in indexes}

        # The index must go before the column: SQLite refuses to drop a column that an index
        # still references.
        if index_name in index_names:
            log_info(f"-- Dropping index {index_name} on {table_name}")
            sess.execute(text(f"DROP INDEX {index_name}"))
            applied = True

        columns_info = sess.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        existing_columns = {col[1] for col in columns_info}

        if "tenant_id" in existing_columns:
            sqlite_version = sess.execute(text("SELECT sqlite_version()")).scalar() or ""
            if _sqlite_supports_drop_column(str(sqlite_version)):
                log_info(f"-- Dropping tenant_id column from {table_name}")
                sess.execute(text(f"ALTER TABLE {table_name} DROP COLUMN tenant_id"))
                applied = True
            else:
                log_info(
                    f"SQLite {sqlite_version} does not support DROP COLUMN "
                    f"(needs 3.35+); leaving tenant_id on {table_name}"
                )

        return applied


async def _revert_async_sqlite(db: AsyncBaseDb, table_name: str) -> bool:
    """Revert: drop the index and the tenant_id column for async SQLite."""
    index_name = _index_name(table_name)

    async with db.async_session_factory() as sess, sess.begin():  # type: ignore
        result = await sess.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:table_name"),
            {"table_name": table_name},
        )
        if not result.scalar():
            log_info(f"Table {table_name} does not exist, skipping revert")
            return False

        applied = False

        result = await sess.execute(text(f"PRAGMA index_list({table_name})"))
        index_names = {idx[1] for idx in result.fetchall()}

        if index_name in index_names:
            log_info(f"-- Dropping index {index_name} on {table_name}")
            await sess.execute(text(f"DROP INDEX {index_name}"))
            applied = True

        result = await sess.execute(text(f"PRAGMA table_info({table_name})"))
        existing_columns = {col[1] for col in result.fetchall()}

        if "tenant_id" in existing_columns:
            result = await sess.execute(text("SELECT sqlite_version()"))
            sqlite_version = result.scalar() or ""
            if _sqlite_supports_drop_column(str(sqlite_version)):
                log_info(f"-- Dropping tenant_id column from {table_name}")
                await sess.execute(text(f"ALTER TABLE {table_name} DROP COLUMN tenant_id"))
                applied = True
            else:
                log_info(
                    f"SQLite {sqlite_version} does not support DROP COLUMN "
                    f"(needs 3.35+); leaving tenant_id on {table_name}"
                )

        return applied


# ---------------------------------------------------------------------------
# MySQL / SingleStore revert
# ---------------------------------------------------------------------------


def _revert_mysql(db: BaseDb, table_name: str) -> bool:
    """Revert: drop the index and the tenant_id column for MySQL/SingleStore."""
    db_type = type(db).__name__
    db_schema = db.db_schema  # type: ignore
    quoted_table = quote_db_identifier(db_type, table_name)
    index_name = _index_name(table_name)

    with db.Session() as sess, sess.begin():  # type: ignore
        table_exists = sess.execute(
            _MYSQL_TABLE_EXISTS,
            {"schema": db_schema, "table_name": table_name},
        ).scalar()

        if not table_exists:
            log_info(f"Table {table_name} does not exist, skipping revert")
            return False

        applied = False

        # MySQL's DROP INDEX requires the owning table and refuses to drop a column an index
        # still references, same ordering constraint as SQLite.
        has_index = sess.execute(
            _MYSQL_INDEX_EXISTS,
            {"schema": db_schema, "table": table_name, "index": index_name},
        ).scalar()
        if has_index is not None:
            log_info(f"-- Dropping index {index_name} on {table_name}")
            sess.execute(text(f"DROP INDEX {quote_db_identifier(db_type, index_name)} ON {quoted_table}"))
            applied = True

        has_column = sess.execute(
            _MYSQL_COLUMN_EXISTS,
            {"schema": db_schema, "table": table_name, "column": "tenant_id"},
        ).scalar()
        if has_column is not None:
            log_info(f"-- Dropping tenant_id column from {table_name}")
            sess.execute(text(f"ALTER TABLE {quoted_table} DROP COLUMN tenant_id"))
            applied = True

        return applied


async def _revert_async_mysql(db: AsyncBaseDb, table_name: str) -> bool:
    """Revert: drop the index and the tenant_id column for async MySQL."""
    db_type = type(db).__name__
    db_schema = db.db_schema  # type: ignore
    quoted_table = quote_db_identifier(db_type, table_name)
    index_name = _index_name(table_name)

    async with db.async_session_factory() as sess, sess.begin():  # type: ignore
        result = await sess.execute(
            _MYSQL_TABLE_EXISTS,
            {"schema": db_schema, "table_name": table_name},
        )
        if not result.scalar():
            log_info(f"Table {table_name} does not exist, skipping revert")
            return False

        applied = False

        result = await sess.execute(
            _MYSQL_INDEX_EXISTS,
            {"schema": db_schema, "table": table_name, "index": index_name},
        )
        if result.scalar() is not None:
            log_info(f"-- Dropping index {index_name} on {table_name}")
            await sess.execute(text(f"DROP INDEX {quote_db_identifier(db_type, index_name)} ON {quoted_table}"))
            applied = True

        result = await sess.execute(
            _MYSQL_COLUMN_EXISTS,
            {"schema": db_schema, "table": table_name, "column": "tenant_id"},
        )
        if result.scalar() is not None:
            log_info(f"-- Dropping tenant_id column from {table_name}")
            await sess.execute(text(f"ALTER TABLE {quoted_table} DROP COLUMN tenant_id"))
            applied = True

        return applied
