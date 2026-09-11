"""R14 -- execution health from run history, plus the quality/drift assessment pass (D14).

Two separate questions, and the API contract already splits them (`monitoring.yaml`, "D14:
two separate questions"): **is it running** (`UseCaseExecutionHealth`, from persisted run
history -- failures, a schedule that stopped firing, a gate waiting too long on a person, cost
drift) and **is it still right** (`UseCaseQuality`, an assessment pass over input drift and,
from R15's golden sets, output quality). A use case that runs green while quietly answering
worse is the failure mode the first question cannot see; that is the whole reason the second
one exists.

**Scope, deliberately**: this module owns execution health and input-drift assessment. It does
not own golden question sets or the rehearsal/publish gate -- that is R15, a sibling package
building concurrently. `golden_pass_rate` is accepted here as an input (from R15's pipeline,
when it has run for a use case) and never computed; a use case with none yet reports it `None`
and the API contract's own worked example for that shape (`unknown`, "No golden set on this
version yet") is exactly the state this module produces before R15 has run.

**Why no model judge.** The framework ships `egpt.eval.agent_as_judge` for scoring output
quality, and it was checked before writing the pure `drift_score` below. It was not reused,
for two reasons specific to this contract. First, `UseCaseQuality` has exactly two evidence
fields -- `golden_pass_rate` (output quality, R15's) and `drift_score` (input drift, this
package's) -- and neither is "a judge's opinion of this run's output"; inventing a third,
undocumented signal to hold one would drift the implementation away from the contract it is
supposed to conform to. Second, and more fundamentally, the framework rule that a permit/block
decision must be a deterministic threshold over a score applies just as much to a *drift*
score: `drift_score` is a statistical distance between two row samples' shapes (mean/stdev
shift, categorical distribution shift, null-rate shift), not a judgment call a model would be
asked to make, so there is nothing here a judge would do more honestly than arithmetic.

**Every executor below is read-only.** None returns ``stop=True`` -- a monitoring pass reports,
it never gates a chain -- so ``permits_or_blocks`` is ``False`` for all three in
``executors/metadata.py``. ``async def`` only, per the registry's own stated exemption: the
workflow engine is the one caller and it always awaits, so a sync twin would only double a
flat, collision-prone namespace to give a choice nobody makes. The pure helpers alongside them
(no I/O) are plain functions, the same split ``executors/rfq.py`` uses for `rank_movers`.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from egpt.db.base import SessionType
from egpt.run.base import RunStatus
from egpt.scheduler.manager import ScheduleManager
from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.app.db import build_control_plane_db
from egpt_retail.app.settings import RetailSettings

__all__ = [
    "DRIFT_SCORE_THRESHOLD",
    "GOLDEN_PASS_THRESHOLD",
    "MONITORING_EXECUTORS",
    "RunRecord",
    "assess_execution_health",
    "assess_quality",
    "cost_drift_pct",
    "drift_score",
    "expected_previous_fire",
    "extract_run_records",
    "input_signature",
    "is_silent",
    "list_silent_schedules",
    "map_run_status",
    "quality_verdict",
    "schedule_run_records",
    "summarize_execution_health",
]

#: Above this, the run itself succeeded but the data it read has moved enough from its
#: baseline shape to call the answer suspect. Deliberately a plain threshold, not tuned
#: against real deployments yet -- the same "0 is none" contract the schema documents.
DRIFT_SCORE_THRESHOLD = 0.3

#: Below this, R15's golden set says the use case is answering worse than its own baseline.
GOLDEN_PASS_THRESHOLD = 0.8

#: `summarize_execution_health`'s tie-break when two records share a `created_at` second:
#: lower rank wins a tie. Anything not listed (`completed`/`regenerated`/etc.) ranks lowest,
#: so a real problem never loses a same-second tie to an ordinary success.
_TIE_BREAK_RANK: Dict[RunStatus, int] = {
    RunStatus.error: 0,
    RunStatus.cancelled: 1,
    RunStatus.paused: 2,
}

#: `UseCaseExecutionHealth.silent`/`SilentSchedule`: how long past a schedule's expected fire
#: the window has to be closed before flagging it, so a schedule two minutes late (the poller
#: is still mid-cycle) is not reported as silent.
_DEFAULT_SILENT_GRACE_MINUTES = 60

#: How long a paused run counts toward `gates_waiting` -- an approval gate a person has not
#: touched yet, not one still within its normal turnaround.
_DEFAULT_GATE_WAIT_MINUTES = 60

#: `egpt.run.base.RunStatus` (six values, this framework's run lifecycle) to the API
#: contract's `RunStatus` (`use-cases.yaml`: queued/running/succeeded/failed/blocked/
#: cancelled). `regenerated` maps to `succeeded`: the run itself completed, and being
#: superseded by a later regeneration is a fact about the session, not about whether this
#: run failed.
_CONTRACT_STATUS: Dict[RunStatus, str] = {
    RunStatus.pending: "queued",
    RunStatus.running: "running",
    RunStatus.completed: "succeeded",
    RunStatus.paused: "blocked",
    RunStatus.cancelled: "cancelled",
    RunStatus.error: "failed",
    RunStatus.regenerated: "succeeded",
}


def map_run_status(status: Any) -> str:
    """The contract's `RunStatus` string for a framework `RunStatus` member.

    Anything this map does not recognise (a future framework status) reports ``"failed"``
    rather than raising or silently reporting ``"succeeded"`` -- an unrecognised state is
    closer in spirit to "something went wrong" than to "this is fine", and health monitoring
    erring toward a false alarm is the same posture K6's drift heuristic documents for itself.
    """
    return _CONTRACT_STATUS.get(status, "failed")


@dataclass(frozen=True)
class RunRecord:
    """One workflow run, reduced to what execution health needs. Pure data -- built once from
    a `WorkflowRunOutput` by :func:`extract_run_records`, so every aggregation below is a
    function of plain values rather than of the framework's run objects."""

    run_id: Optional[str]
    status: Any  # egpt.run.base.RunStatus
    created_at: datetime
    input_tokens: int = 0
    output_tokens: int = 0


