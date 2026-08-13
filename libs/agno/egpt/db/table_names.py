"""Canonical constants for persisted database table naming.

WARNING — blast radius: ``DEFAULT_TABLE_PREFIX`` is persistence identity, not a
cosmetic product string. Changing it re-points every default-configured
deployment at fresh, empty tables (sessions, memories, knowledge, traces, ...)
while the existing data stays behind under the old names. Do NOT sweep this
value in a brand-refresh PR without an upgrade plan.
"""

import re
from typing import Dict, Optional

# Default prefix for persisted database table names (e.g. egpt_sessions).
DEFAULT_TABLE_PREFIX = "egpt_"

# Prefix used by pre-rename (EGPT) deployments. Referenced by the one-release
# compatibility shim in egpt.db.utils and by legacy-table detection warnings.
LEGACY_TABLE_PREFIX = "agno_"

# Single source of truth mapping BaseDb constructor parameter names
# (e.g. "session_table") to the suffix appended to the table prefix when no
# explicit name is passed. Drives serialization and the override whitelist.
TABLE_SUFFIXES: Dict[str, str] = {
    "session_table": "sessions",
    "culture_table": "culture",
    "memory_table": "memories",
    "metrics_table": "metrics",
    "eval_table": "eval_runs",
    "knowledge_table": "knowledge",
    "traces_table": "traces",
    "spans_table": "spans",
    "versions_table": "schema_versions",
    "components_table": "components",
    "component_configs_table": "component_configs",
    "component_links_table": "component_links",
    "component_audit_table": "component_audit",
    "learnings_table": "learnings",
    "schedules_table": "schedules",
    "schedule_runs_table": "schedule_runs",
    "approvals_table": "approvals",
    "auth_tokens_table": "auth_tokens",
    "service_accounts_table": "service_accounts",
}

# The SM-R12 memory tiers (SM-C3 #91), in the order a migration should create them.
#
# Deliberately NOT in TABLE_SUFFIXES: these names derive from ``table_prefix`` and are not
# individually overridable, because nothing reads them yet -- per-table overrides exist to be
# passed through ``clone_with_table_overrides`` by a consumer, and the read/write path arrives
# with SM-V1 (#95) and SM-C2 (#90). Promoting them is a one-line move plus the six constructor
# parameters each backend would then need. ``test_memory_table_names.py`` pins the split so it
# stays a decision rather than an omission.
#
# Each entry is both the table type and its suffix; the attribute is ``{type}_table_name``.
MEMORY_TIER_TABLE_TYPES = (
    "memory_episodic",
    "memory_semantic_fact",
    "memory_entity",
    "memory_relation",
    "memory_procedural",
    "memory_link",
)


def memory_tier_table_attr(table_type: str) -> str:
    """Return the instance attribute holding a memory tier table's resolved name."""
    return f"{table_type}_table_name"


# Keep prefixes short enough that the longest derived identifier
# (e.g. idx_{prefix}component_configs_{columns}) stays under the PostgreSQL
# 63-byte identifier limit without silent truncation.
TABLE_PREFIX_MAX_LENGTH = 32

_TABLE_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def table_name_attr(param: str) -> str:
    """Return the instance attribute name for a table constructor parameter."""
    if param == "traces_table":
        return "trace_table_name"
    if param == "spans_table":
        return "span_table_name"
    return f"{param}_name"


def validate_table_prefix(table_prefix: Optional[str]) -> str:
    """Resolve and validate a table prefix.

    ``None`` means "use the branded default"; an empty string explicitly
    requests unprefixed table names. Anything else must be a safe SQL
    identifier fragment so raw concatenation into table and index names cannot
    produce quoted/case-sensitive or over-long identifiers.
    """
    if table_prefix is None:
        return DEFAULT_TABLE_PREFIX
    if table_prefix == "":
        return ""
    if not _TABLE_PREFIX_PATTERN.match(table_prefix):
        raise ValueError(
            f"Invalid table_prefix {table_prefix!r}: must start with a lowercase letter "
            "and contain only lowercase letters, digits, and underscores."
        )
    if len(table_prefix) > TABLE_PREFIX_MAX_LENGTH:
        raise ValueError(
            f"Invalid table_prefix {table_prefix!r}: longer than {TABLE_PREFIX_MAX_LENGTH} characters, "
            "which risks exceeding database identifier length limits on derived index names."
        )
    return table_prefix
