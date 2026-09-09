"""The load-run ledger: opening a load, closing it, and finding its baseline.

Every load gets a row in ``quality.load_run`` before a single file is read, and that row's
``load_id`` stamps every landing row, every silver row and every gate result the load
produces. It is what makes "which load put this here, and was that load allowed through"
answerable from the data rather than from a log.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

__all__ = [
    "LoadRun",
    "open_load_run",
    "aopen_load_run",
    "close_load_run",
    "aclose_load_run",
    "fail_load_run",
    "afail_load_run",
    "close_stale_load_runs",
    "previous_successful_load",
    "aprevious_successful_load",
    "ungated_rows",
    "LoadLock",
]


@dataclass(frozen=True)
class LoadRun:
    """One run of the landing-to-silver pipeline."""

    load_id: uuid.UUID
    trigger: str
    landing_root: str
    workflow_run_id: Optional[str] = None


def open_load_run(
    engine: Engine,
    *,
    trigger: str,
    landing_root: str,
    workflow_run_id: Optional[str] = None,
) -> LoadRun:
    """Record a load as started, before anything is read.

    Written first so that a load which crashes halfway still leaves evidence it happened.
    A load with a NULL ``finished_at`` is one that never completed, which is a different and
    more alarming state than one that completed and was blocked.

    Closes any earlier unfinished load first. A process killed mid-load runs no exception
    handler, so a load cannot always close itself; the next one does it instead, which is what
    stops ``'failed'`` being a verdict the schema allows and nothing ever writes.
    """
    close_stale_load_runs(engine)
    run = LoadRun(
        load_id=uuid.uuid4(),
        trigger=trigger,
        landing_root=landing_root,
        workflow_run_id=workflow_run_id,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO quality.load_run "
                "(load_id, trigger, landing_root, workflow_run_id) "
                "VALUES (:load_id, :trigger, :landing_root, :workflow_run_id)"
            ),
            {
                "load_id": str(run.load_id),
                "trigger": run.trigger,
                "landing_root": run.landing_root,
                "workflow_run_id": run.workflow_run_id,
            },
        )
    return run


async def aopen_load_run(
    engine: Engine,
    *,
    trigger: str,
    landing_root: str,
    workflow_run_id: Optional[str] = None,
) -> LoadRun:
    return await asyncio.to_thread(
        open_load_run,
        engine,
        trigger=trigger,
        landing_root=landing_root,
        workflow_run_id=workflow_run_id,
    )


def close_load_run(
    engine: Engine,
    load_id: uuid.UUID,
    *,
    verdict: str,
    reason: str,
    files_loaded: int,
    rows_landed: int,
) -> None:
    """Stamp the verdict and the reason. Only now is the load finished."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE quality.load_run SET finished_at = now(), verdict = :verdict, "
                "reason = :reason, files_loaded = :files_loaded, rows_landed = :rows_landed "
                "WHERE load_id = :load_id"
            ),
            {
                "load_id": str(load_id),
                "verdict": verdict,
                "reason": reason,
                "files_loaded": files_loaded,
                "rows_landed": rows_landed,
            },
        )


async def aclose_load_run(
    engine: Engine,
    load_id: uuid.UUID,
    *,
    verdict: str,
    reason: str,
    files_loaded: int,
    rows_landed: int,
) -> None:
    await asyncio.to_thread(
        close_load_run,
        engine,
        load_id,
        verdict=verdict,
        reason=reason,
        files_loaded=files_loaded,
        rows_landed=rows_landed,
    )


def previous_successful_load(engine: Engine, before: uuid.UUID) -> Optional[uuid.UUID]:
    """The most recent load that passed, excluding this one. ``None`` on the first load.

    Deliberately the last *passed* load rather than the last load of any kind. A blocked load
    is one whose numbers were rejected, so measuring against it would let a bad night become
    the new normal -- two undersized pulls in a row would compare small against small and
    both look fine.
    """
    with engine.begin() as connection:
        row = connection.execute(
            text(
                "SELECT load_id FROM quality.load_run "
                "WHERE verdict = 'passed' AND load_id <> :before "
                "AND finished_at IS NOT NULL "
                "ORDER BY finished_at DESC LIMIT 1"
            ),
            {"before": str(before)},
        ).first()
    return uuid.UUID(str(row[0])) if row else None


