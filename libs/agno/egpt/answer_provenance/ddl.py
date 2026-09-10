"""Applying the answer-provenance table's schema (R7, G4).

One statement, idempotent (``CREATE TABLE IF NOT EXISTS``), so applying it is safe to repeat
and needs no version ledger -- the same reasoning ``egpt_retail/warehouse/ddl.py`` documents
for the warehouse, and for the same reason: ``MigrationManager`` covers the framework's own
curated table types (``egpt.db.table_names.TABLE_SUFFIXES``), this table is deliberately not
one of them, and tracker #178 hardened it specifically against stamping a table it never
migrated.
"""

from __future__ import annotations

import asyncio
from typing import Tuple

from egpt.db.table_names import DEFAULT_TABLE_PREFIX

try:
    from sqlalchemy.engine import Engine
except ImportError:
    raise ImportError("`sqlalchemy` not installed. Run `pip install sqlalchemy` or `pip install egpt[sql]`.")

__all__ = ["PROVENANCE_TABLE", "apply_provenance_ddl", "aapply_provenance_ddl"]

#: ``egpt_answer_provenance``. The ``egpt_`` prefix is the same
#: ``egpt.db.table_names.DEFAULT_TABLE_PREFIX`` every framework table uses (``egpt_sessions``,
#: ``egpt_components``, ...), signalling framework ownership even though this table sits
#: outside ``MigrationManager``'s own registry.
PROVENANCE_TABLE = f"{DEFAULT_TABLE_PREFIX}answer_provenance"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {PROVENANCE_TABLE} (
    run_id             TEXT PRIMARY KEY,
    use_case_id        TEXT,
    produced_at        TIMESTAMPTZ NOT NULL,
    semantic_snapshot  TEXT NOT NULL,
    vocabulary_version TEXT,
    skill              JSONB,
    model_tier         TEXT NOT NULL CHECK (model_tier IN ('none', 'small', 'frontier')),
    datasets_read      JSONB NOT NULL DEFAULT '[]'::jsonb,
    sql_executed       JSONB NOT NULL DEFAULT '[]'::jsonb,
    rows_sampled       JSONB,
    freshness          JSONB,
    retrieval_hits     JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
)
""".strip()


def apply_provenance_ddl(engine: Engine) -> Tuple[str, ...]:
    """Create the provenance table if it does not already exist. Returns the names applied.

    ``exec_driver_sql`` rather than ``text()``: the statement takes no bind parameters, and
    ``text()`` would try (and fail) to parse the JSONB default's ``'[]'::jsonb`` cast as a
    bind marker it must not touch -- the same reason ``warehouse/ddl.py`` uses it.
    """
    with engine.begin() as connection:
        connection.exec_driver_sql(_DDL)
    return (PROVENANCE_TABLE,)


async def aapply_provenance_ddl(engine: Engine) -> Tuple[str, ...]:
    """:func:`apply_provenance_ddl` off the event loop -- the engine is the synchronous one,
    so the work moves to a thread rather than pretending to be awaitable, the idiom
    ``DatabaseContextProvider.astatus`` already uses."""
    return await asyncio.to_thread(apply_provenance_ddl, engine)