def extract_run_records(session: Any) -> Tuple[RunRecord, ...]:
    """Every run in one `WorkflowSession`, with its token cost summed across whichever
    agent/team runs the workflow's steps produced (`step_executor_runs` -- the funnel
    explanation step's is the only one so far, but this sums however many there are).

    A run with no `created_at` is dropped rather than defaulted to *now*: a record this
    module cannot place in time cannot contribute to a windowed count without silently
    padding whichever window `now` happens to fall in.
    """
    records: List[RunRecord] = []
    for run in getattr(session, "runs", None) or ():
        created_at = getattr(run, "created_at", None)
        if not created_at:
            continue
        when = datetime.fromtimestamp(int(created_at), tz=timezone.utc)
        input_tokens = 0
        output_tokens = 0
        for executor_run in getattr(run, "step_executor_runs", None) or ():
            metrics = getattr(executor_run, "metrics", None)
            if metrics is None:
                continue
            input_tokens += getattr(metrics, "input_tokens", 0) or 0
            output_tokens += getattr(metrics, "output_tokens", 0) or 0
        records.append(
            RunRecord(
                run_id=getattr(run, "run_id", None),
                status=getattr(run, "status", None),
                created_at=when,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
    return tuple(records)


def cost_drift_pct(current_total: int, prior_total: int) -> Optional[float]:
    """Percent change in total tokens against the prior window. `None` -- not zero -- when
    the prior window has nothing to compare against, since a 7-day-old use case is not
    "0% drift", it has no baseline yet."""
    if prior_total <= 0:
        return None
    return round(((current_total - prior_total) / prior_total) * 100, 2)


def summarize_execution_health(
    records: Sequence[RunRecord],
    *,
    now: datetime,
    window_days: int = 7,
    gate_wait_minutes: int = _DEFAULT_GATE_WAIT_MINUTES,
) -> Dict[str, Any]:
    """Everything `UseCaseExecutionHealth` needs from run history alone -- every field except
    `silent`, which is a schedule-cadence question answered by :func:`is_silent` instead.

    Pure: takes already-extracted records and the clock, returns the contract's own shape.
    `runs_7d`/`failures_7d`/`cost_7d` cover ``[now - window_days, now)``; the cost comparison
    window is the ``window_days`` immediately before that, so "drift" means "against the
    period right before this one", the same meaning the contract's `drift_pct` documents.
    """
    window_start = now - timedelta(days=window_days)
    prior_start = now - timedelta(days=2 * window_days)
    current_window = [r for r in records if window_start <= r.created_at <= now]
    prior_window = [r for r in records if prior_start <= r.created_at < window_start]

    # `created_at` is second-granularity (both the framework's `run.created_at` and
    # `ScheduleRun.triggered_at` are stored as epoch seconds), so two genuinely distinct
    # events -- a scheduled retry landing in the same wall-clock second as the run before it,
    # easily reproduced when a retry follows immediately on failure -- can tie exactly. A bare
    # `max()` breaks that tie by insertion order, which happens to put session-derived records
    # before schedule-derived ones and so silently prefers a tied success over a tied failure --
    # exactly backwards for an executor whose job is to surface trouble, not default to hiding
    # it under ambiguous ordering. `_TIE_BREAK_RANK` makes the non-`completed`/`succeeded`
    # outcome win a tie instead, on the same "the glance costs less than the miss" reasoning
    # `drift_score` documents for its own review heuristic.
    last = (
        max(records, key=lambda r: (r.created_at, -_TIE_BREAK_RANK.get(r.status, 99)))
        if records
        else None
    )
    gate_cutoff = now - timedelta(minutes=gate_wait_minutes)
    gates_waiting = sum(
        1
        for r in records
        if r.status == RunStatus.paused and r.created_at <= gate_cutoff
    )
    failures_7d = sum(1 for r in current_window if r.status == RunStatus.error)

    current_in = sum(r.input_tokens for r in current_window)
    current_out = sum(r.output_tokens for r in current_window)
    prior_total = sum(r.input_tokens + r.output_tokens for r in prior_window)

    return {
        "last_run_status": map_run_status(last.status) if last is not None else None,
        "last_run_at": last.created_at.isoformat() if last is not None else None,
        "runs_7d": len(current_window),
        "failures_7d": failures_7d,
        "gates_waiting": gates_waiting,
        "cost_7d": {
            "input_tokens": current_in,
            "output_tokens": current_out,
            "drift_pct": cost_drift_pct(current_in + current_out, prior_total),
        },
    }


def expected_previous_fire(
    cron_expr: str, timezone_name: str, now: datetime
) -> datetime:
    """The most recent moment this cadence should have fired, at or before ``now``.

    Deliberately derived from the cron expression alone, not from the schedule row's own
    mutable ``next_run_at`` -- the poller only advances ``next_run_at`` for a schedule it
    actually claims, so a *disabled* schedule's ``next_run_at`` simply stalls wherever it was
    left. The cadence itself does not stop implying an expectation just because the schedule
    was switched off, which is exactly the case the D14 Verify line names ("disable a schedule
    and wait past its window").
    """
    import pytz  # type: ignore[import-untyped]
    from croniter import croniter  # type: ignore[import-untyped]

    tz = pytz.timezone(timezone_name)
    localized_now = now.astimezone(tz)
    previous = croniter(cron_expr, localized_now).get_prev(datetime)
    return previous.astimezone(timezone.utc)


def is_silent(
    expected_at: datetime,
    last_fired_at: Optional[datetime],
    *,
    now: datetime,
    grace_minutes: int = _DEFAULT_SILENT_GRACE_MINUTES,
) -> bool:
    """Whether a schedule should have fired by ``expected_at`` and did not.

    ``last_fired_at`` is the last time it *fired* -- any attempt, whatever the outcome --
    deliberately not the last time it *succeeded*. Silence and failure are two different
    things the D14 evidence line is explicit about keeping apart ("the silent schedule is
    flagged, which failure monitoring alone would miss"): a schedule that fires every night
    and fails every night is a failure-monitoring problem (``failures_7d``), not a silent one,
    and conflating the two would make a broken-but-firing schedule indistinguishable from one
    that has stopped being triggered at all.

    ``grace_minutes`` guards the window that has not really closed yet: a schedule expected to
    fire two minutes ago, with the poller still mid-cycle, is not silent. Once the grace period
    has elapsed, silence is "nothing fired at or after the expected moment" -- a fire *before*
    ``expected_at`` does not count, which is what makes this catch a schedule that fired
    yesterday and then stopped, not just one that has never fired at all.
    """
    if now - expected_at < timedelta(minutes=grace_minutes):
        return False
    return last_fired_at is None or last_fired_at < expected_at


def _to_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def input_signature(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """A structural-and-statistical fingerprint of a row sample: per field, a null fraction
    plus either a mean/stdev (every present value parses as a number) or a value-frequency
    table (anything else). Used to compare one run's input rows against another's without
    storing the rows themselves forever -- the same reasoning K6's `structural()` gives for
    dropping sampled values from a stored snapshot, applied here to a live comparison instead
    of a stored one.

    Pure, and total over any row shape: an empty ``rows`` returns a fingerprint with no
    fields rather than raising, so a use case with nothing to compare reports "no evidence",
    not an error.
    """
    if not rows:
        return {"row_count": 0, "fields": {}}
    keys = sorted({key for row in rows for key in row.keys()})
    n = len(rows)
    fields: Dict[str, Any] = {}
    for key in keys:
        values = [row.get(key) for row in rows]
        present = [v for v in values if v is not None]
        null_fraction = 1.0 - (len(present) / n)
        numeric = [_to_number(v) for v in present]
        if present and all(v is not None for v in numeric):
            numbers = [v for v in numeric if v is not None]
            mean = sum(numbers) / len(numbers)
            variance = sum((v - mean) ** 2 for v in numbers) / len(numbers)
            fields[key] = {
                "kind": "numeric",
                "null_fraction": null_fraction,
                "mean": mean,
                "stdev": variance**0.5,
            }
        else:
            counts = Counter(str(v) for v in present)
            total = sum(counts.values()) or 1
            fields[key] = {
                "kind": "categorical",
                "null_fraction": null_fraction,
                "frequencies": {k: v / total for k, v in counts.items()},
            }
    return {"row_count": n, "fields": fields}


def drift_score(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> float:
    """The distance between two :func:`input_signature` fingerprints. ``0.0`` is identical;
    ``1.0`` is the per-field maximum this function ever assigns.

    Per shared numeric field: the mean's shift measured in the baseline's own standard
    deviations, capped at 1 (a field with zero spread in the baseline treats any change in
    the mean as maximal drift rather than dividing by zero), averaged with the null-fraction
    shift. Per shared categorical field: total variation distance between the two frequency
    tables, likewise averaged with the null-fraction shift. A field present in only one side,
    or whose *kind* changed (numeric to categorical or back -- a schema-shape change, not a
    distribution shift), scores the maximum, on the same "the glance costs less than the
    miss" reasoning K6 gives its own review heuristic. The overall score is the mean across
    every field either signature names; two signatures with nothing in common return ``0.0``
    -- nothing to compare is not evidence of drift, it is an absence of evidence.
    """
    baseline_fields: Mapping[str, Any] = baseline.get("fields", {})
    current_fields: Mapping[str, Any] = current.get("fields", {})
    keys = sorted(set(baseline_fields) | set(current_fields))
    if not keys:
        return 0.0
    scores: List[float] = []
    for key in keys:
        before = baseline_fields.get(key)
        after = current_fields.get(key)
        if before is None or after is None or before.get("kind") != after.get("kind"):
            scores.append(1.0)
            continue
        null_delta = abs(
            before.get("null_fraction", 0.0) - after.get("null_fraction", 0.0)
        )
        if before["kind"] == "numeric":
            spread = before.get("stdev") or 0.0
            mean_before = before.get("mean", 0.0)
            mean_after = after.get("mean", 0.0)
            if spread > 0:
                mean_shift = min(abs(mean_after - mean_before) / spread, 1.0)
            else:
                mean_shift = 0.0 if mean_after == mean_before else 1.0
            scores.append((mean_shift + null_delta) / 2)
        else:
            freq_before = before.get("frequencies", {})
            freq_after = after.get("frequencies", {})
            categories = set(freq_before) | set(freq_after)
            total_variation = 0.5 * sum(
                abs(freq_before.get(c, 0.0) - freq_after.get(c, 0.0))
                for c in categories
            )
            scores.append((total_variation + null_delta) / 2)
    return sum(scores) / len(scores)


def quality_verdict(
    golden_pass_rate: Optional[float],
    drift: Optional[float],
    *,
    golden_pass_threshold: float = GOLDEN_PASS_THRESHOLD,
    drift_score_threshold: float = DRIFT_SCORE_THRESHOLD,
) -> str:
    """`healthy` / `degraded` / `unknown`, from whichever of the two signals are available.

    Deterministic threshold over each score, never a model's opinion -- the same rule that
    makes `check_preflight`'s and `sample_rows`' refusals executors rather than agent
    judgments applies here to a *degradation* verdict. Either signal alone can produce
    `degraded`: a good golden pass rate does not excuse a use case whose input has drifted far
    from what it was validated against, and (the D14 Verify line's own point) a use case with
    no golden set yet must still be answerable by drift alone rather than reporting `unknown`
    just because R15 has not run for it."""
    signals: List[bool] = []
    if golden_pass_rate is not None:
        signals.append(golden_pass_rate < golden_pass_threshold)
    if drift is not None:
        signals.append(drift >= drift_score_threshold)
    if not signals:
        return "unknown"
    return "degraded" if any(signals) else "healthy"


#: `egpt.db.schemas.scheduler.ScheduleRun.status` ("running | success | failed | paused |
#: timeout") to the same framework `RunStatus` :data:`RunRecord.status` carries, so a
#: schedule-run-derived record can be summarised by the exact same pure aggregation as one
#: built from a `WorkflowSession`.
_SCHEDULE_RUN_STATUS: Dict[str, RunStatus] = {
    "running": RunStatus.running,
    "success": RunStatus.completed,
    "paused": RunStatus.paused,
    "failed": RunStatus.error,
    "timeout": RunStatus.error,
}


def schedule_run_records(runs: Sequence[Any]) -> Tuple[RunRecord, ...]:
    """`ScheduleRun` rows, reduced to the same :class:`RunRecord` shape a `WorkflowSession`
    produces -- **the source that actually sees a step's raised exception.**

    This is not a redundant second reading of the same fact. `ScheduleExecutor` (the real
    poller) calls a scheduled workflow over HTTP, and on any exception from that call writes
    ``ScheduleRun.status = "failed"`` in its own `except` clause -- but the workflow engine's
    non-streaming `arun()`, driven directly rather than through that HTTP hop, sets
    `WorkflowRunOutput.status = RunStatus.error` on the run object and then **re-raises**,
    which unwinds straight past the code that would otherwise persist the session. A hard
    step failure under `on_error=OnError.fail` is therefore invisible to
    ``get_sessions(session_type=WORKFLOW, ...)`` entirely: nothing is ever written. For a
    *scheduled* use case, ``ScheduleRun`` is where that failure actually lands, which is why
    :func:`assess_execution_health` merges these records in rather than relying on
    `WorkflowSession` run history alone. No token cost is available from a `ScheduleRun` row,
    so these records always carry ``input_tokens=output_tokens=0``.

    A run with no ``triggered_at`` (never actually attempted -- created and then the process
    died before the first update) is dropped, the same reasoning :func:`extract_run_records`
    gives for a run with no ``created_at``.
    """
    records: List[RunRecord] = []
    for run in runs:
        triggered_at = getattr(run, "triggered_at", None)
        if not triggered_at:
            continue
        status = _SCHEDULE_RUN_STATUS.get(getattr(run, "status", ""), RunStatus.error)
        records.append(
            RunRecord(
                run_id=getattr(run, "run_id", None) or getattr(run, "id", None),
                status=status,
                created_at=datetime.fromtimestamp(int(triggered_at), tz=timezone.utc),
            )
        )
    return tuple(records)


def _options(step_input: StepInput) -> Mapping[str, Any]:
    return step_input.additional_data or {}


def _now(options: Mapping[str, Any]) -> datetime:
    raw = options.get("now")
    if raw is None:
        return datetime.now(timezone.utc)
    value = datetime.fromisoformat(str(raw))
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _execution_health_error_record(use_case: Any, exc: BaseException) -> Dict[str, Any]:
    """The record :func:`assess_execution_health` reports for one use case whose own
    processing raised -- a malformed ``use_cases`` entry (a missing ``workflow_id`` or
    ``use_case_id``, say) isolated to that entry rather than aborting the whole batch, the
    same "isolate per-item failures" posture a batch loop over independently-fallible entries
    needs generally."""
    try:
        use_case_id = use_case.get("use_case_id", "<unknown>")
    except AttributeError:
        use_case_id = "<unknown>"
    try:
        title = use_case.get("title", use_case_id)
    except AttributeError:
        title = use_case_id
    try:
        version = int(use_case.get("version", 1))
    except (AttributeError, TypeError, ValueError):
        version = 1
    return {
        "use_case_id": use_case_id,
        "title": title,
        "version": version,
        "silent": False,
        "last_run_status": None,
        "last_run_at": None,
        "runs_7d": 0,
        "failures_7d": 0,
        "gates_waiting": 0,
        "cost_7d": {"input_tokens": 0, "output_tokens": 0, "drift_pct": None},
        "error": f"{type(exc).__name__}: {exc}",
    }


async def _assess_one_use_case_health(
    use_case: Mapping[str, Any],
    *,
    db: Any,
    manager: ScheduleManager,
    schedules_by_name: Mapping[str, Any],
    now: datetime,
    window_days: int,
) -> Dict[str, Any]:
    """One use case's slice of :func:`assess_execution_health`, isolated so one malformed
    entry (a bracket access on a missing key, e.g. ``workflow_id``) cannot abort every other
    use case's report. Not gathered concurrently with its siblings -- see
    :func:`assess_execution_health`'s own docstring for why the shared, lazily-reflecting
    ``db``/``manager`` make that unsafe here."""
    try:
        workflow_id = use_case["workflow_id"]
        sessions = await asyncio.to_thread(
            db.get_sessions,
            session_type=SessionType.WORKFLOW,
            component_id=workflow_id,
            sort_by="created_at",
            sort_order="desc",
            limit=500,
        )
        records: List[RunRecord] = []
        seen_run_ids: set[str] = set()
        for session in sessions or []:
            for record in extract_run_records(session):
                records.append(record)
                if record.run_id is not None:
                    seen_run_ids.add(record.run_id)

        cron = use_case.get("schedule_cron")
        cron_timezone = use_case.get("schedule_timezone", "UTC")
        schedule_name = use_case.get("schedule_name")
        if schedule_name:
            schedule = schedules_by_name.get(schedule_name)
            if schedule is not None:
                cron = schedule.cron_expr
                cron_timezone = schedule.timezone
                schedule_runs = await asyncio.to_thread(
                    manager.get_runs, schedule.id, 100, 1
                )
                # `schedule_run_records` stamps `run_id` from the same run a successful
                # `workflow.arun()` also persists as a `WorkflowSession` -- only the error
                # path skips that persistence -- so a run_id already seen from the session
                # side is the same attempt, not a second one, and would double-count
                # `runs_7d`/`cost_7d` if merged in again.
                for record in schedule_run_records(schedule_runs):
                    if record.run_id is not None and record.run_id in seen_run_ids:
                        continue
                    records.append(record)

        summary = summarize_execution_health(
            records,
            now=now,
            window_days=window_days,
            gate_wait_minutes=int(
                use_case.get("gate_wait_minutes", _DEFAULT_GATE_WAIT_MINUTES)
            ),
        )

        silent = False
        if cron:
            expected_at = expected_previous_fire(cron, cron_timezone, now)
            # Any attempt counts as "fired", not only a successful one -- see
            # `is_silent`'s own docstring on why silence and failure stay distinct.
            last_fired_at = max((r.created_at for r in records), default=None)
            silent = is_silent(
                expected_at,
                last_fired_at,
                now=now,
                grace_minutes=int(
                    use_case.get("silent_grace_minutes", _DEFAULT_SILENT_GRACE_MINUTES)
                ),
            )

        return {
            "use_case_id": use_case["use_case_id"],
            "title": use_case.get("title", use_case["use_case_id"]),
            "version": int(use_case.get("version", 1)),
            "silent": silent,
            **summary,
        }
    except Exception as exc:  # noqa: BLE001 -- isolate this entry, keep the batch going
        return _execution_health_error_record(use_case, exc)


async def assess_execution_health(step_input: StepInput) -> StepOutput:
    """`GET /monitoring/execution-health`'s data, assembled from real run history.

    ``additional_data["use_cases"]``: a list of ``{use_case_id, title, version, workflow_id,
    schedule_name, schedule_cron, schedule_timezone, gate_wait_minutes,
    silent_grace_minutes}`` -- the mapping from a use case to the workflow whose sessions
    carry its runs, and (when it has one) the schedule that promises it a cadence.

    ``schedule_name``, when given, is resolved against a name -> schedule map built **once**
    from the real `ScheduleManager` (the same one ``egpt-retail schedule-nightly`` writes
    through) before the per-use-case pass starts, the same ``list_silent_schedules`` pattern
    -- not re-queried per use case, which would otherwise be N redundant round trips against a
    table that does not change during the pass. Resolving a schedule gets two things: its
    `cron_expr`/`timezone` (used for the silent check below, taking precedence over an
    explicitly passed ``schedule_cron``/``schedule_timezone``), and -- the reason it matters
    here rather than only in :func:`list_silent_schedules` -- its `ScheduleRun` history,
    merged in via :func:`schedule_run_records`. That merge is not optional decoration: a hard
    step failure under `on_error=OnError.fail` unwinds straight past the code that would
    persist a `WorkflowSession`, so a *scheduled* use case's failures are visible here only
    through its `ScheduleRun` ledger, which the real poller writes on exactly that exception.
    Because a *successful* scheduled run is persisted through **both** paths -- the
    `WorkflowSession` (`workflow.arun()`'s normal path) and the `ScheduleRun` row
    (`ScheduleExecutor.execute()` writes one for every attempt) -- the merge dedups by
    ``run_id``: a `ScheduleRun` whose ``run_id`` already appears in the session-derived
    records is the same attempt seen twice, not two runs. A use case with neither
    ``schedule_name`` nor ``schedule_cron`` is never silent by definition, not by a
    missing-data coincidence, and its execution health comes from `WorkflowSession` history
    alone -- the honest limitation for an on-demand use case nothing schedules.

    ``additional_data["now"]`` overrides the clock, the same convention ``gold.py``'s
    ``as_of`` uses, so a cookbook or a test can pin "the window has just closed" instead of a
    real wait.

    Reads every session for each workflow id through the control-plane db's synchronous
    ``get_sessions`` (offloaded via ``asyncio.to_thread`` -- an ``async def`` executor is
    awaited directly by the engine with no automatic thread offload, the same reasoning
    ``read_gold`` documents for its own warehouse round-trips), sorted ``created_at`` descending
    so a use case with more than the 500-row ``limit`` gets its most recent sessions rather
    than an arbitrary, unordered subset, then reduces them to :class:`RunRecord`\\ s and hands
    the aggregation to the pure functions above.

    Each use case is still processed one at a time, deliberately not gathered concurrently
    with `asyncio.gather` even though each one's own I/O is logically independent of every
    other's: `PostgresDb.get_sessions` lazily reflects and caches its `Table` object onto
    ``self`` (``self.session_table``) on first use, and this control-plane ``db`` -- like
    ``manager`` -- is one instance shared across every use case in the batch. Gathering
    concurrently sends that lazy reflection racing across `asyncio.to_thread` worker threads,
    and it is not thread-safe: this was not a theoretical concern, it reproduced against the
    real control-plane Postgres in ``cookbook/94_retail/R14_monitoring.py`` as a stray
    `AttributeError` on the *second* use case in a batch of two, from a `Table` object one
    thread was still constructing while another thread's query already tried to read a column
    off it. Fixing that properly means either a `db`/`manager` per task or a lock around the
    lazy reflection, and either is a larger change than this fix's scope -- so the
    concurrency optimisation is deferred here, sequential is correct and safe, and a use case
    whose own processing raises (a malformed entry -- see :func:`_assess_one_use_case_health`)
    still reports an isolated error record instead of aborting every other use case's report.
    """
    options = _options(step_input)
    now = _now(options)
    use_cases = list(options.get("use_cases") or ())
    settings = RetailSettings()
    db = build_control_plane_db(settings)
    manager = ScheduleManager(db)

    try:
        # `setdefault` keeps the first-listed schedule on a name collision, matching the
        # pre-hoist `next((s for s in ... if s.name == schedule_name), None)` this replaced --
        # schedule names are unique only by a TOCTOU-racy check inside `ScheduleManager.create`,
        # not a DB constraint, so collisions are reachable even if presumed rare by convention.
        schedules_by_name: Dict[str, Any] = {}
        for schedule in await asyncio.to_thread(manager.list, None, 1000, 1):
            schedules_by_name.setdefault(schedule.name, schedule)
        window_days = int(options.get("window_days", 7))
        results = [
            await _assess_one_use_case_health(
                use_case,
                db=db,
                manager=manager,
                schedules_by_name=schedules_by_name,
                now=now,
                window_days=window_days,
            )
            for use_case in use_cases
        ]

        return StepOutput(content={"as_of": now.isoformat(), "use_cases": results})
    finally:
        manager.close()


async def list_silent_schedules(step_input: StepInput) -> StepOutput:
    """`GET /monitoring/schedules/silent`'s data: real `ScheduleManager` schedules whose
    cadence implies a fire this run history does not show.

    ``additional_data["schedules"]``: a list of ``{name, use_case_id, grace_minutes}`` --
    named rather than by id, because a cookbook or an activation flow names the schedule it
    just created (`egpt-retail schedule-nightly`'s own convention) before it knows the id
    the database assigned. A name absent from the live schedule table is skipped rather than
    reported: a use case whose schedule was deleted outright is a different failure from one
    whose schedule fired late, and this endpoint is about the second.
    """
    options = _options(step_input)
    now = _now(options)
    wanted = list(options.get("schedules") or ())
    settings = RetailSettings()
    db = build_control_plane_db(settings)
    manager = ScheduleManager(db)
    try:
        # `setdefault`, not a dict comprehension: same first-match reasoning
        # `assess_execution_health`'s `schedules_by_name` documents just above -- `name` has
        # no DB uniqueness constraint (`SCHEDULE_TABLE_SCHEMA["name"]` only indexes it), so a
        # collision is reachable, and the two executors must not silently disagree about
        # which schedule a shared name resolves to.
        by_name: Dict[str, Any] = {}
        for candidate in await asyncio.to_thread(manager.list, None, 1000, 1):
            by_name.setdefault(candidate.name, candidate)
        silent: List[Dict[str, Any]] = []
        for entry in wanted:
            schedule = by_name.get(entry["name"])
            if schedule is None:
                continue
            expected_at = expected_previous_fire(
                schedule.cron_expr, schedule.timezone, now
            )
            runs = await asyncio.to_thread(manager.get_runs, schedule.id, 100, 1)
            # Any attempt counts as "fired", not only a successful one -- see `is_silent`'s
            # own docstring on why silence and failure stay distinct signals.
            fired_at = [
                datetime.fromtimestamp(run.triggered_at, tz=timezone.utc)
                for run in runs
                if run.triggered_at
            ]
            last_fired_at = max(fired_at) if fired_at else None
            if is_silent(
                expected_at,
                last_fired_at,
                now=now,
                grace_minutes=int(
                    entry.get("grace_minutes", _DEFAULT_SILENT_GRACE_MINUTES)
                ),
            ):
                silent.append(
                    {
                        "schedule_id": schedule.id,
                        "use_case_id": entry.get("use_case_id"),
                        "name": schedule.name,
                        "expected_at": expected_at.isoformat(),
                        "last_fired_at": (
                            last_fired_at.isoformat() if last_fired_at else None
                        ),
                    }
                )
        return StepOutput(content={"silent_schedules": silent})
    finally:
        manager.close()


async def assess_quality(step_input: StepInput) -> StepOutput:
    """`GET /monitoring/quality`'s data: the verdict from whichever of R15's golden pass rate
    and this package's input-drift score are available.

    ``additional_data["use_cases"]``: a list of ``{use_case_id, version, baseline_rows,
    current_rows, golden_pass_rate, notes}``. ``baseline_rows``/``current_rows`` are row
    samples shaped like ``sample_rows``'/``read_gold``'s own output rows (a list of flat
    mappings) -- the caller's job is picking which two runs to compare (typically the
    version's rehearsal baseline against its most recent real run), not this executor's;
    handing it two row sets rather than two run ids keeps it from having to know how either
    was produced. ``golden_pass_rate`` is passed straight through: it is R15's number, not
    computed here, and a use case with no golden set yet passes ``None`` for it (or omits the
    key) and gets an honest ``unknown``/`` healthy``-by-drift-alone verdict instead of a
    fabricated pass rate.
    """
    options = _options(step_input)
    now = _now(options)
    use_cases = list(options.get("use_cases") or ())

    results: List[Dict[str, Any]] = []
    for use_case in use_cases:
        baseline_rows = use_case.get("baseline_rows") or []
        current_rows = use_case.get("current_rows") or []
        drift: Optional[float] = None
        if baseline_rows and current_rows:
            drift = drift_score(
                input_signature(baseline_rows), input_signature(current_rows)
            )
        golden_pass_rate = use_case.get("golden_pass_rate")
        verdict = quality_verdict(golden_pass_rate, drift)

        notes = use_case.get("notes", "")
        if (
            not notes
            and verdict == "degraded"
            and drift is not None
            and drift >= DRIFT_SCORE_THRESHOLD
        ):
            notes = (
                f"input distribution drift_score={drift:.2f}, at or above the "
                f"{DRIFT_SCORE_THRESHOLD} threshold, against this version's baseline rows; "
                "the run itself completed."
            )
        elif not notes and verdict == "degraded" and golden_pass_rate is not None:
            # No drift baseline to compare against (or none given) -- the golden set alone
            # is why this use case is degraded, and that reason deserves the same explanation
            # the drift-driven branch above gives, not a blank string.
            notes = (
                f"golden_pass_rate={golden_pass_rate:.2f} is below threshold; the run "
                "itself completed."
            )

        results.append(
            {
                "use_case_id": use_case["use_case_id"],
                "version": int(use_case.get("version", 1)),
                "verdict": verdict,
                "golden_pass_rate": golden_pass_rate,
                "drift_score": drift,
                "assessed_at": now.isoformat(),
                "notes": notes,
            }
        )

    return StepOutput(content={"as_of": now.isoformat(), "use_cases": results})


#: Registered in ``app/components.py``. None permits or blocks -- a monitoring pass reports.
MONITORING_EXECUTORS = (assess_execution_health, list_silent_schedules, assess_quality)
