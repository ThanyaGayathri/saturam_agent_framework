"""Migration v2.6.0: Add tenant_id to the component tables (EGPT-2.1)

Changes:
- Add a nullable ``tenant_id`` column to the components, component_configs and
  component_links tables.
- Add tenant-leading composite indexes for tenant-scoped lookups.

The column is nullable and is not backfilled: rows written before multi-tenancy keep a NULL
tenant_id, which means "unscoped". Nothing about the existing version / stage / link data is
touched, in either direction, which is what makes this migration reversible.

Index names match the ones the backend table builders derive (``idx_{table}_{columns}``) so a
freshly created table and a migrated table end up with the same schema.
"""

from typing import List, Tuple

from egpt.db.base import AsyncBaseDb, BaseDb
from egpt.db.migrations.utils import quote_db_identifier
from egpt.utils.log import log_error, log_info

try:
    from sqlalchemy import text
except ImportError:
    raise ImportError("`sqlalchemy` not installed. Please install it using `pip install sqlalchemy`")


COMPONENT_TABLE_TYPES = ("components", "component_configs", "component_links")

# Composite indexes to create per component table type, as (column tuple,) entries. The index
# name is derived from the resolved table name, so it is computed at migration time.
_COMPOSITE_INDEX_COLUMNS: dict[str, List[Tuple[str, ...]]] = {
    "components": [("tenant_id", "component_id")],
    "component_configs": [("tenant_id", "component_id")],
    "component_links": [("tenant_id", "parent_component_id"), ("tenant_id", "child_component_id")],
}

# SQLite gained ALTER TABLE ... DROP COLUMN in 3.35.0.
_SQLITE_DROP_COLUMN_MIN_VERSION = (3, 35, 0)


def _index_names(table_type: str, table_name: str) -> List[str]:
    """Derive the composite index names for a component table.

    Mirrors the ``idx_{table_name}_{'_'.join(columns)}`` convention used by the Postgres and
    SQLite table builders.
    """
    return [f"idx_{table_name}_{'_'.join(columns)}" for columns in _COMPOSITE_INDEX_COLUMNS[table_type]]


def up(db: BaseDb, table_type: str, table_name: str) -> bool:
    """Add tenant_id and its composite indexes to a component table.

    Returns:
        bool: True if any migration was applied, False otherwise.
    """
    db_type = type(db).__name__

    try:
        if table_type not in COMPONENT_TABLE_TYPES:
            return False

        if db_type == "PostgresDb":
            return _migrate_postgres(db, table_type, table_name)
        elif db_type == "SqliteDb":
            return _migrate_sqlite(db, table_type, table_name)
        else:
            log_info(f"{db_type} does not require schema migrations")
        return False
    except Exception as e:
        log_error(f"Error running migration v2.6.0 for {db_type} on table {table_name}: {str(e)}")
        raise


async def async_up(db: AsyncBaseDb, table_type: str, table_name: str) -> bool:
    """Add tenant_id and its composite indexes to a component table.

    Returns:
        bool: True if any migration was applied, False otherwise.
    """
    db_type = type(db).__name__

    try:
        if table_type not in COMPONENT_TABLE_TYPES:
            return False

        if db_type == "AsyncPostgresDb":
            return await _migrate_async_postgres(db, table_type, table_name)
        elif db_type == "AsyncSqliteDb":
            return await _migrate_async_sqlite(db, table_type, table_name)
        else:
            log_info(f"{db_type} does not require schema migrations")
        return False
    except Exception as e:
        log_error(f"Error running migration v2.6.0 for {db_type} on table {table_name}: {str(e)}")
        raise


def down(db: BaseDb, table_type: str, table_name: str) -> bool:
    """Revert: drop the composite indexes and the tenant_id column.

    Returns:
        bool: True if any migration was reverted, False otherwise.
    """
    db_type = type(db).__name__

    try:
        if table_type not in COMPONENT_TABLE_TYPES:
            return False

        if db_type == "PostgresDb":
            return _revert_postgres(db, table_type, table_name)
        elif db_type == "SqliteDb":
            return _revert_sqlite(db, table_type, table_name)
        else:
            log_info(f"Revert not implemented for {db_type}")
        return False
    except Exception as e:
        log_error(f"Error reverting migration v2.6.0 for {db_type} on table {table_name}: {str(e)}")
        raise


async def async_down(db: AsyncBaseDb, table_type: str, table_name: str) -> bool:
    """Revert: drop the composite indexes and the tenant_id column.

    Returns:
        bool: True if any migration was reverted, False otherwise.
    """
    db_type = type(db).__name__

    try:
        if table_type not in COMPONENT_TABLE_TYPES:
            return False

        if db_type == "AsyncPostgresDb":
            return await _revert_async_postgres(db, table_type, table_name)
        elif db_type == "AsyncSqliteDb":
            return await _revert_async_sqlite(db, table_type, table_name)
        else:
            log_info(f"Revert not implemented for {db_type}")
        return False
    except Exception as e:
        log_error(f"Error reverting migration v2.6.0 for {db_type} on table {table_name} asynchronously: {str(e)}")
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


