"""Unit tests for R14 -- execution health, silent schedules, and the quality/drift pass.

Everything that touches a real control-plane Postgres (real `WorkflowSession` run history, a
real `ScheduleManager`) is exercised by ``cookbook/94_retail/R14_monitoring.py`` against the
real stack; what is pinned here is every rule that can be wrong without one -- the pure
aggregation, the cron-cadence arithmetic, the drift statistics, and the executors' own
wiring (mocked db/manager) -- the same split ``test_r10_rfq.py`` draws for R10.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from egpt.db.base import SessionType
from egpt.run.base import RunStatus
from egpt.workflow.types import StepInput

from egpt_retail.app.components import R14_EXECUTORS, build_registry
from egpt_retail.executors.metadata import EXECUTOR_METADATA
from egpt_retail.executors.monitoring import (
    DRIFT_SCORE_THRESHOLD,
    GOLDEN_PASS_THRESHOLD,
    RunRecord,
    assess_execution_health,
    assess_quality,
    cost_drift_pct,
    drift_score,
    expected_previous_fire,
    extract_run_records,
    input_signature,
    is_silent,
    list_silent_schedules,
    map_run_status,
    quality_verdict,
    schedule_run_records,
    summarize_execution_health,
)
from egpt_retail.executors.registry import check_registry

_REGISTRY_DOC = (
    Path(__file__).resolve().parents[1]
    / "egpt_retail"
    / "contracts"
    / "EXECUTOR_REGISTRY.md"
)

NOW = datetime(2026, 9, 7, 11, 50, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# map_run_status -- pure
# ---------------------------------------------------------------------------


def test_map_run_status_covers_every_framework_status():
    assert map_run_status(RunStatus.pending) == "queued"
    assert map_run_status(RunStatus.running) == "running"
    assert map_run_status(RunStatus.completed) == "succeeded"
    assert map_run_status(RunStatus.paused) == "blocked"
    assert map_run_status(RunStatus.cancelled) == "cancelled"
    assert map_run_status(RunStatus.error) == "failed"
    assert map_run_status(RunStatus.regenerated) == "succeeded"


def test_map_run_status_defaults_an_unrecognised_status_to_failed():
    assert map_run_status("something-a-future-framework-invented") == "failed"


# ---------------------------------------------------------------------------
# extract_run_records -- pure, over fake session/run objects
# ---------------------------------------------------------------------------


@dataclass
class _FakeMetrics:
    input_tokens: int = 0
    output_tokens: int = 0


def _fake_run(
    run_id: str,
    status: RunStatus,
    created_at: datetime,
    *,
    executor_runs: Optional[List[Any]] = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id,
        status=status,
        created_at=int(created_at.timestamp()),
        step_executor_runs=executor_runs,
    )


def test_extract_run_records_sums_tokens_across_step_executor_runs():
    run = _fake_run(
        "r1",
        RunStatus.completed,
        NOW,
        executor_runs=[
            SimpleNamespace(metrics=_FakeMetrics(input_tokens=100, output_tokens=20)),
            SimpleNamespace(metrics=_FakeMetrics(input_tokens=50, output_tokens=10)),
            SimpleNamespace(metrics=None),
        ],
    )
    session = SimpleNamespace(runs=[run])
    records = extract_run_records(session)
    assert records == (
        RunRecord(
            run_id="r1",
            status=RunStatus.completed,
            created_at=datetime.fromtimestamp(int(NOW.timestamp()), tz=timezone.utc),
            input_tokens=150,
            output_tokens=30,
        ),
    )


def test_extract_run_records_drops_a_run_with_no_created_at():
    run = _fake_run("r1", RunStatus.completed, NOW)
    run.created_at = None
    session = SimpleNamespace(runs=[run])
    assert extract_run_records(session) == ()


def test_extract_run_records_over_an_empty_session():
    assert extract_run_records(SimpleNamespace(runs=None)) == ()
    assert extract_run_records(SimpleNamespace(runs=[])) == ()


# ---------------------------------------------------------------------------
# cost_drift_pct -- pure
# ---------------------------------------------------------------------------


def test_cost_drift_pct_is_none_with_no_prior_baseline():
    assert cost_drift_pct(1000, 0) is None


def test_cost_drift_pct_reports_signed_percent_change():
    assert cost_drift_pct(1100, 1000) == 10.0
    assert cost_drift_pct(900, 1000) == -10.0


# ---------------------------------------------------------------------------
# summarize_execution_health -- pure
# ---------------------------------------------------------------------------


def test_summarize_execution_health_counts_the_7d_window_and_last_status():
    records = [
        RunRecord("r1", RunStatus.completed, NOW - timedelta(days=1)),
        RunRecord("r2", RunStatus.error, NOW - timedelta(days=2)),
        RunRecord("r3", RunStatus.completed, NOW - timedelta(days=10)),  # outside 7d
    ]
    summary = summarize_execution_health(records, now=NOW)
    assert summary["last_run_status"] == "succeeded"
    assert summary["last_run_at"] == records[0].created_at.isoformat()
    assert summary["runs_7d"] == 2
    assert summary["failures_7d"] == 1


def test_summarize_execution_health_with_no_runs_reports_nulls_and_zeros():
    summary = summarize_execution_health([], now=NOW)
    assert summary["last_run_status"] is None
    assert summary["last_run_at"] is None
    assert summary["runs_7d"] == 0
    assert summary["failures_7d"] == 0
    assert summary["gates_waiting"] == 0
    assert summary["cost_7d"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "drift_pct": None,
    }


def test_summarize_execution_health_gates_waiting_needs_the_wait_threshold_elapsed():
    just_paused = RunRecord("r1", RunStatus.paused, NOW - timedelta(minutes=5))
    long_paused = RunRecord("r2", RunStatus.paused, NOW - timedelta(hours=3))
    summary = summarize_execution_health(
        [just_paused, long_paused], now=NOW, gate_wait_minutes=60
    )
    assert summary["gates_waiting"] == 1


def test_summarize_execution_health_cost_drift_against_the_prior_window():
    current = RunRecord(
        "r1",
        RunStatus.completed,
        NOW - timedelta(days=1),
        input_tokens=800,
        output_tokens=200,
    )
    prior = RunRecord(
        "r0",
        RunStatus.completed,
        NOW - timedelta(days=9),
        input_tokens=400,
        output_tokens=100,
    )
    summary = summarize_execution_health([current, prior], now=NOW)
    assert summary["cost_7d"]["input_tokens"] == 800
    assert summary["cost_7d"]["output_tokens"] == 200
    assert summary["cost_7d"]["drift_pct"] == 100.0  # 1000 vs 500, doubled


# ---------------------------------------------------------------------------
# expected_previous_fire / is_silent -- pure
# ---------------------------------------------------------------------------


def test_expected_previous_fire_reads_the_cadence_not_a_stored_next_run_at():
    # Daily at 01:00 UTC; "now" is 11:50 on the 7th, so the most recent expected fire is
    # 01:00 the same day.
    expected = expected_previous_fire("0 1 * * *", "UTC", NOW)
    assert expected == datetime(2026, 9, 7, 1, 0, tzinfo=timezone.utc)


def test_is_silent_true_when_no_success_at_or_after_the_expected_fire():
    expected_at = NOW - timedelta(hours=2)
    assert is_silent(expected_at, None, now=NOW, grace_minutes=60) is True
    stale_success = expected_at - timedelta(days=1)
    assert is_silent(expected_at, stale_success, now=NOW, grace_minutes=60) is True


def test_is_silent_false_once_a_success_lands_at_or_after_the_expected_fire():
    expected_at = NOW - timedelta(hours=2)
    fresh_success = expected_at + timedelta(minutes=1)
    assert is_silent(expected_at, fresh_success, now=NOW, grace_minutes=60) is False


def test_is_silent_respects_the_grace_period():
    # Expected two minutes ago -- the window has not really closed yet.
    expected_at = NOW - timedelta(minutes=2)
    assert is_silent(expected_at, None, now=NOW, grace_minutes=60) is False


def test_is_silent_flags_a_deliberately_disabled_schedule_just_like_an_enabled_one():
    """The D14 Verify line: 'disable a schedule and wait past its window'. `is_silent` takes
    no `enabled` flag at all -- the cadence's expectation does not know or care whether the
    schedule that was supposed to honour it is switched on."""
    expected_at = NOW - timedelta(hours=3)
    assert is_silent(expected_at, None, now=NOW, grace_minutes=60) is True


# ---------------------------------------------------------------------------
# input_signature / drift_score -- pure
# ---------------------------------------------------------------------------


def test_input_signature_of_empty_rows():
    assert input_signature([]) == {"row_count": 0, "fields": {}}


def test_input_signature_numeric_field():
    rows = [{"value": "10"}, {"value": "12"}, {"value": "8"}]
    sig = input_signature(rows)
    field = sig["fields"]["value"]
    assert field["kind"] == "numeric"
    assert field["null_fraction"] == 0.0
    assert field["mean"] == 10.0


def test_input_signature_categorical_field_and_nulls():
    rows = [{"brand": "acme"}, {"brand": "acme"}, {"brand": None}, {"brand": "globex"}]
    sig = input_signature(rows)
    field = sig["fields"]["brand"]
    assert field["kind"] == "categorical"
    assert field["null_fraction"] == 0.25
    assert field["frequencies"] == {"acme": 2 / 3, "globex": 1 / 3}


def test_drift_score_is_zero_for_identical_signatures():
    rows = [{"value": "10", "brand": "acme"}, {"value": "12", "brand": "globex"}]
    sig = input_signature(rows)
    assert drift_score(sig, sig) == 0.0


def test_drift_score_is_zero_with_no_shared_fields():
    assert drift_score({"fields": {}}, {"fields": {}}) == 0.0


def test_drift_score_rises_with_a_large_numeric_mean_shift():
    baseline = input_signature([{"value": "10"}, {"value": "12"}, {"value": "8"}])
    shifted = input_signature([{"value": "100"}, {"value": "120"}, {"value": "80"}])
    score = drift_score(baseline, shifted)
    assert score > DRIFT_SCORE_THRESHOLD


def test_drift_score_rises_with_a_null_rate_jump():
    baseline = input_signature([{"value": "10"}, {"value": "12"}])
    degraded = input_signature([{"value": "10"}, {"value": None}, {"value": None}])
    score = drift_score(baseline, degraded)
    assert score > 0.0


def test_drift_score_rises_with_a_categorical_distribution_flip():
    baseline = input_signature([{"brand": "acme"}] * 9 + [{"brand": "globex"}])
    flipped = input_signature([{"brand": "globex"}] * 9 + [{"brand": "acme"}])
    score = drift_score(baseline, flipped)
    assert score > DRIFT_SCORE_THRESHOLD


def test_drift_score_scores_a_field_present_on_only_one_side_as_maximal():
    baseline = input_signature([{"value": "10"}])
    current = input_signature([{"value": "10", "extra": "x"}])
    # `value` contributes 0 (identical), `extra` contributes 1 (only on one side) -> mean 0.5.
    assert drift_score(baseline, current) == pytest.approx(0.5)


def test_drift_score_scores_a_kind_change_as_maximal():
    baseline = input_signature([{"value": "10"}, {"value": "12"}])
    current = input_signature([{"value": "not-a-number"}, {"value": "also-not"}])
    assert drift_score(baseline, current) == 1.0


# ---------------------------------------------------------------------------
# quality_verdict -- pure
# ---------------------------------------------------------------------------


def test_quality_verdict_unknown_with_neither_signal():
    assert quality_verdict(None, None) == "unknown"


def test_quality_verdict_healthy_when_both_signals_are_good():
    assert quality_verdict(0.95, 0.04) == "healthy"


def test_quality_verdict_degraded_by_golden_pass_rate_alone():
    assert quality_verdict(0.6, None) == "degraded"


def test_quality_verdict_degraded_by_drift_alone_with_no_golden_set():
    """The D14 Verify line's own point: a use case with no golden set yet must still be
    answerable by drift alone, not fall back to 'unknown' just because R15 has not run."""
    assert quality_verdict(None, DRIFT_SCORE_THRESHOLD) == "degraded"
    assert quality_verdict(None, DRIFT_SCORE_THRESHOLD - 0.01) == "healthy"


def test_quality_verdict_thresholds_are_the_documented_defaults():
    assert quality_verdict(GOLDEN_PASS_THRESHOLD, None) == "healthy"
    assert quality_verdict(GOLDEN_PASS_THRESHOLD - 0.01, None) == "degraded"


# ---------------------------------------------------------------------------
# schedule_run_records -- pure
# ---------------------------------------------------------------------------


def test_schedule_run_records_maps_a_real_poller_failure():
    """The whole reason this function exists: `ScheduleExecutor` catches a scheduled
    workflow's exception and writes `ScheduleRun.status = "failed"`, which is where that
    failure actually lands -- `workflow.arun()`'s own non-streaming path re-raises past the
    code that would otherwise persist a `WorkflowSession`, so nothing is ever written there."""
    failed = SimpleNamespace(
        run_id=None,
        status="failed",
        triggered_at=int((NOW - timedelta(hours=1)).timestamp()),
    )
    succeeded = SimpleNamespace(
        run_id="wf-run-1",
        status="success",
        triggered_at=int((NOW - timedelta(days=1)).timestamp()),
    )
    records = schedule_run_records([failed, succeeded])
    assert records[0].status == RunStatus.error
    assert records[1].status == RunStatus.completed
    assert records[1].run_id == "wf-run-1"


def test_schedule_run_records_drops_a_run_never_actually_triggered():
    never_triggered = SimpleNamespace(run_id=None, status="running", triggered_at=None)
    assert schedule_run_records([never_triggered]) == ()


def test_schedule_run_records_defaults_an_unrecognised_status_to_error():
    mystery = SimpleNamespace(
        run_id=None, status="a-future-status", triggered_at=int(NOW.timestamp())
    )
    [record] = schedule_run_records([mystery])
    assert record.status == RunStatus.error


# ---------------------------------------------------------------------------
# assess_execution_health / list_silent_schedules -- executor wiring, mocked db
# ---------------------------------------------------------------------------


class _FakeControlPlaneDb:
    def __init__(self, sessions_by_workflow):
        self._sessions_by_workflow = sessions_by_workflow
        self.calls: List[dict] = []

    def get_sessions(self, *, session_type=None, component_id=None, limit=None):
        self.calls.append({"session_type": session_type, "component_id": component_id})
        assert session_type == SessionType.WORKFLOW
        return self._sessions_by_workflow.get(component_id, [])


def test_assess_execution_health_reads_the_named_workflows_run_history(monkeypatch):
    import egpt_retail.executors.monitoring as monitoring_module

    session = SimpleNamespace(
        runs=[_fake_run("r1", RunStatus.completed, NOW - timedelta(hours=1))]
    )
    fake_db = _FakeControlPlaneDb({"rfq-funnel": [session]})
    monkeypatch.setattr(
        monitoring_module, "build_control_plane_db", lambda settings: fake_db
    )

    output = asyncio.run(
        assess_execution_health(
            StepInput(
                additional_data={
                    "now": NOW.isoformat(),
                    "use_cases": [
                        {
                            "use_case_id": "uc_rfq_0001",
                            "title": "RFQ funnel",
                            "version": 3,
                            "workflow_id": "rfq-funnel",
                        }
                    ],
                }
            )
        )
    )
    assert fake_db.calls == [
        {"session_type": SessionType.WORKFLOW, "component_id": "rfq-funnel"}
    ]
    [health] = output.content["use_cases"]
    assert health["use_case_id"] == "uc_rfq_0001"
    assert health["last_run_status"] == "succeeded"
    assert health["runs_7d"] == 1
    assert health["silent"] is False  # no schedule_cron given -> never silent


def test_assess_execution_health_flags_silent_from_a_stale_cadence(monkeypatch):
    import egpt_retail.executors.monitoring as monitoring_module

    fake_db = _FakeControlPlaneDb({"nightly-refresh": []})
    monkeypatch.setattr(
        monitoring_module, "build_control_plane_db", lambda settings: fake_db
    )

    output = asyncio.run(
        assess_execution_health(
            StepInput(
                additional_data={
                    "now": NOW.isoformat(),
                    "use_cases": [
                        {
                            "use_case_id": "uc_0002",
                            "title": "Supplier reliability watch",
                            "version": 1,
                            "workflow_id": "nightly-refresh",
                            "schedule_cron": "0 1 * * *",
                        }
                    ],
                }
            )
        )
    )
    [health] = output.content["use_cases"]
    assert health["silent"] is True
    assert health["runs_7d"] == 0


def test_assess_execution_health_merges_schedule_run_failures_via_schedule_name(
    monkeypatch,
):
    """A hard step failure never reaches `WorkflowSession` (see `schedule_run_records`'s own
    docstring) -- so this is the scenario that actually proves execution health can see 'let
    a run fail' at all: the workflow session shows only the prior success, and the failure
    is visible exclusively through the schedule's own run history."""
    import egpt_retail.executors.monitoring as monitoring_module

    session = SimpleNamespace(
        runs=[_fake_run("r1", RunStatus.completed, NOW - timedelta(days=1))]
    )
    fake_db = _FakeControlPlaneDb({"nightly-refresh": [session]})
    schedule = _FakeSchedule("sch_nightly", "nightly-refresh", "0 1 * * *")
    fake_manager = _FakeScheduleManager(
        [schedule],
        runs_by_id={
            "sch_nightly": [
                _FakeScheduleRun("failed", int((NOW - timedelta(hours=2)).timestamp()))
            ]
        },
    )
    monkeypatch.setattr(
        monitoring_module, "build_control_plane_db", lambda settings: fake_db
    )
    monkeypatch.setattr(monitoring_module, "ScheduleManager", lambda db: fake_manager)

    output = asyncio.run(
        assess_execution_health(
            StepInput(
                additional_data={
                    "now": NOW.isoformat(),
                    "use_cases": [
                        {
                            "use_case_id": "uc_nightly_refresh",
                            "title": "Nightly refresh",
                            "version": 1,
                            "workflow_id": "nightly-refresh",
                            "schedule_name": "nightly-refresh",
                        }
                    ],
                }
            )
        )
    )
    [health] = output.content["use_cases"]
    assert (
        health["runs_7d"] == 2
    )  # the prior WorkflowSession success + the ScheduleRun failure
    assert health["failures_7d"] == 1
    assert health["last_run_status"] == "failed"
    assert health["silent"] is False  # the ScheduleRun failure counts as "it did fire"


