"""Central branding constants for user-facing product strings.

Persistence identity (database table prefixes) lives in
``egpt.db.table_names`` — not here — because changing it re-points every
default-configured deployment at fresh empty tables. See
``DEFAULT_TABLE_PREFIX`` / ``LEGACY_TABLE_PREFIX`` there.
"""

PRODUCT_NAME = "EGPT"
PRODUCT_OS_NAME = "EgptOS"
# Wire-visible default identity for REST and MCP surfaces (kept in sync).
DEFAULT_OS_ID = "egpt-os"
ENV_PREFIX = "EGPT"
LEGACY_ENV_PREFIX = "AGNO"

__all__ = [
    "DEFAULT_OS_ID",
    "ENV_PREFIX",
    "LEGACY_ENV_PREFIX",
    "PRODUCT_NAME",
    "PRODUCT_OS_NAME",
]
