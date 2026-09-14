"""The dashboard-suggestion-run table: one row per ``POST /dashboards/suggestions``.

Mirrors :mod:`egpt_retail.suggestions.runs` (#254's ``kpi_suggestion_run``) column for column,
with ``purpose`` (free text) standing in for ``business_area``: a dashboard suggestion run is
one frontier-tier model call and can take a while, so the row is recorded, ``202`` is returned
with it, and the frontend polls ``GET /dashboards/suggestions/{run_id}`` until the row is
terminal -- the same reason KPI suggestion runs left the request in the first place. The row is
in Postgres, not process memory, for the same reason: the api serves from more than one
instance, and the poll that follows a ``POST`` is not guaranteed to land where it was accepted.

Same self-provisioning posture as ``kpi_suggestion_run`` and the stores it was itself modelled
on: created by the code that owns it, under the shared advisory lock, at boot.
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
    "DashboardSuggestionRunRecord",
    "DashboardSuggestionRunStatus",
    "DashboardSuggestionRunStore",
]

#: The same control-plane schema the KPI suggestion-run table lives in.
RUN_SCHEMA = "semantic"


class DashboardSuggestionRunStatus(str, Enum):
    """Where a run is. Spelled like ``SuggestionRunStatus``/``HarvestStatus`` -- the same four
    words for the same shape, rather than a third vocabulary."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_OPEN = (
    DashboardSuggestionRunStatus.QUEUED.value,
    DashboardSuggestionRunStatus.RUNNING.value,
)

#: What a run that outlived its window reports. Worded for the person polling: the run is
#: gone, not slow, and the remedy is a new one.
LOST_RUN_ERROR = (
    "The suggestion run was lost before it finished (the serving instance was likely "
    "restarted); start a new run."
)


@dataclass(frozen=True)
class DashboardSuggestionRunRecord:
    """One row. ``result`` is the ``DashboardSuggestion`` as plain JSON (``model_dump(mode=
    "json")``), set only once ``status`` is ``succeeded``; ``error`` only once ``failed``."""

    identifier: str
    status: DashboardSuggestionRunStatus
    purpose: str
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result: Optional[Mapping[str, Any]] = None
    error: Optional[str] = None

    @property
    def terminal(self) -> bool:
        return self.status in (
            DashboardSuggestionRunStatus.SUCCEEDED,
            DashboardSuggestionRunStatus.FAILED,
        )


def _ddl(schema: str) -> Tuple[str, ...]:
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""CREATE TABLE IF NOT EXISTS {schema}.dashboard_suggestion_run (
        id            text PRIMARY KEY,
        status        text        NOT NULL DEFAULT 'queued',
        purpose       text        NOT NULL,
        created_at    timestamptz NOT NULL DEFAULT now(),
        started_at    timestamptz,
        finished_at   timestamptz,
        result        jsonb,
        error         text,
        CONSTRAINT dashboard_suggestion_run_status_valid
            CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
        CONSTRAINT dashboard_suggestion_run_outcome_matches_status
            CHECK ((status = 'succeeded') = (result IS NOT NULL)
               AND (status = 'failed') = (error IS NOT NULL))
    )""",
        f"""CREATE INDEX IF NOT EXISTS dashboard_suggestion_run_open
        ON {schema}.dashboard_suggestion_run (status, created_at)""",
        f"""COMMENT ON TABLE {schema}.dashboard_suggestion_run IS
        'One row per POST /dashboards/suggestions. Polled by GET '
        '/dashboards/suggestions/{{id}}; result is the DashboardSuggestion once succeeded, '
        'error the reason once failed.'""",
    )


_COLUMNS = "id, status, purpose, created_at, started_at, finished_at, result, error"


class DashboardSuggestionRunStore:
    """The dashboard-suggestion-run table. The connection is injected; the caller owns its
    lifetime -- see :class:`egpt_retail.suggestions.runs.SuggestionRunStore`'s own docstring,
    which this mirrors exactly."""

    def __init__(self, connection: Any, schema: str = RUN_SCHEMA) -> None:
        self._connection = connection
        self._schema = schema

    # --- schema ------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the table if absent. Idempotent, and safe from several processes -- the
        same transaction-scoped advisory lock the KPI suggestion-run table's DDL takes."""
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

    def create(self, purpose: str) -> DashboardSuggestionRunRecord:
        """Record a new, queued run and return it."""
        identifier = f"dsr_{uuid.uuid4().hex}"
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.dashboard_suggestion_run (id, purpose)
                    VALUES (%s, %s)
                    RETURNING {_COLUMNS}""",
                (identifier, purpose),
            )
            row = cursor.fetchone()
        self._connection.commit()
        return _record(row)

    async def acreate(self, purpose: str) -> DashboardSuggestionRunRecord:
        """Async variant."""
        return await asyncio.to_thread(self.create, purpose)

    def mark_running(self, identifier: str) -> None:
        """``queued`` -> ``running``. A no-op on a row that has already moved on."""
        self._update(
            "status = 'running', started_at = now()",
            (),
            identifier,
            only_from=(DashboardSuggestionRunStatus.QUEUED.value,),
        )

    async def amark_running(self, identifier: str) -> None:
        """Async variant."""
        await asyncio.to_thread(self.mark_running, identifier)

    def mark_succeeded(self, identifier: str, result: Mapping[str, Any]) -> None:
        """Terminal: the run's ``DashboardSuggestion`` as JSON. A no-op if the row was already
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
    ) -> Optional[DashboardSuggestionRunRecord]:
        """One run by id, or ``None``. ``stale_after`` closes a stuck ``queued``/``running``
        row as ``failed`` before reading it back -- see ``SuggestionRunStore.get``'s docstring
        for the full reasoning, identical here."""
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
                f"SELECT {_COLUMNS} FROM {self._schema}.dashboard_suggestion_run WHERE id = %s",
                (identifier,),
            )
            row = cursor.fetchone()
        return _record(row) if row is not None else None

    async def aget(
        self, identifier: str, *, stale_after: Optional[timedelta] = None
    ) -> Optional[DashboardSuggestionRunRecord]:
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
                f"""UPDATE {self._schema}.dashboard_suggestion_run
                    SET {assignment}
                    WHERE id = %s AND status = ANY(%s) {extra_where}""",
                (*params, identifier, list(only_from), *extra_params),
            )
        self._connection.commit()


def _record(row: Any) -> DashboardSuggestionRunRecord:
    (
        identifier,
        status,
        purpose,
        created_at,
        started_at,
        finished_at,
        result,
        error,
    ) = row
    if isinstance(result, str):  # a driver that hands jsonb back undecoded
        result = json.loads(result)
    return DashboardSuggestionRunRecord(
        identifier=identifier,
        status=DashboardSuggestionRunStatus(status),
        purpose=purpose,
        created_at=created_at,
        started_at=started_at,
        finished_at=finished_at,
        result=result,
        error=error,
    )