class _FakeSchedule:
    def __init__(self, id, name, cron_expr, timezone="UTC"):
        self.id = id
        self.name = name
        self.cron_expr = cron_expr
        self.timezone = timezone


class _FakeScheduleRun:
    def __init__(self, status, triggered_at):
        self.status = status
        self.triggered_at = triggered_at


class _FakeScheduleManager:
    def __init__(self, schedules, runs_by_id):
        self._schedules = schedules
        self._runs_by_id = runs_by_id
        self.closed = False

    def list(self, enabled=None, limit=100, page=1):
        return self._schedules

    def get_runs(self, schedule_id, limit=20, page=1):
        return self._runs_by_id.get(schedule_id, [])

    def close(self):
        self.closed = True


def test_list_silent_schedules_flags_a_disabled_schedule_past_its_window(monkeypatch):
    import egpt_retail.executors.monitoring as monitoring_module

    schedule = _FakeSchedule("sch_0002", "supplier-reliability-weekly", "0 1 * * *")
    fake_manager = _FakeScheduleManager([schedule], runs_by_id={})
    monkeypatch.setattr(
        monitoring_module, "build_control_plane_db", lambda settings: object()
    )
    monkeypatch.setattr(monitoring_module, "ScheduleManager", lambda db: fake_manager)

    output = asyncio.run(
        list_silent_schedules(
            StepInput(
                additional_data={
                    "now": NOW.isoformat(),
                    "schedules": [
                        {
                            "name": "supplier-reliability-weekly",
                            "use_case_id": "uc_0002",
                        }
                    ],
                }
            )
        )
    )
    assert fake_manager.closed is True
    [silent] = output.content["silent_schedules"]
    assert silent["schedule_id"] == "sch_0002"
    assert silent["use_case_id"] == "uc_0002"
    assert silent["last_fired_at"] is None


