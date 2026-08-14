"""Table schemas and related utils used by the PostgresDb class"""

from typing import Any

from egpt.db.table_names import DEFAULT_TABLE_PREFIX as TABLE_PREFIX

try:
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.types import BigInteger, Boolean, Date, Float, Integer, String, Text
except ImportError:
    raise ImportError("`sqlalchemy` not installed. Please install it using `pip install sqlalchemy`")

SESSION_TABLE_SCHEMA = {
    "session_id": {"type": String, "primary_key": True, "nullable": False},
    "session_type": {"type": String, "nullable": False, "index": True},
    "agent_id": {"type": String, "nullable": True},
    "team_id": {"type": String, "nullable": True},
    "workflow_id": {"type": String, "nullable": True},
    "user_id": {"type": String, "nullable": True},
    "session_data": {"type": JSONB, "nullable": True},
    "agent_data": {"type": JSONB, "nullable": True},
    "team_data": {"type": JSONB, "nullable": True},
    "workflow_data": {"type": JSONB, "nullable": True},
    "metadata": {"type": JSONB, "nullable": True},
    "runs": {"type": JSONB, "nullable": True},
    "summary": {"type": JSONB, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": False, "index": True},
    "updated_at": {"type": BigInteger, "nullable": True},
}

MEMORY_TABLE_SCHEMA = {
    "memory_id": {"type": String, "primary_key": True, "nullable": False},
    "memory": {"type": JSONB, "nullable": False},
    # The tenant this memory belongs to (SM-C3 #91). Nullable and un-backfilled: rows written
    # before multi-tenancy mean "unscoped", which is what makes the migration reversible.
    # Nothing filters on it yet -- MemoryManager still partitions by user_id alone, and the
    # read/write path is aicoe-tracker#175.
    "tenant_id": {"type": String, "nullable": True, "index": True},
    "feedback": {"type": Text, "nullable": True},
    "input": {"type": Text, "nullable": True},
    "agent_id": {"type": String, "nullable": True},
    "team_id": {"type": String, "nullable": True},
    "user_id": {"type": String, "nullable": True, "index": True},
    "topics": {"type": JSONB, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": False, "index": True},
    "updated_at": {"type": BigInteger, "nullable": True, "index": True},
}

EVAL_TABLE_SCHEMA = {
    "run_id": {"type": String, "primary_key": True, "nullable": False},
    "eval_type": {"type": String, "nullable": False},
    "eval_data": {"type": JSONB, "nullable": False},
    "eval_input": {"type": JSONB, "nullable": False},
    "name": {"type": String, "nullable": True},
    "agent_id": {"type": String, "nullable": True},
    "team_id": {"type": String, "nullable": True},
    "workflow_id": {"type": String, "nullable": True},
    "model_id": {"type": String, "nullable": True},
    "model_provider": {"type": String, "nullable": True},
    "evaluated_component_name": {"type": String, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": False, "index": True},
    "updated_at": {"type": BigInteger, "nullable": True},
}

KNOWLEDGE_TABLE_SCHEMA = {
    "id": {"type": String, "primary_key": True, "nullable": False},
    "name": {"type": String, "nullable": False},
    "description": {"type": Text, "nullable": False},
    "metadata": {"type": JSONB, "nullable": True},
    "type": {"type": String, "nullable": True},
    "size": {"type": BigInteger, "nullable": True},
    "linked_to": {"type": String, "nullable": True},
    "access_count": {"type": BigInteger, "nullable": True},
    "status": {"type": String, "nullable": True},
    "status_message": {"type": Text, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": True},
    "updated_at": {"type": BigInteger, "nullable": True},
    "external_id": {"type": String, "nullable": True},
}

METRICS_TABLE_SCHEMA = {
    "id": {"type": String, "primary_key": True, "nullable": False},
    "agent_runs_count": {"type": BigInteger, "nullable": False, "default": 0},
    "team_runs_count": {"type": BigInteger, "nullable": False, "default": 0},
    "workflow_runs_count": {"type": BigInteger, "nullable": False, "default": 0},
    "agent_sessions_count": {"type": BigInteger, "nullable": False, "default": 0},
    "team_sessions_count": {"type": BigInteger, "nullable": False, "default": 0},
    "workflow_sessions_count": {"type": BigInteger, "nullable": False, "default": 0},
    "users_count": {"type": BigInteger, "nullable": False, "default": 0},
    "token_metrics": {"type": JSONB, "nullable": False, "default": {}},
    "model_metrics": {"type": JSONB, "nullable": False, "default": {}},
    "date": {"type": Date, "nullable": False, "index": True},
    "aggregation_period": {"type": String, "nullable": False},
    "created_at": {"type": BigInteger, "nullable": False},
    "updated_at": {"type": BigInteger, "nullable": True},
    "completed": {"type": Boolean, "nullable": False, "default": False},
    "_unique_constraints": [
        {
            "name": "uq_metrics_date_period",
            "columns": ["date", "aggregation_period"],
        }
    ],
}

CULTURAL_KNOWLEDGE_TABLE_SCHEMA = {
    "id": {"type": String, "primary_key": True, "nullable": False},
    "name": {"type": String, "nullable": False, "index": True},
    "summary": {"type": Text, "nullable": True},
    "content": {"type": JSONB, "nullable": True},
    "metadata": {"type": JSONB, "nullable": True},
    "input": {"type": Text, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": True},
    "updated_at": {"type": BigInteger, "nullable": True},
    "agent_id": {"type": String, "nullable": True},
    "team_id": {"type": String, "nullable": True},
}

VERSIONS_TABLE_SCHEMA = {
    "table_name": {"type": String, "nullable": False, "primary_key": True},
    "version": {"type": String, "nullable": False},
    "created_at": {"type": String, "nullable": False, "index": True},
    "updated_at": {"type": String, "nullable": True},
}

TRACE_TABLE_SCHEMA = {
    "trace_id": {"type": String, "primary_key": True, "nullable": False},
    "name": {"type": String, "nullable": False},
    "status": {"type": String, "nullable": False, "index": True},
    "start_time": {"type": String, "nullable": False, "index": True},  # ISO 8601 datetime string
    "end_time": {"type": String, "nullable": False},  # ISO 8601 datetime string
    "duration_ms": {"type": BigInteger, "nullable": False},
    "run_id": {"type": String, "nullable": True, "index": True},
    "session_id": {"type": String, "nullable": True, "index": True},
    "user_id": {"type": String, "nullable": True, "index": True},
    "agent_id": {"type": String, "nullable": True, "index": True},
    "team_id": {"type": String, "nullable": True, "index": True},
    "workflow_id": {"type": String, "nullable": True, "index": True},
    "created_at": {"type": String, "nullable": False, "index": True},  # ISO 8601 datetime string
}


def _get_span_table_schema(traces_table_name: str = TABLE_PREFIX + "traces", db_schema: str = "agno") -> dict[str, Any]:
    """Get the span table schema with the correct foreign key reference.

    Args:
        traces_table_name: The name of the traces table to reference in the foreign key.
        db_schema: The database schema name.

    Returns:
        The span table schema dictionary.
    """
    return {
        "span_id": {"type": String, "primary_key": True, "nullable": False},
        "trace_id": {
            "type": String,
            "nullable": False,
            "index": True,
            "foreign_key": f"{db_schema}.{traces_table_name}.trace_id",
        },
        "parent_span_id": {"type": String, "nullable": True, "index": True},
        "name": {"type": String, "nullable": False},
        "span_kind": {"type": String, "nullable": False},
        "status_code": {"type": String, "nullable": False},
        "status_message": {"type": Text, "nullable": True},
        "start_time": {"type": String, "nullable": False, "index": True},  # ISO 8601 datetime string
        "end_time": {"type": String, "nullable": False},  # ISO 8601 datetime string
        "duration_ms": {"type": BigInteger, "nullable": False},
        "attributes": {"type": JSONB, "nullable": True},
        "created_at": {"type": String, "nullable": False, "index": True},  # ISO 8601 datetime string
    }


# ``tenant_id`` scopes a component to a tenant. It is nullable: rows written before
# multi-tenancy (and by single-tenant callers that never pass one) carry NULL, which means
# "unscoped". It leads every composite index below, so a bare tenant filter is served by
# the composite and no separate single-column index is needed.
COMPONENT_TABLE_SCHEMA = {
    "component_id": {"type": String, "primary_key": True},
    "tenant_id": {"type": String, "nullable": True},
    "component_type": {"type": String, "nullable": False, "index": True},  # agent|team|workflow
    "name": {"type": String, "nullable": True, "index": True},
    "description": {"type": Text, "nullable": True},
    "current_version": {"type": Integer, "nullable": True, "index": True},
    "metadata": {"type": JSONB, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": False, "index": True},
    "updated_at": {"type": BigInteger, "nullable": True},
    "deleted_at": {"type": BigInteger, "nullable": True},
    "__composite_indexes__": [
        {"name": "tenant_id_component_id", "columns": ["tenant_id", "component_id"]},
    ],
}

COMPONENT_CONFIGS_TABLE_SCHEMA = {
    "component_id": {"type": String, "primary_key": True, "foreign_key": "components.component_id"},
    "version": {"type": Integer, "primary_key": True},
    "tenant_id": {"type": String, "nullable": True},
    "label": {"type": String, "nullable": True},  # stable|v1.2.0|pre-refactor
    "stage": {"type": String, "nullable": False, "default": "draft", "index": True},  # draft|published
    "config": {"type": JSONB, "nullable": False},
    "notes": {"type": Text, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": False, "index": True},
    "updated_at": {"type": BigInteger, "nullable": True},
    "deleted_at": {"type": BigInteger, "nullable": True},
    "__composite_indexes__": [
        {"name": "tenant_id_component_id", "columns": ["tenant_id", "component_id"]},
    ],
}

COMPONENT_LINKS_TABLE_SCHEMA = {
    "parent_component_id": {"type": String, "nullable": False},
    "parent_version": {"type": Integer, "nullable": False},
    "tenant_id": {"type": String, "nullable": True},
    "link_kind": {"type": String, "nullable": False, "index": True},
    "link_key": {"type": String, "nullable": False},
    "child_component_id": {"type": String, "nullable": False, "foreign_key": "components.component_id"},
    "child_version": {"type": Integer, "nullable": True},
    "position": {"type": Integer, "nullable": False},
    "meta": {"type": JSONB, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": True, "index": True},
    "updated_at": {"type": BigInteger, "nullable": True},
    "__primary_key__": ["parent_component_id", "parent_version", "link_kind", "link_key"],
    "__foreign_keys__": [
        {
            "columns": ["parent_component_id", "parent_version"],
            "ref_table": "component_configs",
            "ref_columns": ["component_id", "version"],
        }
    ],
    # Both directions are indexed: parent lookups drive get_links/load_component_graph,
    # child lookups drive get_dependents.
    "__composite_indexes__": [
        {"name": "tenant_id_parent_component_id", "columns": ["tenant_id", "parent_component_id"]},
        {"name": "tenant_id_child_component_id", "columns": ["tenant_id", "child_component_id"]},
    ],
}
# Append-only change history for component definitions (EGPT-2.6). Deliberately carries NO
# foreign key to ``components``: a hard delete removes the component and its configs, and an
# audit trail that vanished with the thing it describes would be no trail at all.
#
# ``audit_id`` is the ordering key, not ``created_at``: an update and the publish that follows
# it land in the same second, and the history has to come back in the order it happened. An
# ``Integer`` primary key becomes ``SERIAL`` here, so the value is server-assigned and the
# insert omits the column.
#
# Only two indexes, and no single-column ones: this table takes a write on every component
# mutation including ``Agent.save()``, and every read is already filtered to one component, so
# the composites below serve them while a third index would be pure write cost.
COMPONENT_AUDIT_TABLE_SCHEMA = {
    "audit_id": {"type": Integer, "primary_key": True},
    "component_id": {"type": String, "nullable": False},
    "tenant_id": {"type": String, "nullable": True},
    "version": {"type": Integer, "nullable": True},  # NULL for component-level actions
    "action": {"type": String, "nullable": False},  # component.*|config.*
    "actor": {"type": String, "nullable": True},  # NULL for an unattributed programmatic write
    "note": {"type": Text, "nullable": True},
    "details": {"type": JSONB, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": False},
    "__composite_indexes__": [
        {"name": "tenant_id_component_id", "columns": ["tenant_id", "component_id"]},
        {"name": "component_id_audit_id", "columns": ["component_id", "audit_id"]},
    ],
}

LEARNINGS_TABLE_SCHEMA = {
    "learning_id": {"type": String, "primary_key": True, "nullable": False},
    "learning_type": {"type": String, "nullable": False, "index": True},
    "namespace": {"type": String, "nullable": True, "index": True},
    "user_id": {"type": String, "nullable": True, "index": True},
    "agent_id": {"type": String, "nullable": True, "index": True},
    "team_id": {"type": String, "nullable": True, "index": True},
    "workflow_id": {"type": String, "nullable": True, "index": True},
    "session_id": {"type": String, "nullable": True, "index": True},
    "entity_id": {"type": String, "nullable": True, "index": True},
    "entity_type": {"type": String, "nullable": True, "index": True},
    "content": {"type": JSONB, "nullable": False},
    "metadata": {"type": JSONB, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": False, "index": True},
    "updated_at": {"type": BigInteger, "nullable": True},
}


SCHEDULE_TABLE_SCHEMA = {
    "id": {"type": String, "primary_key": True, "nullable": False},
    "name": {"type": String, "nullable": False, "index": True},
    "description": {"type": Text, "nullable": True},
    "method": {"type": String, "nullable": False},
    "endpoint": {"type": String, "nullable": False},
    "payload": {"type": JSONB, "nullable": True},
    "cron_expr": {"type": String, "nullable": False},
    "timezone": {"type": String, "nullable": False},
    "timeout_seconds": {"type": BigInteger, "nullable": False},
    "max_retries": {"type": BigInteger, "nullable": False},
    "retry_delay_seconds": {"type": BigInteger, "nullable": False},
    "enabled": {"type": Boolean, "nullable": False, "default": True},
    "next_run_at": {"type": BigInteger, "nullable": True, "index": True},
    "locked_by": {"type": String, "nullable": True},
    "locked_at": {"type": BigInteger, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": False, "index": True},
    "updated_at": {"type": BigInteger, "nullable": True},
    "__composite_indexes__": [
        {"name": "enabled_next_run_at", "columns": ["enabled", "next_run_at"]},
    ],
}


def _get_schedule_runs_table_schema(
    schedules_table_name: str = TABLE_PREFIX + "schedules", db_schema: str = "agno"
) -> dict[str, Any]:
    """Get the schedule runs table schema with a foreign key to the schedules table."""
    return {
        "id": {"type": String, "primary_key": True, "nullable": False},
        "schedule_id": {
            "type": String,
            "nullable": False,
            "index": True,
            "foreign_key": f"{db_schema}.{schedules_table_name}.id",
            "ondelete": "CASCADE",
        },
        "attempt": {"type": BigInteger, "nullable": False},
        "triggered_at": {"type": BigInteger, "nullable": True},
        "completed_at": {"type": BigInteger, "nullable": True},
        "status": {"type": String, "nullable": False, "index": True},
        "status_code": {"type": BigInteger, "nullable": True},
        "run_id": {"type": String, "nullable": True},
        "session_id": {"type": String, "nullable": True},
        "error": {"type": Text, "nullable": True},
        "input": {"type": JSONB, "nullable": True},
        "output": {"type": JSONB, "nullable": True},
        "requirements": {"type": JSONB, "nullable": True},
        "created_at": {"type": BigInteger, "nullable": False, "index": True},
    }


APPROVAL_TABLE_SCHEMA = {
    "id": {"type": String, "primary_key": True, "nullable": False},
    "run_id": {"type": String, "nullable": False, "index": True},
    "session_id": {"type": String, "nullable": False, "index": True},
    "status": {"type": String, "nullable": False, "index": True},
    "source_type": {"type": String, "nullable": False, "index": True},
    "approval_type": {"type": String, "nullable": True, "index": True},
    "pause_type": {"type": String, "nullable": False, "index": True},
    "tool_name": {"type": String, "nullable": True},
    "tool_args": {"type": JSONB, "nullable": True},
    "expires_at": {"type": BigInteger, "nullable": True},
    "agent_id": {"type": String, "nullable": True, "index": True},
    "team_id": {"type": String, "nullable": True, "index": True},
    "workflow_id": {"type": String, "nullable": True, "index": True},
    "user_id": {"type": String, "nullable": True, "index": True},
    "schedule_id": {"type": String, "nullable": True, "index": True},
    "schedule_run_id": {"type": String, "nullable": True, "index": True},
    "source_name": {"type": String, "nullable": True},
    "requirements": {"type": JSONB, "nullable": True},
    "context": {"type": JSONB, "nullable": True},
    "resolution_data": {"type": JSONB, "nullable": True},
    "resolved_by": {"type": String, "nullable": True},
    "resolved_at": {"type": BigInteger, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": False, "index": True},
    "updated_at": {"type": BigInteger, "nullable": True},
    # Run status from the associated run. Updated when run completes/errors/cancels.
    # Values: "PAUSED", "COMPLETED", "RUNNING", "ERROR", "CANCELLED", or None.
    "run_status": {"type": String, "nullable": True, "index": True},
}

AUTH_TOKEN_TABLE_SCHEMA = {
    "id": {"type": String, "primary_key": True, "nullable": False},
    "provider": {"type": String, "nullable": False, "index": True},
    # Empty string for single-user mode
    "user_id": {"type": String, "nullable": False, "index": True},
    "service": {"type": String, "nullable": False, "index": True},
    "token_data": {"type": JSONB, "nullable": False},
    "granted_scopes": {"type": JSONB, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": False, "index": True},
    "updated_at": {"type": BigInteger, "nullable": True},
    "_unique_constraints": [
        {"name": "uq_auth_token_provider_user_service", "columns": ["provider", "user_id", "service"]}
    ],
}

SERVICE_ACCOUNT_TABLE_SCHEMA = {
    "id": {"type": String, "primary_key": True, "nullable": False},
    "name": {"type": String, "nullable": False},
    # Ownership: the user this account belongs to (created_by is audit: who minted it).
    # NULL means a workspace-level machine account with no owning user.
    "user_id": {"type": String, "nullable": True},
    # The tenant this account acts for (EGPT-2.4). NULL means an unscoped platform account.
    # Nullable and un-backfilled, so every pre-2.4 row stays valid and the down-migration is a
    # clean DROP COLUMN. Note name uniqueness stays global rather than per-tenant: the partial
    # unique index below is not cleanly reversible if rebuilt, and a cross-tenant name clash is
    # a naming annoyance, not a security boundary.
    "tenant_id": {"type": String, "nullable": True, "index": True},
    "token_hash": {"type": String, "nullable": False, "unique": True, "index": True},
    "token_prefix": {"type": String, "nullable": False},
    "scopes": {"type": JSONB, "nullable": False},
    "created_at": {"type": BigInteger, "nullable": False, "index": True},
    "expires_at": {"type": BigInteger, "nullable": True},
    "last_used_at": {"type": BigInteger, "nullable": True},
    "revoked_at": {"type": BigInteger, "nullable": True},
    "created_by": {"type": String, "nullable": True},
    # Names are reusable after revocation (rotation keeps the same identity),
    # so uniqueness only applies to active accounts.
    "_partial_unique_indexes": [{"name": "uq_active_name", "columns": ["name"], "where": "revoked_at IS NULL"}],
}


# ---------------------------------------------------------------------------
# Memory tiers (SM-C3 #91) -- SM-R12's record model, mirroring
# ``benchmarks/memory_datamodel/ddl/schema.sql``. Column names match
# ``egpt.db.schemas.memory`` field-for-field; a parity test asserts it per tier, and a second
# asserts these agree column-for-column with the SQLite definitions.
#
# Two deliberate differences from that DDL, both because this is the portable db layer and not
# pgvector: ``embedding`` is a JSONB array rather than ``vector(768)`` (SM-V1 #95 owns the typed
# column, its HNSW index and the dimension parameterisation ADR 0003 section B.4 asks for), and
# there is no LIST partitioning, so ``tenant_id`` leads the composite primary key and every
# composite index instead of being a partition key.
#
# Index policy, because the per-tier asymmetry is a decision rather than an oversight: these
# mirror the indexes SM-R12's DDL declares, minus the HNSW ones that need pgvector. A bare tenant
# filter is already served by the primary key, so a tier gets its own index only where that DDL
# has a *non-PK* lookup -- traversal by endpoint, and point-in-time on the tiers SM-R10 filters.
# ``memory_link`` is bi-temporal but deliberately has no temporal index: SM-R12 indexes it by
# endpoint only, links being resolved from a known row rather than scanned by validity.
# ``memory_entity`` and ``memory_procedural`` get none beyond the PK for the same reason --
# ``entity_type``, ``name`` and ``kind`` are plausible filters, but no read path uses them yet and
# an index nothing queries is write cost on every insert. Read-path indexes belong with the reader
# (SM-V1 #95 / aicoe-tracker#175), which is also what knows the selectivity.
#
# A third difference the header used to omit: the DDL declares ``entity_ids``, ``supersedes``,
# ``superseded_by``, ``source_episode_ids`` and ``tags`` as ``TEXT[]`` (7 column instances across
# 4 tables); they are stored here as JSONB so SQLite can carry them too.
#
# Two fidelity gaps, named so a future reader diffing against the DDL does not read them as
# accidental. **Partial indexes:** SM-R12 scopes its temporal indexes with
# ``WHERE expired_at IS NULL``; the composite indexes below are unfiltered, because the builder's
# only predicate-carrying mechanism hardcodes ``unique=True``. Expressing it needs a new schema
# key, which belongs with the reader that benefits (SM-V1 #95 / aicoe-tracker#175).
# **CHECK constraints:** SM-R12 enforces the bi-temporal ordering in the database; there is no
# CHECK support anywhere in this shared DSL, so those invariants -- and the closed vocabularies on
# ``kind`` and the link tiers -- hold only at the ``egpt.db.schemas.memory`` row-model boundary.
# Until the builder grows one, **every writer must go through the row models**; raw SQL or a bulk
# loader can violate them silently. For the same reason a reader must resolve cross-tier
# references (``from_id``/``to_id``, ``source_episode_ids``, ``entity_ids``) by ``(tenant_id,
# id)`` -- the id alone is not unique across tenants.
# ---------------------------------------------------------------------------

_TENANT_SCOPED_COLUMNS: dict[str, Any] = {
    "id": {"type": String, "nullable": False},
    "tenant_id": {"type": String, "nullable": False},
    "user_id": {"type": String, "nullable": True},
    "agent_id": {"type": String, "nullable": True},
    "team_id": {"type": String, "nullable": True},
    "session_id": {"type": String, "nullable": True},
    "created_at": {"type": BigInteger, "nullable": False},
    "updated_at": {"type": BigInteger, "nullable": True},
    "metadata": {"type": JSONB, "nullable": False, "default": dict},
}

# SM-R10's four timestamps. ``superseded_by`` is a single id here; the episodic tier widens it
# to an array below, because an SM-R6 re-cut can merge or split one episode into several.
_BI_TEMPORAL_COLUMNS: dict[str, Any] = {
    "t_valid": {"type": BigInteger, "nullable": True},
    "t_invalid": {"type": BigInteger, "nullable": True},
    "expired_at": {"type": BigInteger, "nullable": True},
    "superseded_by": {"type": String, "nullable": True},
}

_EMBEDDABLE_COLUMNS: dict[str, Any] = {
    "embedding": {"type": JSONB, "nullable": True},
    "embedding_dim": {"type": Integer, "nullable": True},
    "embedding_version": {"type": String, "nullable": True},
}

# SM-R5's decay inputs. ``strength`` is absent by decision, not omission (ADR 0003 section 6).
_DECAYING_COLUMNS: dict[str, Any] = {
    "importance": {"type": Float, "nullable": False, "default": 0.5},
    "last_accessed_at": {"type": BigInteger, "nullable": True},
}

# The CHECK constraints SM-R12's DDL declares on every bi-temporal table. Enforced in the database
# rather than only in the row models, so a bulk loader or raw SQL cannot write a window no reader
# can interpret. The IS NULL guards are what let an open-ended (still true, still held) row pass.
_BI_TEMPORAL_CHECKS = [
    {"name": "tvalid_order", "sql": "t_valid IS NULL OR t_invalid IS NULL OR t_invalid >= t_valid"},
    {"name": "expiry_order", "sql": "expired_at IS NULL OR expired_at >= created_at"},
]

# SM-R12 scopes the fact and relation temporal indexes to current rows; the episodic one is
# unfiltered in that DDL and stays unfiltered here.
_CURRENT_BELIEF_TEMPORAL_INDEX = [{"columns": ["tenant_id", "t_valid", "t_invalid"], "where": "expired_at IS NULL"}]

_MEMORY_PRIMARY_KEY = ["tenant_id", "id"]

MEMORY_EPISODIC_TABLE_SCHEMA = {
    **_TENANT_SCOPED_COLUMNS,
    **_BI_TEMPORAL_COLUMNS,
    **_EMBEDDABLE_COLUMNS,
    **_DECAYING_COLUMNS,
    "content": {"type": Text, "nullable": False},
    "occurred_at": {"type": BigInteger, "nullable": True},
    "location": {"type": String, "nullable": True},
    "cause": {"type": Text, "nullable": True},
    "entity_ids": {"type": JSONB, "nullable": False, "default": list},
    "graph_node_id": {"type": String, "nullable": True},
    # SM-R6 episode identity / segmentation contract.
    "revision": {"type": Integer, "nullable": False, "default": 0},
    "supersedes": {"type": JSONB, "nullable": False, "default": list},
    "superseded_by": {"type": JSONB, "nullable": False, "default": list},
    "boundary_method": {"type": String, "nullable": True},
    "boundary_trigger": {"type": Float, "nullable": True},
    "boundary_confidence": {"type": Float, "nullable": True},
    "refined": {"type": Boolean, "nullable": False, "default": False},
    "segmenter_version": {"type": String, "nullable": True},
    "__check_constraints__": _BI_TEMPORAL_CHECKS,
    "__primary_key__": _MEMORY_PRIMARY_KEY,
    "__composite_indexes__": [
        {"name": "tenant_id_t_valid_t_invalid", "columns": ["tenant_id", "t_valid", "t_invalid"]},
    ],
}

MEMORY_SEMANTIC_FACT_TABLE_SCHEMA = {
    **_TENANT_SCOPED_COLUMNS,
    **_BI_TEMPORAL_COLUMNS,
    **_EMBEDDABLE_COLUMNS,
    **_DECAYING_COLUMNS,
    "content": {"type": Text, "nullable": False},
    "subject": {"type": String, "nullable": True},
    "predicate": {"type": String, "nullable": True},
    "object": {"type": String, "nullable": True},
    "confidence": {"type": Float, "nullable": False, "default": 1.0},
    "source_episode_ids": {"type": JSONB, "nullable": False, "default": list},
    "derived_by": {"type": String, "nullable": True},
    "entity_ids": {"type": JSONB, "nullable": False, "default": list},
    "__check_constraints__": _BI_TEMPORAL_CHECKS,
    "__partial_indexes__": _CURRENT_BELIEF_TEMPORAL_INDEX,
    "__primary_key__": _MEMORY_PRIMARY_KEY,
}

MEMORY_ENTITY_TABLE_SCHEMA = {
    **_TENANT_SCOPED_COLUMNS,
    **_EMBEDDABLE_COLUMNS,
    "entity_type": {"type": String, "nullable": False},
    "name": {"type": String, "nullable": True},
    "description": {"type": Text, "nullable": True},
    "properties": {"type": JSONB, "nullable": False, "default": dict},
    "__primary_key__": _MEMORY_PRIMARY_KEY,
}

MEMORY_RELATION_TABLE_SCHEMA = {
    **_TENANT_SCOPED_COLUMNS,
    **_BI_TEMPORAL_COLUMNS,
    "subject_entity_id": {"type": String, "nullable": False},
    "predicate": {"type": String, "nullable": False},
    "object_entity_id": {"type": String, "nullable": False},
    "confidence": {"type": Float, "nullable": False, "default": 1.0},
    "source_episode_ids": {"type": JSONB, "nullable": False, "default": list},
    "derived_by": {"type": String, "nullable": True},
    "__check_constraints__": _BI_TEMPORAL_CHECKS,
    "__primary_key__": _MEMORY_PRIMARY_KEY,
    # Both directions: traversal reads an edge from either endpoint.
    "__composite_indexes__": [
        {"name": "tenant_id_subject_entity_id", "columns": ["tenant_id", "subject_entity_id"]},
        {"name": "tenant_id_object_entity_id", "columns": ["tenant_id", "object_entity_id"]},
    ],
    "__partial_indexes__": _CURRENT_BELIEF_TEMPORAL_INDEX,
}

MEMORY_PROCEDURAL_TABLE_SCHEMA = {
    **_TENANT_SCOPED_COLUMNS,
    **_EMBEDDABLE_COLUMNS,
    "kind": {"type": String, "nullable": False, "default": "rule"},
    "title": {"type": String, "nullable": True},
    "content": {"type": Text, "nullable": False},
    "trigger": {"type": Text, "nullable": True},
    "tags": {"type": JSONB, "nullable": False, "default": list},
    "success_count": {"type": Integer, "nullable": True},
    "__primary_key__": _MEMORY_PRIMARY_KEY,
}

MEMORY_LINK_TABLE_SCHEMA = {
    **_TENANT_SCOPED_COLUMNS,
    **_BI_TEMPORAL_COLUMNS,
    "from_id": {"type": String, "nullable": False},
    "from_tier": {"type": String, "nullable": False},
    "to_id": {"type": String, "nullable": False},
    "to_tier": {"type": String, "nullable": False},
    "link_type": {"type": String, "nullable": False, "default": "relates_to"},
    "__check_constraints__": _BI_TEMPORAL_CHECKS,
    "__primary_key__": _MEMORY_PRIMARY_KEY,
    "__composite_indexes__": [
        {"name": "tenant_id_from_id", "columns": ["tenant_id", "from_id"]},
        {"name": "tenant_id_to_id", "columns": ["tenant_id", "to_id"]},
    ],
}


def get_table_schema_definition(
    table_type: str,
    traces_table_name: str = TABLE_PREFIX + "traces",
    db_schema: str = "agno",
    schedules_table_name: str = TABLE_PREFIX + "schedules",
) -> dict[str, Any]:
    """
    Get the expected schema definition for the given table.

    Args:
        table_type (str): The type of table to get the schema for.
        traces_table_name (str): The name of the traces table (used for spans foreign key).
        db_schema (str): The database schema name (used for spans foreign key).

    Returns:
        Dict[str, Any]: Dictionary containing column definitions for the table
    """
    # Handle tables with dynamic foreign key references
    if table_type == "spans":
        return _get_span_table_schema(traces_table_name, db_schema)
    if table_type == "schedule_runs":
        return _get_schedule_runs_table_schema(schedules_table_name, db_schema)

    schemas = {
        "sessions": SESSION_TABLE_SCHEMA,
        "evals": EVAL_TABLE_SCHEMA,
        "metrics": METRICS_TABLE_SCHEMA,
        "memories": MEMORY_TABLE_SCHEMA,
        "knowledge": KNOWLEDGE_TABLE_SCHEMA,
        "culture": CULTURAL_KNOWLEDGE_TABLE_SCHEMA,
        "versions": VERSIONS_TABLE_SCHEMA,
        "traces": TRACE_TABLE_SCHEMA,
        "components": COMPONENT_TABLE_SCHEMA,
        "component_configs": COMPONENT_CONFIGS_TABLE_SCHEMA,
        "component_links": COMPONENT_LINKS_TABLE_SCHEMA,
        "component_audit": COMPONENT_AUDIT_TABLE_SCHEMA,
        "learnings": LEARNINGS_TABLE_SCHEMA,
        "schedules": SCHEDULE_TABLE_SCHEMA,
        "approvals": APPROVAL_TABLE_SCHEMA,
        "auth_tokens": AUTH_TOKEN_TABLE_SCHEMA,
        "service_accounts": SERVICE_ACCOUNT_TABLE_SCHEMA,
        "memory_episodic": MEMORY_EPISODIC_TABLE_SCHEMA,
        "memory_semantic_fact": MEMORY_SEMANTIC_FACT_TABLE_SCHEMA,
        "memory_entity": MEMORY_ENTITY_TABLE_SCHEMA,
        "memory_relation": MEMORY_RELATION_TABLE_SCHEMA,
        "memory_procedural": MEMORY_PROCEDURAL_TABLE_SCHEMA,
        "memory_link": MEMORY_LINK_TABLE_SCHEMA,
    }

    schema = schemas.get(table_type, {})
    if not schema:
        raise ValueError(f"Unknown table type: {table_type}")

    return schema  # type: ignore[return-value]
