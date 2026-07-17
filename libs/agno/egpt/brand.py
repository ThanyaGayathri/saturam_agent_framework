"""Central branding constants for user-facing product strings."""

PRODUCT_NAME = "EGPT"
PRODUCT_OS_NAME = "EgptOS"
# Wire-visible default identity for REST and MCP surfaces (kept in sync).
DEFAULT_OS_ID = "egpt-os"
ENV_PREFIX = "EGPT"
LEGACY_ENV_PREFIX = "AGNO"
# Default prefix for persisted database table names (e.g. egpt_sessions).
TABLE_PREFIX = "egpt_"

__all__ = [
    "DEFAULT_OS_ID",
    "ENV_PREFIX",
    "LEGACY_ENV_PREFIX",
    "PRODUCT_NAME",
    "PRODUCT_OS_NAME",
    "TABLE_PREFIX",
]
