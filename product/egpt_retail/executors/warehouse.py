"""R4's executors: load, conform, measure, decide (G8).

The first six entries in the executor registry, and they follow its convention exactly.
``<verb>_<object>``, one required parameter named ``step_input``, and -- because every one of
them does I/O -- ``async def`` under a single name, which is the registry's explicit,
Lane-R-accepted exemption from the repository's standing sync/async rule. There is no
``aload_landing_files`` twin: an executor has exactly one caller, the workflow engine, which
handles both forms, and a flat namespace that cannot survive ambiguity should not be doubled
to offer a choice nobody makes. The sync and async pairs live one layer down, on
``egpt_retail.warehouse`` and ``egpt_retail.storage``, where a caller genuinely picks.

**The workflow these compose is ``arun``-only.** A synchronous ``Workflow.run()`` raises
"Cannot use async function with synchronous execution" *inside* ``Step``'s retry loop
(``libs/agno/egpt/workflow/step.py:772``), which ``on_error`` then handles -- and ``on_error``
defaults to ``OnError.skip``. So a sync run would skip the load, skip conformance, skip all
three gates and report a completed run. Every step also sets ``on_error=OnError.fail``, so
that an unexpected exception fails the run instead of carrying on toward gold.

Five of the six measure and report. Only ``decide_load_verdict`` blocks, which keeps the one
thing that says no in one reviewable place, and means an operator with two problems on the
same night learns about both.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Mapping, Optional

from egpt.workflow.types import StepInput, StepOutput
from sqlalchemy.engine import Engine

from egpt_retail.app.db import build_warehouse_engine, psycopg_dsn
from egpt_retail.app.settings import RetailSettings
from egpt_retail.storage import build_object_store
from egpt_retail.warehouse.conform import aconform_silver, arecord_stream_counts
from egpt_retail.warehouse.landing import aload_landing
from egpt_retail.warehouse.loads import (
    LoadLock,
    aclose_load_run,
    aopen_load_run,
    aprevious_successful_load,
    fail_load_run,
    ungated_rows,
)
from egpt_retail.warehouse.quality import (
    GATE_ROW_COUNT_DELTA,
    SUBJECT_WHOLE_LOAD,
    GateResult,
    aggregate_verdict,
    arecord_gate_results,
    measure_key_null_rate,
    measure_referential_integrity,
    measure_row_count_delta,
    read_gate_results,
)

__all__ = [
    "load_landing_files",
    "conform_silver_entities",
    "check_row_count_delta",
    "check_key_null_rates",
    "check_referential_integrity",
    "decide_load_verdict",
    "WAREHOUSE_EXECUTORS",
    "selected_paths",
]

log = logging.getLogger(__name__)

#: Where a step stashes what the next one needs. The workflow engine passes each step the
#: previous step's output, but a six-step chain in which step five needs step one's load_id
#: would otherwise thread it through four intermediate payloads.
_STATE_KEY = "r4_load"

#: The advisory lock held for the duration of one run, keyed by load_id. Module state because
#: the lock must outlive a single executor -- it is acquired by the first step and released by
#: the last -- and an open psycopg connection cannot go in ``additional_data``, which the
#: workflow persists.
_LOCKS: Dict[str, LoadLock] = {}


def _state(step_input: StepInput) -> Dict[str, Any]:
    """The mutable bag shared across this run's steps."""
    data = step_input.additional_data
    if data is None:
        raise RuntimeError(
            "the landing-to-silver workflow needs additional_data; run it with at least "
            "{'trigger': 'manual'}"
        )
    return data.setdefault(_STATE_KEY, {})


def _options(step_input: StepInput) -> Mapping[str, Any]:
    return step_input.additional_data or {}


@contextmanager
def _warehouse(settings: RetailSettings) -> Iterator[Engine]:
    """A warehouse engine for the duration of one executor, disposed on the way out.

    Disposed per call rather than shared across the run. A shared engine needs an owner for
    its lifetime, and a run can end at any step -- ``decide_load_verdict`` returns
    ``stop=True`` on a block and never runs at all if an earlier step raises -- so "dispose it
    in the last step" is a lifetime that does not always happen. Six connect cycles on a
    nightly job is not worth that. What was actually wrong before was that nothing disposed
    them at all, inside a long-lived pipelines process: six ``QueuePool``s per run, for ever.
    """
    engine = build_warehouse_engine(settings)
    try:
        yield engine
    finally:
        engine.dispose()


@contextmanager
def _guarded(step_input: StepInput, engine: Engine) -> Iterator[None]:
    """Close the load as ``failed`` if the body raises, then let the exception propagate.

    ``close_load_run`` is only reached from the last step, so before this an exception in any
    earlier one left ``finished_at`` and ``verdict`` NULL for ever -- indistinguishable from
    a run still in progress, and excluded from ``quality.latest_load``. ``'failed'`` was a
    verdict the schema permitted and nothing wrote.

    Re-raises rather than swallowing: the step must still fail, so the workflow stops instead
    of carrying on toward gold with a load that did not finish.
    """
    state = _state(step_input)
    load_id = state.get("load_id")
    try:
        yield
    except Exception as exc:
        if load_id is not None:
            fail_load_run(
                engine,
                uuid.UUID(str(load_id)),
                f"Failed during the load: {type(exc).__name__}: {exc}",
            )
            _release_lock(str(load_id))
        raise


