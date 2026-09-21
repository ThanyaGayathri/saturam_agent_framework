"""The use-case catalog and run-history tables (K16).

Two tables on the control plane, same idempotent-DDL treatment as ``egpt_retail.delivery``
and ``egpt_retail.documents.rollback`` -- one statement each, deliberately not through
``MigrationManager``:

* ``retail_use_cases`` -- the catalog row the contract's ``UseCase`` renders, plus the three
  artifacts a use case is: the builder ``draft``, the ``spec`` (shape, template, parameters)
  and the ``compiled`` workflow the orchestration compiler produced at activation. Stored as
  JSON because each is a contract shape of its own and the catalog reads them whole.
* ``retail_use_case_runs`` -- one row per run, in the contract's ``UseCaseRun`` shape, with
  the run's own ``result`` (the batch the last executor returned) so the interface can show
  what was created and what was held without re-running anything.
"""

from __future__ import annotations

import asyncio
from typing import Tuple

from sqlalchemy.engine import Engine

__all__ = [
    "USE_CASES_TABLE",
    "RUNS_TABLE",
    "CASES_TABLE",
    "apply_use_case_ddl",
    "aapply_use_case_ddl",
]

USE_CASES_TABLE = "retail_use_cases"
RUNS_TABLE = "retail_use_case_runs"
#: K19: golden cases -- a document the person supplied or the platform generated, with the
#: outcome and values they expect, re-run whenever the use case changes.
CASES_TABLE = "retail_use_case_cases"

_DDL = (
    f"""
CREATE TABLE IF NOT EXISTS {USE_CASES_TABLE} (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    description           TEXT NOT NULL DEFAULT '',
    business_area         TEXT NOT NULL DEFAULT 'sales',
    status                TEXT NOT NULL DEFAULT 'draft',
    source                TEXT NOT NULL DEFAULT 'user',
    level                 INTEGER NOT NULL DEFAULT 1,
    autonomy              TEXT NOT NULL DEFAULT 'copilot',
    tags                  JSONB NOT NULL DEFAULT '[]'::jsonb,
    version               INTEGER NOT NULL DEFAULT 0,
    schedule              TEXT,
    workflow_component_id TEXT,
    draft                 JSONB,
    spec                  JSONB,
    compiled              JSONB,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip(),
    f"""
CREATE TABLE IF NOT EXISTS {RUNS_TABLE} (
    id                TEXT PRIMARY KEY,
    use_case_id       TEXT NOT NULL,
    use_case_version  INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'queued',
    trigger           TEXT NOT NULL DEFAULT 'manual',
    mode              TEXT NOT NULL DEFAULT 'live',
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    framework_run_id  TEXT,
    output_ref        TEXT,
    provenance_ref    TEXT,
    summary           TEXT NOT NULL DEFAULT '',
    blocked_reason    TEXT,
    result            JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip(),
    f"CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_use_case_idx ON {RUNS_TABLE} (use_case_id, started_at DESC)",
    # K19: what a rehearsal was asked to run over (source, message ids, case ids).
    f"ALTER TABLE {RUNS_TABLE} ADD COLUMN IF NOT EXISTS request JSONB",
    f"""
CREATE TABLE IF NOT EXISTS {CASES_TABLE} (
    id            TEXT PRIMARY KEY,
    use_case_id   TEXT NOT NULL,
    label         TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'uploaded',
    sender        TEXT NOT NULL DEFAULT '',
    subject       TEXT NOT NULL DEFAULT '',
    filename      TEXT NOT NULL DEFAULT 'document.pdf',
    document_b64  TEXT NOT NULL,
    expected      JSONB,
    last_run_id   TEXT,
    last_status   TEXT NOT NULL DEFAULT 'untested',
    last_result   JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip(),
    f"CREATE INDEX IF NOT EXISTS {CASES_TABLE}_use_case_idx ON {CASES_TABLE} (use_case_id, created_at)",
)


def apply_use_case_ddl(engine: Engine) -> Tuple[str, ...]:
    """Create both tables if they do not exist. Returns the names applied."""
    with engine.begin() as connection:
        for statement in _DDL:
            connection.exec_driver_sql(statement)
    return (USE_CASES_TABLE, RUNS_TABLE, CASES_TABLE)


async def aapply_use_case_ddl(engine: Engine) -> Tuple[str, ...]:
    return await asyncio.to_thread(apply_use_case_ddl, engine)