def test_list_silent_schedules_is_quiet_once_a_success_lands(monkeypatch):
    import egpt_retail.executors.monitoring as monitoring_module

    schedule = _FakeSchedule("sch_0001", "nightly-refresh-daily", "0 1 * * *")
    fresh_success_epoch = int((NOW - timedelta(hours=1)).timestamp())
    fake_manager = _FakeScheduleManager(
        [schedule],
        runs_by_id={"sch_0001": [_FakeScheduleRun("success", fresh_success_epoch)]},
    )
    monkeypatch.setattr(
        monitoring_module, "build_control_plane_db", lambda settings: object()
    )
    monkeypatch.setattr(monitoring_module, "ScheduleManager", lambda db: fake_manager)

    output = asyncio.run(
        list_silent_schedules(
            StepInput(
                additional_data={
                    "now": NOW.isoformat(),
                    "schedules": [{"name": "nightly-refresh-daily"}],
                }
            )
        )
    )
    assert output.content["silent_schedules"] == []


def test_list_silent_schedules_skips_a_name_nobody_registered(monkeypatch):
    import egpt_retail.executors.monitoring as monitoring_module

    fake_manager = _FakeScheduleManager([], runs_by_id={})
    monkeypatch.setattr(
        monitoring_module, "build_control_plane_db", lambda settings: object()
    )
    monkeypatch.setattr(monitoring_module, "ScheduleManager", lambda db: fake_manager)

    output = asyncio.run(
        list_silent_schedules(
            StepInput(
                additional_data={
                    "now": NOW.isoformat(),
                    "schedules": [{"name": "not-a-real-schedule"}],
                }
            )
        )
    )
    assert output.content["silent_schedules"] == []


