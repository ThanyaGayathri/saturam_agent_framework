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

Two more things the row carries for the person waiting on it, both read by the poll
(``KpiSuggestionRun.phase`` / ``.typical_duration_seconds``): which of the pass's three
stages a ``running`` row is in, written by the executor as the pipeline reports them, and a
median of how long this deployment's recent runs took, so the screen can say "usually about
a minute and a half" from evidence rather than a guess.

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
from typing import Any, Mapping, Optional, Sequence, Tuple

__all__ = [
    "RUN_SCHEMA",
    "TYPICAL_DURATION_SAMPLE",
    "CurrentRuns",
    "SuggestionRunPhase",
    "SuggestionRunRecord",
    "SuggestionRunStatus",
    "SuggestionRunStore",
]

#: The control-plane schema the registry (K11) and proposals (K7) already live in.
RUN_SCHEMA = "semantic"

#: How many recent successful runs the typical duration is a median of. Small on purpose:
#: a deployment whose data grew, or whose model tier changed, should see the estimate
#: follow within a handful of runs rather than average over a month of history.
TYPICAL_DURATION_SAMPLE = 5


class SuggestionRunStatus(str, Enum):
    """Where a run is. Spelled like the contract's ``HarvestStatus`` -- the same four words
    for the same shape, rather than a third vocabulary for "a job that is or isn't done"."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SuggestionRunPhase(str, Enum):
    """The three stages a ``running`` pass moves through, in order. What the screen's
    stepper shows; ``suggestions/run.py`` reports each as it begins. A ``running`` row
    with no phase yet is one whose executor has started but not reached the first stage."""

    READING = "reading"  # gathering the semantic layer the agents are shown
    PROPOSING = "proposing"  # the model calls
    CHECKING = "checking"  # assembly and feasibility against the snapshot


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
    #: The one area the run was narrowed to (a filter on what comes back), or ``None``.
    business_area: Optional[str]
    created_at: datetime
    #: The areas the customer asked the agents to propose for first (a priority in the
    #: message, never a filter). Empty when the run was started with no focus.
    business_areas: Tuple[str, ...] = ()
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result: Optional[Mapping[str, Any]] = None
    error: Optional[str] = None
    #: Set while ``running``, as the pipeline reports each stage; left as it was once the
    #: row is terminal (a ``failed`` row's phase says where it failed).
    phase: Optional[SuggestionRunPhase] = None
    #: What the customer asked for, in their own words (contract 1.2.3) -- a steer in the
    #: agents' message, kept on the row so a poll can say what the run was for. ``None``
    #: when the run was started without one.
    ask: Optional[str] = None
    #: The registered data source this run was started for -- the one whose harvest just
    #: finished -- or ``None`` for a run over the deployment's own warehouse (one started by
    #: ``POST /kpis/suggestions``). Also what makes a run's *scope*: the standing suggestions
    #: are the newest successful run of each scope (:meth:`SuggestionRunStore.current`).
    source_id: Optional[str] = None

    @property
    def terminal(self) -> bool:
        return self.status in (
            SuggestionRunStatus.SUCCEEDED,
            SuggestionRunStatus.FAILED,
        )


@dataclass(frozen=True)
class CurrentRuns:
    """What :meth:`SuggestionRunStore.current` found: the runs behind the standing list of
    suggestions, and what is happening behind it."""

    #: The newest successful run of each scope (a source, or the deployment's own warehouse),
    #: newest first. Their ``result`` is the suggestions.
    succeeded: Tuple[SuggestionRunRecord, ...] = ()
    #: A run still ``queued`` or ``running`` -- the newest, if several -- or ``None``.
    generating: Optional[SuggestionRunRecord] = None
    #: The newest ``failed`` run that no later success of the same scope has replaced, or
    #: ``None``. A failure that a later run got past is history, not something to show.
    #: One failure, across every scope, by design: the page has one banner with one "Try
    #: again". When two scopes have a standing failure the newer is the one reported, and the
    #: other is reported once that one is replaced by a success of its own scope.
    failed: Optional[SuggestionRunRecord] = None


def _scope(source_id: Optional[str]) -> str:
    """A run's scope: the source it was started for, or ``''`` for the deployment's own."""
    return source_id or ""


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
        business_areas jsonb NOT NULL DEFAULT '[]'::jsonb,
        phase         text,
        CONSTRAINT kpi_suggestion_run_status_valid
            CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
        CONSTRAINT kpi_suggestion_run_outcome_matches_status
            CHECK ((status = 'succeeded') = (result IS NOT NULL)
               AND (status = 'failed') = (error IS NOT NULL))
    )""",
        # A table provisioned before the focus existed (contract 1.1.5) gains the column
        # in place, with the same "no focus" default every older row then reads as.
        f"""ALTER TABLE {schema}.kpi_suggestion_run
        ADD COLUMN IF NOT EXISTS business_areas jsonb NOT NULL DEFAULT '[]'::jsonb""",
        # A table provisioned before `phase` existed (contract 1.1.5) gains the column in
        # place; the constraint is added once, guarded, since ADD CONSTRAINT has no IF NOT
        # EXISTS of its own.
        f"""ALTER TABLE {schema}.kpi_suggestion_run
        ADD COLUMN IF NOT EXISTS phase text""",
        # A table provisioned before `ask` existed (contract 1.2.3) gains the column in
        # place; every older row reads as "no request", which is what it was.
        f"""ALTER TABLE {schema}.kpi_suggestion_run
        ADD COLUMN IF NOT EXISTS ask text""",
        # A table provisioned before a run could be started for a source (contract 1.2.9)
        # gains the column in place; every older row reads as "the deployment's own warehouse",
        # which is what it was.
        f"""ALTER TABLE {schema}.kpi_suggestion_run
        ADD COLUMN IF NOT EXISTS source_id text""",
        f"""DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'kpi_suggestion_run_phase_valid'
              AND conrelid = '{schema}.kpi_suggestion_run'::regclass
        ) THEN
            ALTER TABLE {schema}.kpi_suggestion_run
            ADD CONSTRAINT kpi_suggestion_run_phase_valid
            CHECK (phase IS NULL OR phase IN ('reading', 'proposing', 'checking'));
        END IF;
    END $$""",
        f"""CREATE INDEX IF NOT EXISTS kpi_suggestion_run_open
        ON {schema}.kpi_suggestion_run (status, created_at)""",
        # Matches typical_duration()'s ORDER BY and the selective half of its predicate
        # (status = 'succeeded'; the started_at/finished_at IS NOT NULL checks are redundant
        # with it in practice, by two different guarantees -- finished_at because
        # mark_succeeded's UPDATE sets it atomically with that status, started_at because
        # _execute_suggestion_run always calls mark_running before mark_succeeded can ever
        # apply, and mark_succeeded's own only_from=_OPEN guard no-ops otherwise) -- that
        # query runs on every 202 and every poll, so it needs an index rather than a
        # sort/scan of the (unpurged) whole table.
        f"""CREATE INDEX IF NOT EXISTS kpi_suggestion_run_succeeded_finished
        ON {schema}.kpi_suggestion_run (finished_at DESC) WHERE status = 'succeeded'""",
        # `current()` reads the newest successful (and failed) run of each scope.
        f"""CREATE INDEX IF NOT EXISTS kpi_suggestion_run_scope_finished
        ON {schema}.kpi_suggestion_run ((coalesce(source_id, '')), finished_at DESC)
        WHERE status IN ('succeeded', 'failed')""",
        f"""COMMENT ON TABLE {schema}.kpi_suggestion_run IS
        'One row per POST /kpis/suggestions (#254). Polled by GET /kpis/suggestions/{{id}}; '
        'result is the KpiSuggestionList once succeeded, error the reason once failed; '
        'phase is the stage a running row is in.'""",
    )