async def aprevious_successful_load(
    engine: Engine, before: uuid.UUID
) -> Optional[uuid.UUID]:
    return await asyncio.to_thread(previous_successful_load, engine, before)


def fail_load_run(engine: Engine, load_id: uuid.UUID, reason: str) -> None:
    """Close a load as failed. Called when a step raises, before the exception propagates."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE quality.load_run SET finished_at = now(), verdict = 'failed', "
                "reason = :reason WHERE load_id = :load_id AND verdict IS NULL"
            ),
            {"load_id": str(load_id), "reason": reason},
        )


async def afail_load_run(engine: Engine, load_id: uuid.UUID, reason: str) -> None:
    await asyncio.to_thread(fail_load_run, engine, load_id, reason)


def close_stale_load_runs(engine: Engine) -> int:
    """Mark every load that never finished as failed. Returns how many were closed.

    The safety net for a load that could not close itself -- an OOM kill, a pod eviction, a
    lost connection. Without it a crashed load keeps a NULL verdict for ever, which reads as
    "still running" rather than "died", and `'failed'` stays a verdict the CHECK constraint
    permits and no code path writes.
    """
    with engine.begin() as connection:
        result = connection.execute(
            text(
                "UPDATE quality.load_run SET finished_at = now(), verdict = 'failed', "
                "reason = COALESCE(reason, 'Did not finish. Closed by a later load; the "
                "process most likely died before it could record its own verdict.') "
                "WHERE finished_at IS NULL AND verdict IS NULL"
            )
        )
        return int(result.rowcount or 0)


def ungated_rows(engine: Engine, load_id: uuid.UUID) -> int:
    """Landing rows belonging to a load that never passed its gates, excluding this one.

    Conformance is a full recompute over all of landing, so rows a crashed or blocked load
    landed are folded into the next load's silver -- attributed to a load whose own gates
    never evaluated them. That is a real hole: a load's quality gates can be bypassed for rows
    it landed. Counting them here is what makes it visible rather than silent.
    """
    with engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM landing.raw_row AS r "
                    "JOIN quality.load_run AS l ON l.load_id = r.load_id "
                    "WHERE r.load_id <> :load_id "
                    "  AND (l.verdict IS DISTINCT FROM 'passed')"
                ),
                {"load_id": str(load_id)},
            ).scalar_one()
        )


class LoadLock:
    """A Postgres advisory lock held for one landing-to-silver run.

    Two overlapping loads do not corrupt anything -- every write is an upsert, and landing is
    keyed on ``(source_path, line_number)``. What they do corrupt is the *baseline*: each calls
    :func:`previous_successful_load` independently, so whichever finishes second computed its
    comparison before the first recorded its verdict, and both gates measured against a state
    that was already stale. Nothing in the output says two loads overlapped, so the result is
    a silently wrong gate rather than a visible conflict -- which is worse.

    Mirrors ``entrypoints/consolidator.py``'s guard, for the same reason and in the same shape:
    ``pg_try_advisory_lock`` on a **dedicated connection held for the run**. Session-scoped, so
    a process that dies releases it automatically rather than wedging every later load.

    The connection is opened straight to Postgres. Through a transaction-mode pooler the
    session would end with the first transaction and the lock would silently evaporate -- the
    warehouse URL points at the server directly today, and this is the reason it must keep
    doing so.
    """

    #: One key for the whole pipeline: any two loads contend, whatever they were asked to load.
    KEY = "egpt-retail:landing-to-silver"

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: Optional[Any] = None
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def acquire(self) -> bool:
        import psycopg

        self._conn = psycopg.connect(self._dsn, autocommit=True)
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (self.KEY,))
            row = cursor.fetchone()
        self._held = bool(row and row[0])
        if not self._held:
            self.release()
        return self._held

    def release(self) -> None:
        if self._conn is None:
            return
        try:
            if self._held:
                with self._conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s))", (self.KEY,)
                    )
        finally:
            self._held = False
            self._conn.close()
            self._conn = None