# ---------------------------------------------------------------------------
# assess_quality -- no I/O
# ---------------------------------------------------------------------------


def test_assess_quality_degraded_from_drift_alone_output_text_unchanged():
    """The D14 Verify line's second scenario: leave a run's output unchanged but degrade its
    input data. `assess_quality` never looks at delivered text at all -- it is handed row
    samples -- so an unchanged summary cannot mask a real shift in the rows behind it."""
    baseline_rows = [
        {"brand": "acme", "value": "100"},
        {"brand": "globex", "value": "80"},
    ]
    degraded_rows = [
        {"brand": "acme", "value": "5"},
        {"brand": "globex", "value": None},
    ]
    output = asyncio.run(
        assess_quality(
            StepInput(
                additional_data={
                    "now": NOW.isoformat(),
                    "use_cases": [
                        {
                            "use_case_id": "uc_rfq_0001",
                            "version": 4,
                            "baseline_rows": baseline_rows,
                            "current_rows": degraded_rows,
                        }
                    ],
                }
            )
        )
    )
    [quality] = output.content["use_cases"]
    assert quality["verdict"] == "degraded"
    assert quality["golden_pass_rate"] is None
    assert quality["drift_score"] > DRIFT_SCORE_THRESHOLD
    assert quality["notes"]  # a reason is filled in when none was given


