"""The suggestion-run table: one row per ``POST /kpis/suggestions`` (#254).

A KPI suggestion run is two frontier-tier model calls back to back and can take minutes.
``api/routers/kpis.py`` used to do that inside the request; now it records a row here,
returns ``202`` with the row, and the frontend polls ``GET /kpis/suggestions/{run_id}`` until
the row is terminal. The row is in Postgres, not process memory, because the api serves from
more than one instance (two replicas under Helm, scale-from-zero on the Functions plan) and
the poll that follows a ``POST`` is not guaranteed to land on the instance that accepted it.

What the row holds when it succeeds is the full ``KpiSuggestionList`` the synchronous route
would have returned, *including* ``proposed_sql`` -- the ``kpis:write`` gate on candidate SQL
is applied when the row is read, per caller, exactly as the synchronous route applies it, so
one run serves a viewer and an editor differently without running twice.

Same self-provisioning posture as ``kpi/store.py`` and ``semantic/proposals.py`` (and the
same schema): created by the code that owns it, under the shared advisory lock, at boot.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

__all__ = [
    "RUN_SCHEMA",
    "SuggestionRunRecord",
    "SuggestionRunStatus",
    "SuggestionRunStore",
]

#: The control-plane schema the registry (K11) and proposals (K7) already live in.
RUN_SCHEMA = "semantic"


class SuggestionRunStatus(str, Enum):
    """Where a run is. Spelled like the contract's ``HarvestStatus`` -- the same four words
    for the same shape, rather than a third vocabulary for "a job that is or isn't done"."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_OPEN = (SuggestionRunStatus.QUEUED.value, SuggestionRunStatus.RUNNING.value)

#: What a run that outlived its window reports. Worded for the person polling: the run is
#: gone, not slow, and the remedy is a new one.
LOST_RUN_ERROR = (
    "The suggestion run was lost before it finished (the serving instance was likely "
    "restarted); start a new run."
)


@dataclass(frozen=True)
class SuggestionRunRecord:
    """One row. ``result`` is the ``KpiSuggestionList`` as plain JSON (``model_dump(mode=
    "json")``), set only once ``status`` is ``succeeded``; ``error`` only once ``failed``."""

    identifier: str
    status: SuggestionRunStatus
    business_area: Optional[str]
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result: Optional[Mapping[str, Any]] = None
    error: Optional[str] = None

    @property
    def terminal(self) -> bool:
        return self.status in (
            SuggestionRunStatus.SUCCEEDED,
            SuggestionRunStatus.FAILED,
        )


