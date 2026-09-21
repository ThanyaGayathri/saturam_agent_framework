"""The recommendations table (K26), same idempotent-DDL treatment as the use-case tables."""

from __future__ import annotations

import asyncio
from typing import Tuple

from sqlalchemy.engine import Engine

__all__ = [
    "RECOMMENDATIONS_TABLE",
    "apply_recommendations_ddl",
    "aapply_recommendations_ddl",
]

RECOMMENDATIONS_TABLE = "retail_recommendations"

_DDL = (
    f"""
CREATE TABLE IF NOT EXISTS {RECOMMENDATIONS_TABLE} (
    id                  TEXT PRIMARY KEY,
    use_case_id         TEXT NOT NULL,
    run_id              TEXT,
    kind                TEXT NOT NULL DEFAULT 'recommendation',
    subject_type        TEXT NOT NULL DEFAULT 'quote',
    subject_id          TEXT NOT NULL,
    subject_label       TEXT NOT NULL DEFAULT '',
    account_id          TEXT,
    account_label       TEXT NOT NULL DEFAULT '',
    owner               TEXT NOT NULL DEFAULT '',
    risk_score          DOUBLE PRECISION NOT NULL DEFAULT 0,
    risk_level          TEXT NOT NULL DEFAULT 'low',
    horizon_days        INTEGER,
    model_version       TEXT,
    drivers             JSONB NOT NULL DEFAULT '[]'::jsonb,
    action              JSONB,
    value_at_stake      DOUBLE PRECISION NOT NULL DEFAULT 0,
    value_unit          TEXT NOT NULL DEFAULT 'USD',
    context             JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    status              TEXT NOT NULL DEFAULT 'open',
    status_note         TEXT NOT NULL DEFAULT '',
    status_by           TEXT NOT NULL DEFAULT '',
    status_at           TIMESTAMPTZ,
    outcome             TEXT,
    outcome_at          TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip(),
    f"CREATE INDEX IF NOT EXISTS {RECOMMENDATIONS_TABLE}_use_case_idx ON {RECOMMENDATIONS_TABLE} (use_case_id, status, created_at DESC)",
    f"CREATE INDEX IF NOT EXISTS {RECOMMENDATIONS_TABLE}_subject_idx ON {RECOMMENDATIONS_TABLE} (use_case_id, subject_type, subject_id)",
)


def apply_recommendations_ddl(engine: Engine) -> Tuple[str, ...]:
    with engine.begin() as connection:
        for statement in _DDL:
            connection.exec_driver_sql(statement)
    return (RECOMMENDATIONS_TABLE,)


async def aapply_recommendations_ddl(engine: Engine) -> Tuple[str, ...]:
    return await asyncio.to_thread(apply_recommendations_ddl, engine)