def _migrate_postgres(db: BaseDb, table_type: str, table_name: str) -> bool:
    """Add tenant_id column and composite indexes for PostgreSQL."""
    db_schema = db.db_schema or "public"  # type: ignore
    db_type = type(db).__name__
    quoted_schema = quote_db_identifier(db_type, db_schema)
    quoted_table = quote_db_identifier(db_type, table_name)
    full_table = f"{quoted_schema}.{quoted_table}"

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

        for index_name, columns in zip(_index_names(table_type, table_name), _COMPOSITE_INDEX_COLUMNS[table_type]):
            has_index = sess.execute(
                _PG_INDEX_EXISTS,
                {"schema": db_schema, "table": table_name, "index": index_name},
            ).scalar()
            if has_index is None:
                log_info(f"-- Adding index {index_name} on {table_name}")
                sess.execute(
                    text(
                        f"CREATE INDEX {quote_db_identifier(db_type, index_name)} "
                        f"ON {full_table} ({', '.join(columns)})"
                    )
                )
                applied = True

        return applied


async def _migrate_async_postgres(db: AsyncBaseDb, table_type: str, table_name: str) -> bool:
    """Add tenant_id column and composite indexes for async PostgreSQL."""
    db_schema = db.db_schema or "public"  # type: ignore
    db_type = type(db).__name__
    quoted_schema = quote_db_identifier(db_type, db_schema)
    quoted_table = quote_db_identifier(db_type, table_name)
    full_table = f"{quoted_schema}.{quoted_table}"

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

        for index_name, columns in zip(_index_names(table_type, table_name), _COMPOSITE_INDEX_COLUMNS[table_type]):
            result = await sess.execute(
                _PG_INDEX_EXISTS,
                {"schema": db_schema, "table": table_name, "index": index_name},
            )
            if result.scalar() is None:
                log_info(f"-- Adding index {index_name} on {table_name}")
                await sess.execute(
                    text(
                        f"CREATE INDEX {quote_db_identifier(db_type, index_name)} "
                        f"ON {full_table} ({', '.join(columns)})"
                    )
                )
                applied = True

        return applied


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def _migrate_sqlite(db: BaseDb, table_type: str, table_name: str) -> bool:
    """Add tenant_id column and composite indexes for SQLite."""
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

        for index_name, columns in zip(_index_names(table_type, table_name), _COMPOSITE_INDEX_COLUMNS[table_type]):
            if index_name not in index_names:
                log_info(f"-- Adding index {index_name} on {table_name}")
                sess.execute(text(f"CREATE INDEX {index_name} ON {table_name} ({', '.join(columns)})"))
                applied = True

        return applied


async def _migrate_async_sqlite(db: AsyncBaseDb, table_type: str, table_name: str) -> bool:
    """Add tenant_id column and composite indexes for async SQLite."""
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

        for index_name, columns in zip(_index_names(table_type, table_name), _COMPOSITE_INDEX_COLUMNS[table_type]):
            if index_name not in index_names:
                log_info(f"-- Adding index {index_name} on {table_name}")
                await sess.execute(text(f"CREATE INDEX {index_name} ON {table_name} ({', '.join(columns)})"))
                applied = True

        return applied


# ---------------------------------------------------------------------------
# Revert functions
# ---------------------------------------------------------------------------


def _revert_postgres(db: BaseDb, table_type: str, table_name: str) -> bool:
    """Revert: drop the composite indexes and the tenant_id column for PostgreSQL."""
    db_schema = db.db_schema or "public"  # type: ignore
    db_type = type(db).__name__
    quoted_schema = quote_db_identifier(db_type, db_schema)
    quoted_table = quote_db_identifier(db_type, table_name)
    full_table = f"{quoted_schema}.{quoted_table}"

    with db.Session() as sess, sess.begin():  # type: ignore
        table_exists = sess.execute(
            _PG_TABLE_EXISTS,
            {"schema": db_schema, "table_name": table_name},
        ).scalar()

        if not table_exists:
            log_info(f"Table {table_name} does not exist, skipping revert")
            return False

        applied = False

        # Indexes must go before the column they cover.
        for index_name in _index_names(table_type, table_name):
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


async def _revert_async_postgres(db: AsyncBaseDb, table_type: str, table_name: str) -> bool:
    """Revert: drop the composite indexes and the tenant_id column for async PostgreSQL."""
    db_schema = db.db_schema or "public"  # type: ignore
    db_type = type(db).__name__
    quoted_schema = quote_db_identifier(db_type, db_schema)
    quoted_table = quote_db_identifier(db_type, table_name)
    full_table = f"{quoted_schema}.{quoted_table}"

    async with db.async_session_factory() as sess, sess.begin():  # type: ignore
        result = await sess.execute(
            _PG_TABLE_EXISTS,
            {"schema": db_schema, "table_name": table_name},
        )
        if not result.scalar():
            log_info(f"Table {table_name} does not exist, skipping revert")
            return False

        applied = False

        for index_name in _index_names(table_type, table_name):
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


def _revert_sqlite(db: BaseDb, table_type: str, table_name: str) -> bool:
    """Revert: drop the composite indexes and the tenant_id column for SQLite.

    ``ALTER TABLE ... DROP COLUMN`` needs SQLite 3.35+; on older builds the indexes are still
    dropped and the now-unused column is left in place with a warning.
    """
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

        # Indexes must go before the column they cover: SQLite refuses to drop a column that
        # an index still references.
        for index_name in _index_names(table_type, table_name):
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


async def _revert_async_sqlite(db: AsyncBaseDb, table_type: str, table_name: str) -> bool:
    """Revert: drop the composite indexes and the tenant_id column for async SQLite."""
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

        for index_name in _index_names(table_type, table_name):
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