def test_assess_quality_healthy_with_no_baseline_to_compare_and_a_good_golden_rate():
    output = asyncio.run(
        assess_quality(
            StepInput(
                additional_data={
                    "now": NOW.isoformat(),
                    "use_cases": [
                        {
                            "use_case_id": "uc_0001",
                            "version": 3,
                            "golden_pass_rate": 0.95,
                        }
                    ],
                }
            )
        )
    )
    [quality] = output.content["use_cases"]
    assert quality["verdict"] == "healthy"
    assert quality["drift_score"] is None


def test_assess_quality_unknown_with_no_golden_set_and_nothing_to_diff():
    output = asyncio.run(
        assess_quality(
            StepInput(
                additional_data={
                    "now": NOW.isoformat(),
                    "use_cases": [{"use_case_id": "uc_0002", "version": 1}],
                }
            )
        )
    )
    [quality] = output.content["use_cases"]
    assert quality["verdict"] == "unknown"


# ---------------------------------------------------------------------------
# Registry conformance and the metadata table
# ---------------------------------------------------------------------------


def test_the_three_executors_satisfy_the_convention_and_are_listed():
    assert check_registry(list(R14_EXECUTORS)) == ()
    for executor in R14_EXECUTORS:
        assert inspect.iscoroutinefunction(executor)
        assert f"| `{executor.__name__}` |" in _REGISTRY_DOC.read_text(encoding="utf-8")


def test_r14_executors_are_in_the_live_registry_and_metadata():
    names = {f.__name__ for f in build_registry().functions}
    assert {executor.__name__ for executor in R14_EXECUTORS} <= names
    for executor in R14_EXECUTORS:
        assert executor.__name__ in EXECUTOR_METADATA
        assert EXECUTOR_METADATA[executor.__name__].permits_or_blocks is False