def _release_lock(load_id: str) -> None:
    """Drop the run's advisory lock, if it still holds one. Safe to call twice."""
    lock = _LOCKS.pop(load_id, None)
    if lock is not None:
        lock.release()


def _load_id(step_input: StepInput) -> uuid.UUID:
    state = _state(step_input)
    load_id = state.get("load_id")
    if load_id is None:
        raise RuntimeError(
            "no load_id in the run state: load_landing_files must run before this step"
        )
    return uuid.UUID(str(load_id))


def _overrides(step_input: StepInput) -> Optional[Mapping[str, Mapping[str, float]]]:
    value = _options(step_input).get("thresholds")
    return value if isinstance(value, Mapping) else None


def selected_paths(options: Mapping[str, Any]) -> Optional[List[str]]:
    """Which objects this run may read: a list, or ``None`` meaning everything.

    ``paths is not None``, never ``if paths``. An explicit empty list means "restrict this run
    to zero files", and ``[]`` is falsy -- so a truthiness test turned that into ``None``,
    which ``load_landing`` reads as "load everything". Asking for nothing and silently getting
    an unrestricted full load is the worst available answer to the question.

    Pure, and separate from the executor, so the distinction is testable without a warehouse.
    """
    paths = options.get("paths")
    return None if paths is None else [str(path) for path in paths]


async def load_landing_files(step_input: StepInput) -> StepOutput:
    """Read the raw drop into ``landing.raw_row`` and open the load's ledger row.

    Refuses a path that does not conform to the landing contract, and an object outside
    ``raw/``. A row that is not a JSON object, or whose CRM ``record_type`` is unknown, is
    reported as a defect rather than raised -- one bad line must not cost a whole file.
    """
    options = _options(step_input)
    settings = RetailSettings()
    store = build_object_store(settings)
    state = _state(step_input)

    with _warehouse(settings) as engine:
        # Refuse rather than race. Two overlapping loads each compute their baseline from
        # `previous_successful_load` before the other records a verdict, so both measure
        # against a stale state and neither says so -- a silently wrong gate, which is worse
        # than a visible conflict. Same guard, same shape, as the consolidator's.
        lock = LoadLock(psycopg_dsn(str(settings.retail_warehouse_db_url)))
        if not lock.acquire():
            raise RuntimeError(
                "another landing-to-silver load holds the advisory lock. Loads are "
                "deliberately serialised: two at once would each compute a gate baseline "
                "from a state the other was about to change. Wait for it to finish."
            )

        run = await aopen_load_run(
            engine,
            trigger=str(options.get("trigger", "manual")),
            landing_root=repr(store),
            workflow_run_id=_run_id(step_input),
        )
        state["load_id"] = str(run.load_id)
        _LOCKS[str(run.load_id)] = lock
        with _guarded(step_input, engine):
            result = await aload_landing(
                engine, store, run.load_id, paths=selected_paths(options)
            )

            state["files"] = list(result.files)
            state["landed_rows"] = dict(result.landed_rows)
            state["inserted_rows"] = dict(result.inserted_rows)
            state["source_paths"] = {
                stream_id: list(found)
                for stream_id, found in result.source_paths.items()
            }
            state["defects"] = len(result.defects)
            state["ungated_rows"] = ungated_rows(engine, run.load_id)

            return StepOutput(
                content={
                    "load_id": str(run.load_id),
                    "files": len(result.files),
                    "rows_carried": result.total_landed,
                    "rows_inserted": sum(result.inserted_rows.values()),
                    "defects": len(result.defects),
                }
            )


def _run_id(step_input: StepInput) -> Optional[str]:
    session = step_input.workflow_session
    return getattr(session, "run_id", None) if session else None


async def conform_silver_entities(step_input: StepInput) -> StepOutput:
    """Run the five conformance statements and record this load's per-stream counts.

    Reports; never blocks. A stream that conformed nothing is a fact for the gates to weigh,
    not a decision this step is entitled to make.
    """
    load_id = _load_id(step_input)
    state = _state(step_input)
    source_paths = state.get("source_paths", {})

    with _warehouse(RetailSettings()) as engine, _guarded(step_input, engine):
        conformed = await aconform_silver(engine, load_id, source_paths)
        await arecord_stream_counts(
            engine,
            load_id,
            state.get("landed_rows", {}),
            state.get("inserted_rows", {}),
            conformed,
            source_paths,
        )
        return StepOutput(
            content={
                stream_id: {"conformed": counts[0], "silver": counts[1]}
                for stream_id, counts in conformed.items()
            }
        )


