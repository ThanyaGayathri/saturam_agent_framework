"""Migration v2.9.0: Add tenant_id to the traces table (EGPT-4.3a, aicoe-tracker#201)

Changes:
- Add a nullable ``tenant_id`` column to the traces table.
- Add an ``idx_{table}_tenant_id`` index for tenant-scoped listings.

The column is nullable: traces written before multi-tenancy keep a NULL tenant_id,
which means "unscoped / pre-tenancy". The traces router handles NULL-tenant rows
as fail-closed (404 for scoped callers).

.. warning::
   Deploy order matters. ``_get_table`` validates a live table against the schema
   definition and raises when a declared column is missing. A deployment that ships
   EGPT-4.2/4.3 code before running ``MigrationManager(db).up()`` will fail every
   trace read/write until the migration runs. Run the migration first, or in the
   same maintenance step.
"""

from egpt.db.base import AsyncBaseDb, BaseDb
from egpt.db.migrations.utils import quote_db_identifier
from egpt.utils.log import log_error, log_info

try:
    from sqlalchemy import text
except ImportError:
    raise ImportError("`sqlalchemy` not installed. Please install it using `pip install sqlalchemy`")


TRACES_TABLE_TYPE = "traces"

# ---------------------------------------------------------------------------
# Shared SQL templates
# ---------------------------------------------------------------------------

_PG_TABLE_EXISTS = text(
    "SELECT table_name FROM information_schema.tables WHERE table_schema = :schema AND table_name = :table_name"
)

_PG_COLUMN_EXISTS = text(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema = :schema AND table_name = :table AND column_name = :column"
)

_PG_INDEX_EXISTS = text(
    "SELECT indexname FROM pg_indexes WHERE schemaname = :schema AND tablename = :table AND indexname = :index"
)


def _index_name(table_name: str) -> str:
    return f"idx_{table_name}_tenant_id"


# ---------------------------------------------------------------------------
# PostgreSQL (sync)
# ---------------------------------------------------------------------------


def _migrate_postgres(db: BaseDb, table_name: str) -> bool:
    """Add the tenant_id column and index to the traces table for PostgreSQL."""
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


# ---------------------------------------------------------------------------
# PostgreSQL (async)
# ---------------------------------------------------------------------------


async def _migrate_async_postgres(db: AsyncBaseDb, table_name: str) -> bool:
    """Add the tenant_id column and index to the traces table for async PostgreSQL."""
    db_schema = db.db_schema or "public"  # type: ignore
    db_type = type(db).__name__
    quoted_schema = quote_db_identifier(db_type, db_schema)
    quoted_table = quote_db_identifier(db_type, table_name)
    full_table = f"{quoted_schema}.{quoted_table}"
    index_name = _index_name(table_name)

    async with db.async_session() as sess:  # type: ignore
        async with sess.begin():
            table_exists = (
                await sess.execute(
                    _PG_TABLE_EXISTS,
                    {"schema": db_schema, "table_name": table_name},
                )
            ).scalar()
            if not table_exists:
                log_info(f"Table {table_name} does not exist, skipping migration")
                return False

            applied = False

            has_column = (
                await sess.execute(
                    _PG_COLUMN_EXISTS,
                    {"schema": db_schema, "table": table_name, "column": "tenant_id"},
                )
            ).scalar()
            if has_column is None:
                log_info(f"-- Adding tenant_id column to {table_name}")
                await sess.execute(text(f"ALTER TABLE {full_table} ADD COLUMN tenant_id VARCHAR"))
                applied = True

            has_index = (
                await sess.execute(
                    _PG_INDEX_EXISTS,
                    {"schema": db_schema, "table": table_name, "index": index_name},
                )
            ).scalar()
            if has_index is None:
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
    """Add the tenant_id column to the traces table for SQLite.

    SQLite has the auto-migration mechanism (_try_add_missing_columns) that
    already handles this, but we include a migration for consistency and for
    deployments that run migrations explicitly.
    """
    with db.Session() as sess, sess.begin():  # type: ignore
        # Check if table exists
        result = sess.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"), {"name": table_name}
        )
        if result.scalar() is None:
            log_info(f"Table {table_name} does not exist, skipping migration")
            return False

        # Check existing columns
        columns_result = sess.execute(text(f"PRAGMA table_info({table_name})"))
        existing_columns = {row[1] for row in columns_result}

        if "tenant_id" not in existing_columns:
            log_info(f"-- Adding tenant_id column to {table_name}")
            sess.execute(text(f"ALTER TABLE {table_name} ADD COLUMN tenant_id TEXT"))
            return True

        return False


# ---------------------------------------------------------------------------
# Public API (called by MigrationManager)
# ---------------------------------------------------------------------------


def up(db: BaseDb, table_type: str, table_name: str) -> bool:
    """Run the v2.9.0 migration (add tenant_id to traces)."""
    if table_type != TRACES_TABLE_TYPE:
        return False
    db_type = type(db).__name__
    try:
        if "Postgres" in db_type and "Async" not in db_type:
            return _migrate_postgres(db, table_name)
        elif "Sqlite" in db_type and "Async" not in db_type:
            return _migrate_sqlite(db, table_name)
        else:
            log_info(f"v2.9.0 migration not applicable for {db_type}")
            return False
    except Exception as e:
        log_error(f"v2.9.0 migration failed for {table_name}: {e}")
        return False


async def async_up(db: AsyncBaseDb, table_type: str, table_name: str) -> bool:
    """Run the v2.9.0 migration (add tenant_id to traces) for async backends."""
    if table_type != TRACES_TABLE_TYPE:
        return False
    db_type = type(db).__name__
    try:
        if "Postgres" in db_type:
            return await _migrate_async_postgres(db, table_name)
        else:
            log_info(f"v2.9.0 async migration not applicable for {db_type}")
            return False
    except Exception as e:
        log_error(f"v2.9.0 async migration failed for {table_name}: {e}")
        return False


def down(db: BaseDb, table_type: str, table_name: str) -> bool:
    """Revert the v2.9.0 migration (remove tenant_id from traces).

    Note: SQLite < 3.35 does not support DROP COLUMN.
    """
    log_info(
        f"v2.9.0 down migration: tenant_id removal from {table_name} not implemented (nullable column, safe to leave)"
    )
    return False


async def async_down(db: AsyncBaseDb, table_type: str, table_name: str) -> bool:
    """Revert the v2.9.0 async migration."""
    log_info(
        f"v2.9.0 async down migration: tenant_id removal from {table_name} not implemented (nullable column, safe to leave)"
    )
    return False