_COLUMNS = (
    "id, status, business_area, created_at, started_at, finished_at, result, error, "
    "business_areas, phase, ask, source_id"
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

    def create(
        self,
        business_area: Optional[str],
        business_areas: Sequence[str] = (),
        ask: Optional[str] = None,
        source_id: Optional[str] = None,
    ) -> SuggestionRunRecord:
        """Record a new, queued run and return it. ``business_areas`` is the focus and
        ``ask`` the customer's request the run was started with, both kept on the row so
        the poll can echo what was asked for. ``source_id`` names the registered data source
        the run is for (``None``: the deployment's own warehouse)."""
        identifier = f"sr_{uuid.uuid4().hex}"
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.kpi_suggestion_run
                        (id, business_area, business_areas, ask, source_id)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING {_COLUMNS}""",
                (
                    identifier,
                    business_area,
                    json.dumps(list(business_areas)),
                    ask,
                    source_id,
                ),
            )
            row = cursor.fetchone()
        self._connection.commit()
        return _record(row)

    async def acreate(
        self,
        business_area: Optional[str],
        business_areas: Sequence[str] = (),
        ask: Optional[str] = None,
        source_id: Optional[str] = None,
    ) -> SuggestionRunRecord:
        """Async variant."""
        return await asyncio.to_thread(
            self.create, business_area, business_areas, ask, source_id
        )

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

    def mark_phase(self, identifier: str, phase: SuggestionRunPhase) -> None:
        """Record which stage a ``running`` run has reached. A no-op on any other row: a
        phase reported after the timeout closed the row must not reopen or relabel it."""
        self._update(
            "phase = %s",
            (SuggestionRunPhase(phase).value,),
            identifier,
            only_from=(SuggestionRunStatus.RUNNING.value,),
        )

    async def amark_phase(self, identifier: str, phase: SuggestionRunPhase) -> None:
        """Async variant."""
        await asyncio.to_thread(self.mark_phase, identifier, phase)

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

    def current(self, *, stale_after: Optional[timedelta] = None) -> CurrentRuns:
        """The runs behind the standing list of suggestions -- see :class:`CurrentRuns`.

        ``stale_after`` closes a ``queued``/``running`` run whose executor is gone, exactly as
        :meth:`get` does for one run, so a run lost mid-flight cannot leave the page waiting
        on ``generating`` forever: it becomes ``failed`` here, and is reported as such.

        One row per scope, because a scope's newest success is the whole of what it currently
        proposes -- an older run of the same scope is superseded by it, while another scope's
        run is not (two sources each keep their own suggestions).

        "Newest" is the run that *finished* last (``finished_at``), not the one that was
        created last: runs of one scope can overlap (a manual run started while a harvest's
        is still going), and the one that finishes last holds the answer that is current.
        """
        if stale_after is not None:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    f"""UPDATE {self._schema}.kpi_suggestion_run
                        SET status = 'failed', error = %s, finished_at = now()
                        WHERE status = ANY(%s) AND created_at < %s""",
                    (
                        LOST_RUN_ERROR,
                        list(_OPEN),
                        datetime.now(timezone.utc) - stale_after,
                    ),
                )
            self._connection.commit()
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT DISTINCT ON (coalesce(source_id, '')) {_COLUMNS}
                    FROM {self._schema}.kpi_suggestion_run
                    WHERE status = 'succeeded'
                    ORDER BY coalesce(source_id, ''), finished_at DESC""",
            )
            succeeded = [_record(row) for row in cursor.fetchall()]
            cursor.execute(
                f"""SELECT {_COLUMNS} FROM {self._schema}.kpi_suggestion_run
                    WHERE status = ANY(%s)
                    ORDER BY created_at DESC LIMIT 1""",
                (list(_OPEN),),
            )
            row = cursor.fetchone()
            generating = _record(row) if row is not None else None
            cursor.execute(
                f"""SELECT DISTINCT ON (coalesce(source_id, '')) {_COLUMNS}
                    FROM {self._schema}.kpi_suggestion_run
                    WHERE status = 'failed'
                    ORDER BY coalesce(source_id, ''), finished_at DESC""",
            )
            failures = [_record(row) for row in cursor.fetchall()]
        self._connection.commit()

        succeeded_at = {
            _scope(run.source_id): run.finished_at
            for run in succeeded
            if run.finished_at
        }
        standing = [
            run
            for run in failures
            if run.finished_at is not None
            and (
                _scope(run.source_id) not in succeeded_at
                or run.finished_at > succeeded_at[_scope(run.source_id)]
            )
        ]
        succeeded.sort(key=lambda run: run.finished_at or run.created_at, reverse=True)
        # One banner for every scope, not one per scope: see ``CurrentRuns.failed``.
        failed = max(
            standing, key=lambda run: run.finished_at or run.created_at, default=None
        )
        return CurrentRuns(
            succeeded=tuple(succeeded), generating=generating, failed=failed
        )

    async def acurrent(self, *, stale_after: Optional[timedelta] = None) -> CurrentRuns:
        """Async variant."""
        return await asyncio.to_thread(self.current, stale_after=stale_after)

    def typical_duration(
        self, *, sample: int = TYPICAL_DURATION_SAMPLE
    ) -> Optional[float]:
        """How long a run takes here, in seconds: the median of the last ``sample``
        successful runs' ``started_at`` -> ``finished_at``, or ``None`` before any has
        succeeded. Successful runs only -- a run the timeout closed says how long the bound
        is, not how long a run takes -- and a median, so one outlier does not move it."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds)
                    FROM (
                        SELECT EXTRACT(EPOCH FROM (finished_at - started_at)) AS seconds
                        FROM {self._schema}.kpi_suggestion_run
                        WHERE status = 'succeeded'
                          AND started_at IS NOT NULL
                          AND finished_at IS NOT NULL
                        ORDER BY finished_at DESC
                        LIMIT %s
                    ) recent""",
                (sample,),
            )
            row = cursor.fetchone()
        seconds = row[0] if row is not None else None
        return float(seconds) if seconds is not None else None

    async def atypical_duration(
        self, *, sample: int = TYPICAL_DURATION_SAMPLE
    ) -> Optional[float]:
        """Async variant."""
        return await asyncio.to_thread(self.typical_duration, sample=sample)

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
        business_areas,
        phase,
        ask,
        source_id,
    ) = row
    if isinstance(result, str):  # a driver that hands jsonb back undecoded
        result = json.loads(result)
    if isinstance(business_areas, str):
        business_areas = json.loads(business_areas)
    return SuggestionRunRecord(
        identifier=identifier,
        status=SuggestionRunStatus(status),
        business_area=business_area,
        created_at=created_at,
        started_at=started_at,
        finished_at=finished_at,
        result=result,
        error=error,
        business_areas=tuple(business_areas or ()),
        phase=SuggestionRunPhase(phase) if phase is not None else None,
        ask=ask,
        source_id=source_id,
    )