async def check_row_count_delta(step_input: StepInput) -> StepOutput:
    """Landed rows against the previous successful load, and a silver-shrink check."""
    load_id = _load_id(step_input)
    options = _options(step_input)
    with _warehouse(RetailSettings()) as engine, _guarded(step_input, engine):
        previous = await aprevious_successful_load(engine, load_id)
        results = measure_row_count_delta(
            engine,
            load_id,
            previous,
            overrides=_overrides(step_input),
            acknowledge_shrink=bool(options.get("acknowledge_shrink")),
            acknowledge_undersized=bool(options.get("acknowledge_undersized")),
        )
        await arecord_gate_results(engine, load_id, results)
        return _measurement_output(results)


async def check_key_null_rates(step_input: StepInput) -> StepOutput:
    """Missing, blank or uncoercible key and watermark columns, per stream."""
    load_id = _load_id(step_input)
    with _warehouse(RetailSettings()) as engine, _guarded(step_input, engine):
        results = measure_key_null_rate(
            engine, load_id, overrides=_overrides(step_input)
        )
        await arecord_gate_results(engine, load_id, results)
        return _measurement_output(results)


async def check_referential_integrity(step_input: StepInput) -> StepOutput:
    """Child rows naming a parent that does not exist, per declared edge."""
    load_id = _load_id(step_input)
    with _warehouse(RetailSettings()) as engine, _guarded(step_input, engine):
        results = measure_referential_integrity(
            engine, overrides=_overrides(step_input)
        )
        await arecord_gate_results(engine, load_id, results)
        return _measurement_output(results)


def _measurement_output(results: Any) -> StepOutput:
    """A gate's output: what it saw, and success even when what it saw is bad.

    A failing measurement is not a failing step. The step did its job; the load is what has
    the problem, and ``decide_load_verdict`` is what is entitled to say so.
    """
    return StepOutput(
        content=[
            {
                "gate": r.gate,
                "subject": r.subject,
                "passed": r.passed,
                "measured": r.measured,
                "threshold": r.threshold,
                "reason": r.reason,
            }
            for r in results
        ]
    )


async def decide_load_verdict(step_input: StepInput) -> StepOutput:
    """Aggregate every gate result, stamp the verdict, and stop the run if any gate refused.

    The only executor in R4 that blocks. ``stop=True`` breaks the workflow's step loop, so a
    gold step appended after this one (R5) never runs. It does not mark the run failed --
    which is exactly why the verdict is written to ``quality.load_run`` as well as returned:
    a blocked load has to be legible from the data, not only from a run record.
    """
    load_id = _load_id(step_input)
    state = _state(step_input)

    with _warehouse(RetailSettings()) as engine, _guarded(step_input, engine):
        results = list(read_gate_results(engine, load_id))

        # Conformance is a full recompute over all of landing, so rows landed by a load that
        # never passed -- one that crashed, or one that was blocked -- are folded into this
        # load's silver and attributed to a load whose own gates never saw them. Counting
        # them here is what stops a load's gates being silently bypassed for rows it landed.
        absorbed = int(state.get("ungated_rows", 0))
        if absorbed:
            results.append(
                GateResult(
                    gate=GATE_ROW_COUNT_DELTA,
                    subject=SUBJECT_WHOLE_LOAD,
                    passed=False,
                    reason=(
                        f"This load's silver absorbed {absorbed} landing row(s) from an "
                        "earlier load that never passed its gates. A full recompute cannot "
                        "tell them apart, so those rows entered silver ungated. Re-run once "
                        "the earlier load's problem is fixed, or accept them deliberately."
                    ),
                    measured=float(absorbed),
                    threshold=0.0,
                    observed={"ungated_rows": absorbed},
                )
            )
            await arecord_gate_results(engine, load_id, results[-1:])

        verdict = aggregate_verdict(
            results,
            acknowledged_shrink=bool(_options(step_input).get("acknowledge_shrink")),
        )
        await aclose_load_run(
            engine,
            load_id,
            verdict=verdict.verdict,
            reason=verdict.reason,
            files_loaded=len(state.get("files", [])),
            rows_landed=sum(state.get("landed_rows", {}).values()),
        )
        log.info("load %s: %s -- %s", load_id, verdict.verdict, verdict.reason)

    # The run is over either way -- a blocked verdict returns stop=True and no later step
    # runs -- so the lock is released here rather than left for a step that may never happen.
    _release_lock(str(load_id))

    return StepOutput(
        content=verdict.to_dict(),
        success=not verdict.blocked,
        error=verdict.reason if verdict.blocked else None,
        stop=verdict.blocked,
    )


#: Registered in ``app/components.py``. Order is documentation only -- the registry resolves
#: by name -- but it is the order the workflow runs them in.
WAREHOUSE_EXECUTORS = (
    load_landing_files,
    conform_silver_entities,
    check_row_count_delta,
    check_key_null_rates,
    check_referential_integrity,
    decide_load_verdict,
)