def _ddl(schema: str) -> Tuple[str, ...]:
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""CREATE TABLE IF NOT EXISTS {schema}.kpi_suggestion_run (
        id            text PRIMARY KEY,
        status        text        NOT NULL DEFAULT 'queued',
        business_area text,
        created_at    timestamptz NOT NULL DEFAULT now(),
        started_at    timestamptz,
        finished_at   timestamptz,
        result        jsonb,
        error         text,
        CONSTRAINT kpi_suggestion_run_status_valid
            CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
        CONSTRAINT kpi_suggestion_run_outcome_matches_status
            CHECK ((status = 'succeeded') = (result IS NOT NULL)
               AND (status = 'failed') = (error IS NOT NULL))
    )""",
        f"""CREATE INDEX IF NOT EXISTS kpi_suggestion_run_open
        ON {schema}.kpi_suggestion_run (status, created_at)""",
        f"""COMMENT ON TABLE {schema}.kpi_suggestion_run IS
        'One row per POST /kpis/suggestions (#254). Polled by GET /kpis/suggestions/{{id}}; '
        'result is the KpiSuggestionList once succeeded, error the reason once failed.'""",
    )


_COLUMNS = (
    "id, status, business_area, created_at, started_at, finished_at, result, error"
)


class SuggestionRunStore:
    """The suggestion-run table. The connection is injected; the caller owns its lifetime.

    Every method opens no connection of its own and holds no state between calls, so the
    router can open a pooled connection, do one thing, and release it -- never holding one
    across the model calls the run itself makes (the discipline PR #242 recorded for the
    synchronous route).
    """

    def __init__(self, connection: Any, schema: str = RUN_SCHEMA) -> None:
        self._connection = connection
        self._schema = schema

    # --- schema ------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the table if absent. Idempotent, and safe from several processes -- the
        same transaction-scoped advisory lock ``KpiRegistryStore``/``ProposalStore`` take."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("egpt_retail.semantic.ddl",),
            )
            for statement in _ddl(self._schema):
                cursor.execute(statement)
        self._connection.commit()

    async def aensure_schema(self) -> None:
        """Async variant."""
        await asyncio.to_thread(self.ensure_schema)

    # --- writing -----------------------------------------------------------------------

    def create(self, business_area: Optional[str]) -> SuggestionRunRecord:
        """Record a new, queued run and return it."""
        identifier = f"sr_{uuid.uuid4().hex}"
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.kpi_suggestion_run (id, business_area)
                    VALUES (%s, %s)
                    RETURNING {_COLUMNS}""",
                (identifier, business_area),
            )
            row = cursor.fetchone()
        self._connection.commit()
        return _record(row)

    async def acreate(self, business_area: Optional[str]) -> SuggestionRunRecord:
        """Async variant."""
        return await asyncio.to_thread(self.create, business_area)

    def mark_running(self, identifier: str) -> None:
        """``queued`` -> ``running``. A no-op on a row that has already moved on."""
        self._update(
            "status = 'running', started_at = now()",
            (),
            identifier,
            only_from=(SuggestionRunStatus.QUEUED.value,),
        )

    async def amark_running(self, identifier: str) -> None:
        """Async variant."""
        await asyncio.to_thread(self.mark_running, identifier)

    def mark_succeeded(self, identifier: str, result: Mapping[str, Any]) -> None:
        """Terminal: the run's ``KpiSuggestionList`` as JSON. A no-op if the row was already
        closed -- by the stale guard, for one, if the run outlived its window."""
        self._update(
            "status = 'succeeded', result = %s, finished_at = now()",
            (json.dumps(dict(result)),),
            identifier,
            only_from=_OPEN,
        )

    async def amark_succeeded(self, identifier: str, result: Mapping[str, Any]) -> None:
        """Async variant."""
        await asyncio.to_thread(self.mark_succeeded, identifier, result)

    def mark_failed(self, identifier: str, error: str) -> None:
        """Terminal: why the run produced nothing. Same no-op rule as ``mark_succeeded``."""
        self._update(
            "status = 'failed', error = %s, finished_at = now()",
            (error,),
            identifier,
            only_from=_OPEN,
        )

    async def amark_failed(self, identifier: str, error: str) -> None:
        """Async variant."""
        await asyncio.to_thread(self.mark_failed, identifier, error)

    # --- reading -----------------------------------------------------------------------

    def get(
        self, identifier: str, *, stale_after: Optional[timedelta] = None
    ) -> Optional[SuggestionRunRecord]:
        """One run by id, or ``None``.

        ``stale_after`` is the guard against a run whose executor is gone: the run executes
        as a task in the api process that accepted the ``POST``, and if that instance is
        recycled mid-run nothing would ever close the row. A ``queued``/``running`` row
        older than ``stale_after`` is therefore closed here, as ``failed`` with
        :data:`LOST_RUN_ERROR`, and the closed row returned -- so the poller sees an outcome
        instead of a spinner that never ends. The router passes its timeout plus a grace
        period, so a run that is merely slow (still inside its own bound) is never touched.
        """
        if stale_after is not None:
            self._update(
                "status = 'failed', error = %s, finished_at = now()",
                (LOST_RUN_ERROR,),
                identifier,
                only_from=_OPEN,
                extra_where="AND created_at < %s",
                extra_params=(datetime.now(timezone.utc) - stale_after,),
            )
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM {self._schema}.kpi_suggestion_run WHERE id = %s",
                (identifier,),
            )
            row = cursor.fetchone()
        return _record(row) if row is not None else None

    async def aget(
        self, identifier: str, *, stale_after: Optional[timedelta] = None
    ) -> Optional[SuggestionRunRecord]:
        """Async variant."""
        return await asyncio.to_thread(self.get, identifier, stale_after=stale_after)

    # --- internals ---------------------------------------------------------------------

    def _update(
        self,
        assignment: str,
        params: Tuple[Any, ...],
        identifier: str,
        *,
        only_from: Tuple[str, ...],
        extra_where: str = "",
        extra_params: Tuple[Any, ...] = (),
    ) -> None:
        """A state transition guarded by the states it may leave from, so two writers (the
        executor finishing, the stale guard closing) cannot both claim the same row."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.kpi_suggestion_run
                    SET {assignment}
                    WHERE id = %s AND status = ANY(%s) {extra_where}""",
                (*params, identifier, list(only_from), *extra_params),
            )
        self._connection.commit()


def _record(row: Any) -> SuggestionRunRecord:
    (
        identifier,
        status,
        business_area,
        created_at,
        started_at,
        finished_at,
        result,
        error,
    ) = row
    if isinstance(result, str):  # a driver that hands jsonb back undecoded
        result = json.loads(result)
    return SuggestionRunRecord(
        identifier=identifier,
        status=SuggestionRunStatus(status),
        business_area=business_area,
        created_at=created_at,
        started_at=started_at,
        finished_at=finished_at,
        result=result,
        error=error,
    )
