import asyncio

from egpt.db.migrations.manager import MigrationManager
from egpt.db.postgres import AsyncPostgresDb, PostgresDb

# Create your database connection here.
# Pre-rename deployments store their data in agno_* tables. Setting
# table_prefix="agno_" points every default table name (sessions, memories,
# metrics, evals, knowledge, culture, approvals, ... and the
# agno_schema_versions bookkeeping table) at the legacy layout so the
# migration operates on the real data instead of empty egpt_* tables.
db = PostgresDb(
    db_url="postgresql+psycopg://ai:ai@localhost:5532/ai",
    table_prefix="agno_",
)
# Or for asynchronous operations
async_db = AsyncPostgresDb(
    db_url="postgresql+psycopg_async://ai:ai@localhost:5532/ai",
    table_prefix="agno_",
)


# Upgrade your DB to the latest version
async def run_migration():
    await MigrationManager(async_db).up()
    # Optionally force the migration if necessary
    # await MigrationManager(db).up(force=True)

    # Downgrade your DB to a specific version
    # await MigrationManager(db).down(target_version="2.0.0")


if __name__ == "__main__":
    asyncio.run(run_migration())
