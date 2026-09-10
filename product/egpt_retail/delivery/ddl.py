"""Applying the delivery-record table's schema (R7, G4).

Same reasoning as ``egpt.answer_provenance.ddl`` and ``egpt_retail.warehouse.ddl``: one
idempotent statement, deliberately not routed through ``MigrationManager``.
"""

from __future__ import annotations

import asyncio
from typing import Tuple

from sqlalchemy.engine import Engine

__all__ = ["DELIVERY_TABLE", "apply_delivery_ddl", "aapply_delivery_ddl"]

#: Not ``egpt_``-prefixed, unlike the provenance table: this is retail-specific storage, not a
#: framework primitive (docs/plan/delivery-plan.md:98 -- provenance is the one framework
#: exception in R7's otherwise-product executor library).
DELIVERY_TABLE = "retail_deliveries"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {DELIVERY_TABLE} (
    id            TEXT PRIMARY KEY,
    run_id        TEXT,
    use_case_id   TEXT,
    channel       TEXT NOT NULL DEFAULT 'in_app',
    dry_run       BOOLEAN NOT NULL DEFAULT false,
    content       JSONB NOT NULL,
    delivered_at  TIMESTAMPTZ,       -- NULL exactly when dry_run is true: nothing was sent
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()


def apply_delivery_ddl(engine: Engine) -> Tuple[str, ...]:
    """Create the delivery table if it does not already exist. Returns the names applied."""
    with engine.begin() as connection:
        connection.exec_driver_sql(_DDL)
    return (DELIVERY_TABLE,)


async def aapply_delivery_ddl(engine: Engine) -> Tuple[str, ...]:
    """:func:`apply_delivery_ddl` off the event loop."""
    return await asyncio.to_thread(apply_delivery_ddl, engine)
