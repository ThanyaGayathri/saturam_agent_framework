"""Applying the alert-storage schema (R12, G7).

Same reasoning as ``egpt_retail.delivery.ddl`` and ``egpt_retail.warehouse.ddl``: one
idempotent statement per table, deliberately not routed through ``MigrationManager`` --
neither table is one of ``MigrationManager._TABLE_TYPE_TO_ATTR``'s curated framework types,
and per ``entrypoints/migrate.py``'s own note that should stay true (tracker #178).

Two tables, not one, because the contract splits them (``api/openapi/CHANGELOG.md``, "alerts.js
is split into definitions and events"): a definition is a rule a person authors and edits; an
event is one firing of it, frozen at the moment it fired. "Editing never changes an event it
already fired" (the contract's own ``updateAlertDefinition`` summary) is why the two are
separate tables rather than one row with a mutable "last fired" blob -- an edit to
``alert_definitions`` must never rewrite history sitting in ``alert_events``.
"""

from __future__ import annotations

import asyncio
from typing import Tuple

from sqlalchemy.engine import Engine

__all__ = [
    "ALERT_DEFINITIONS_TABLE",
    "ALERT_EVENTS_TABLE",
    "apply_alerts_ddl",
    "aapply_alerts_ddl",
]

#: Not ``egpt_``-prefixed, like ``retail_deliveries``: retail-specific storage, not a framework
#: primitive.
ALERT_DEFINITIONS_TABLE = "alert_definitions"
ALERT_EVENTS_TABLE = "alert_events"

_DEFINITIONS_DDL = f"""
CREATE TABLE IF NOT EXISTS {ALERT_DEFINITIONS_TABLE} (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    kpi_identifier       TEXT NOT NULL,
    operator             TEXT NOT NULL,
    threshold            NUMERIC NOT NULL,
    time_window          JSONB,
    scope                JSONB,
    severity             TEXT NOT NULL,
    recipients           JSONB NOT NULL DEFAULT '[]'::jsonb,
    recommended_actions  JSONB NOT NULL DEFAULT '[]'::jsonb,
    enabled              BOOLEAN NOT NULL DEFAULT true,
    use_case_id          TEXT,
    process_id           TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_evaluated_at    TIMESTAMPTZ,
    last_fired_at        TIMESTAMPTZ
)
""".strip()

#: One row per KPI a definition targets, so ``evaluate_alerts`` (R12) can find every enabled
#: definition for a given KPI without a table scan on every nightly pass.
_DEFINITIONS_INDEX_DDL = (
    f"CREATE INDEX IF NOT EXISTS {ALERT_DEFINITIONS_TABLE}_kpi_identifier_idx "
    f"ON {ALERT_DEFINITIONS_TABLE} (kpi_identifier)"
)

_EVENTS_DDL = f"""
CREATE TABLE IF NOT EXISTS {ALERT_EVENTS_TABLE} (
    id                   TEXT PRIMARY KEY,
    alert_id             TEXT NOT NULL,
    alert_name           TEXT,
    severity             TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'new',
    title                TEXT NOT NULL,
    summary              TEXT NOT NULL,
    business_area_id     TEXT,
    process_id           TEXT,
    use_case_id          TEXT,
    kpi                  JSONB NOT NULL,
    rule                 TEXT,
    recommended_actions  JSONB NOT NULL DEFAULT '[]'::jsonb,
    fired_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at      TIMESTAMPTZ,
    resolved_at          TIMESTAMPTZ,
    snooze_until         TIMESTAMPTZ,
    run_id               TEXT,
    provenance_ref       TEXT,
    as_of                TEXT
)
""".strip()

# For a table created before `as_of` existed. Idempotent, like the rest.
_EVENTS_ADD_AS_OF_DDL = (
    f"ALTER TABLE {ALERT_EVENTS_TABLE} ADD COLUMN IF NOT EXISTS as_of TEXT"
)

_EVENTS_ALERT_ID_INDEX_DDL = (
    f"CREATE INDEX IF NOT EXISTS {ALERT_EVENTS_TABLE}_alert_id_idx "
    f"ON {ALERT_EVENTS_TABLE} (alert_id)"
)

#: One fire per definition per evaluation window. ``as_of`` is the KPI's own business-date
#: reading the firing row's value came from (``evaluate_alert_condition``'s own content),
#: never ``fired_at`` -- ``fired_at`` is wall-clock and would let a retried or concurrent
#: ``evaluate_alerts`` pass for the *same* window insert a second row and page recipients
#: twice. Postgres never treats two NULLs as equal, so an event with no ``as_of`` (no KPI
#: window to key on at all) is simply never deduplicated against another -- the same "refuse
#: to guess" posture ``executors/alerts.py``'s ``_select_row`` takes, rather than a wrong
#: guess. A ``UNIQUE`` index rather than a table constraint, so it can be added
#: ``IF NOT EXISTS`` the same idempotent way as everything else here.
_EVENTS_ALERT_ID_AS_OF_UNIQUE_DDL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {ALERT_EVENTS_TABLE}_alert_id_as_of_uidx "
    f"ON {ALERT_EVENTS_TABLE} (alert_id, as_of)"
)


def apply_alerts_ddl(engine: Engine) -> Tuple[str, ...]:
    """Create both alert tables (and their indexes) if absent. Returns the names applied."""
    with engine.begin() as connection:
        connection.exec_driver_sql(_DEFINITIONS_DDL)
        connection.exec_driver_sql(_DEFINITIONS_INDEX_DDL)
        connection.exec_driver_sql(_EVENTS_DDL)
        connection.exec_driver_sql(_EVENTS_ADD_AS_OF_DDL)
        connection.exec_driver_sql(_EVENTS_ALERT_ID_INDEX_DDL)
        connection.exec_driver_sql(_EVENTS_ALERT_ID_AS_OF_UNIQUE_DDL)
    return (ALERT_DEFINITIONS_TABLE, ALERT_EVENTS_TABLE)


async def aapply_alerts_ddl(engine: Engine) -> Tuple[str, ...]:
    """:func:`apply_alerts_ddl` off the event loop."""
    return await asyncio.to_thread(apply_alerts_ddl, engine)
