"""
Db
==

Demonstrates db.
"""

from egpt.integrations.db.surrealdb import SurrealDb

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# ************* SurrealDB Config *************
SURREALDB_URL = "ws://localhost:8000"
SURREALDB_USER = "root"
SURREALDB_PASSWORD = "root"
SURREALDB_NAMESPACE = "egpt"
SURREALDB_DATABASE = "agent_os_demo"
# *******************************

# ************* Create the SurrealDB instance *************
creds = {"username": SURREALDB_USER, "password": SURREALDB_PASSWORD}
db = SurrealDb(None, SURREALDB_URL, creds, SURREALDB_NAMESPACE, SURREALDB_DATABASE)
# *******************************

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit("This module is intended to be imported.")
