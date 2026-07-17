import asyncio
from os import getenv

from egpt.db.migrations.manager import MigrationManager
from egpt.db.singlestore import SingleStoreDb

# Create your database connection here
USERNAME = getenv("SINGLESTORE_USERNAME")
PASSWORD = getenv("SINGLESTORE_PASSWORD")
HOST = getenv("SINGLESTORE_HOST")
PORT = getenv("SINGLESTORE_PORT")
DATABASE = getenv("SINGLESTORE_DATABASE")

db_url = f"mysql+pymysql://{USERNAME}:{PASSWORD}@{HOST}:{PORT}/{DATABASE}"
# Pre-rename deployments store their data in agno_* tables. Setting
# table_prefix="agno_" points every default table name (including the
# agno_schema_versions bookkeeping table) at the legacy layout so the
# migration operates on the real data instead of empty egpt_* tables.
db = SingleStoreDb(db_url=db_url, table_prefix="agno_")


# Upgrade your DB to the latest version
async def run_migration():
    await MigrationManager(db).up()
    # Optionally force the migration if necessary
    # await MigrationManager(db).up(force=True)

    # Downgrade your DB to a specific version
    # await MigrationManager(db).down(target_version="2.0.0")


if __name__ == "__main__":
    asyncio.run(run_migration())
