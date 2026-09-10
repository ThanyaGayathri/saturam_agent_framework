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

#: Backs ``list_provenance_by_use_case`` (R11): a use-case-scoped playground reads every
#: record for one ``use_case_id`` "by reference, not search", which without an index is a
#: full-table scan on a column that carries no ordering guarantee of its own.
_USE_CASE_INDEX = f"{PROVENANCE_TABLE}_use_case_id_idx"

_DDL_STATEMENTS: Tuple[str, ...] = (
    f"""
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
    """.strip(),
    f"""
    CREATE INDEX IF NOT EXISTS {_USE_CASE_INDEX}
        ON {PROVENANCE_TABLE} (use_case_id, produced_at DESC)
        WHERE use_case_id IS NOT NULL
    """.strip(),
)


def apply_provenance_ddl(engine: Engine) -> Tuple[str, ...]:
    """Create the provenance table and its indexes if they do not already exist. Returns the
    names applied.

    ``exec_driver_sql`` rather than ``text()``: neither statement takes a bind parameter, and
    ``text()`` would try (and fail) to parse the JSONB default's ``'[]'::jsonb`` cast as a
    bind marker it must not touch -- the same reason ``warehouse/ddl.py`` uses it. Two
    statements, not one string with a semicolon: ``exec_driver_sql`` sends exactly what the
    driver is given, and not every driver splits a multi-statement string the same way.
    """
    with engine.begin() as connection:
        for statement in _DDL_STATEMENTS:
            connection.exec_driver_sql(statement)
    return (PROVENANCE_TABLE, _USE_CASE_INDEX)


async def aapply_provenance_ddl(engine: Engine) -> Tuple[str, ...]:
    """:func:`apply_provenance_ddl` off the event loop -- the engine is the synchronous one,
    so the work moves to a thread rather than pretending to be awaitable, the idiom
    ``DatabaseContextProvider.astatus`` already uses."""
    return await asyncio.to_thread(apply_provenance_ddl, engine)
