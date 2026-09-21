"""The rollback ledger: what a run wrote, and how to undo it (K15's Record rollback point).

One row per record a run created in a target system, with the reverse action the mapping
declared (``delete`` for an Order; a future field-level mapping would record the prior
values and ``restore``). ``record_rollback_point`` writes rows; ``revert_run`` reads a run's
rows back and applies each reverse action through the same Salesforce client the write used,
newest first, stamping ``reverted_at`` as it goes -- so a half-finished revert is visible as
exactly that.

Same idempotent-DDL treatment as ``egpt_retail.delivery`` (one statement, deliberately not
through ``MigrationManager``), on the control plane. When no control plane is configured --
the offline cookbook, the unit tests -- the executor keeps the ledger in the run's own
``additional_data`` and says so in its content; it never silently drops it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

__all__ = [
    "ROLLBACK_TABLE",
    "apply_rollback_ddl",
    "aapply_rollback_ddl",
    "write_rollback_points",
    "awrite_rollback_points",
    "read_rollback_points",
    "aread_rollback_points",
    "mark_reverted",
    "amark_reverted",
]

ROLLBACK_TABLE = "retail_rollback_points"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {ROLLBACK_TABLE} (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    use_case_id   TEXT,
    target        TEXT NOT NULL,
    object        TEXT NOT NULL,
    record_id     TEXT NOT NULL,
    action        TEXT NOT NULL,
    content       JSONB NOT NULL,
    reverted_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()

_INSERT = f"""
INSERT INTO {ROLLBACK_TABLE}
    (id, run_id, use_case_id, target, object, record_id, action, content)
VALUES
    (:id, :run_id, :use_case_id, :target, :object, :record_id, :action, CAST(:content AS jsonb))
ON CONFLICT (id) DO NOTHING
"""

_SELECT = f"""
SELECT id, run_id, use_case_id, target, object, record_id, action, content, reverted_at, created_at
FROM {ROLLBACK_TABLE} WHERE run_id = :run_id ORDER BY created_at DESC, id DESC
"""

_REVERT = f"UPDATE {ROLLBACK_TABLE} SET reverted_at = :when WHERE id = :id"


def apply_rollback_ddl(engine: Engine) -> Tuple[str, ...]:
    with engine.begin() as connection:
        connection.exec_driver_sql(_DDL)
    return (ROLLBACK_TABLE,)


async def aapply_rollback_ddl(engine: Engine) -> Tuple[str, ...]:
    return await asyncio.to_thread(apply_rollback_ddl, engine)


def write_rollback_points(engine: Engine, rows: Sequence[Mapping[str, Any]]) -> int:
    """Insert the rows (each: id, run_id, use_case_id, target, object, record_id, action,
    content). Returns how many were written."""
    if not rows:
        return 0
    with engine.begin() as connection:
        for row in rows:
            connection.execute(
                text(_INSERT),
                {
                    "id": row["id"],
                    "run_id": row["run_id"],
                    "use_case_id": row.get("use_case_id"),
                    "target": row["target"],
                    "object": row["object"],
                    "record_id": row["record_id"],
                    "action": row["action"],
                    "content": json.dumps(row.get("content") or {}, default=str),
                },
            )
    return len(rows)


async def awrite_rollback_points(
    engine: Engine, rows: Sequence[Mapping[str, Any]]
) -> int:
    return await asyncio.to_thread(write_rollback_points, engine, rows)


def read_rollback_points(engine: Engine, run_id: str) -> List[Dict[str, Any]]:
    with engine.connect() as connection:
        result = connection.execute(text(_SELECT), {"run_id": run_id})
        return [dict(row._mapping) for row in result]


async def aread_rollback_points(engine: Engine, run_id: str) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(read_rollback_points, engine, run_id)


def mark_reverted(
    engine: Engine, point_id: str, when: Optional[datetime] = None
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(_REVERT), {"id": point_id, "when": when or datetime.now(timezone.utc)}
        )


async def amark_reverted(
    engine: Engine, point_id: str, when: Optional[datetime] = None
) -> None:
    await asyncio.to_thread(mark_reverted, engine, point_id, when)
