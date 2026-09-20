"""``suggest_kpis`` (contract 1.2.9), DB-free: the ``harvest-source`` workflow's second step.

The suggestion pipeline itself (the agents, the timeout, the phase reporting) is the KPI
router's and is tested there; the run row and its SQL are proved against Postgres by
``test_kpi_suggestions_live.py``. What is tested here is what this step adds: it starts one run
scoped to the harvested source, hands it to the router's executor, reports what happened, and
-- the property that matters -- never turns a failed suggestion run into a failed harvest.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from egpt.workflow.types import StepInput

from egpt_retail.api.routers import kpis as kpis_router
from egpt_retail.executors import kpi_suggestions as step_module
from egpt_retail.executors.kpi_suggestions import suggest_kpis
from egpt_retail.suggestions.runs import SuggestionRunRecord, SuggestionRunStatus


class _RunStore:
    def __init__(self, *, fail_create: Optional[BaseException] = None) -> None:
        self.rows: Dict[str, SuggestionRunRecord] = {}
        self.created_with: List[tuple] = []
        self._fail_create = fail_create

    def create(self, business_area, business_areas=(), ask=None, source_id=None):
        if self._fail_create is not None:
            raise self._fail_create
        self.created_with.append((business_area, tuple(business_areas), ask, source_id))
        record = SuggestionRunRecord(
            identifier=f"sr_{len(self.rows) + 1}",
            status=SuggestionRunStatus.QUEUED,
            business_area=business_area,
            created_at=datetime.now(timezone.utc),
            source_id=source_id,
        )
        self.rows[record.identifier] = record
        return record

    def get(self, identifier, *, stale_after=None):
        return self.rows.get(identifier)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    """The router's helpers replaced: a run store, a registry snapshot, and an executor whose
    outcome a test chooses."""
    store = _RunStore()
    calls: Dict[str, Any] = {"executed": []}
    outcome: Dict[str, Any] = {"suggestions": 2, "error": None}

    monkeypatch.setattr(
        kpis_router, "_suggestion_registry_snapshot", lambda: "registry-snapshot"
    )
    monkeypatch.setattr(
        kpis_router,
        "_with_run_store",
        lambda settings, action: action(calls.get("store", store)),
    )

    async def _execute(run_id, settings, snapshot, business_area, *args, **kwargs):
        calls["executed"].append(
            {"run_id": run_id, "snapshot": snapshot, "args": args, **kwargs}
        )
        active = calls.get("store", store)
        record = active.rows[run_id]
        if outcome["error"] is not None:
            active.rows[run_id] = replace(
                record, status=SuggestionRunStatus.FAILED, error=outcome["error"]
            )
        else:
            active.rows[run_id] = replace(
                record,
                status=SuggestionRunStatus.SUCCEEDED,
                result={"suggestions": [{}] * outcome["suggestions"], "notes": ""},
            )

    monkeypatch.setattr(kpis_router, "_execute_suggestion_run", _execute)
    monkeypatch.delenv("EGPT_RETAIL_SUGGEST_AFTER_HARVEST", raising=False)
    return SimpleWired(store, calls, outcome)


class SimpleWired:
    def __init__(self, store, calls, outcome):
        self.store = store
        self.calls = calls
        self.outcome = outcome


def _step(source_id: Optional[str] = "ds_1") -> StepInput:
    return StepInput(additional_data={"source_id": source_id} if source_id else {})


def _run(step_input: StepInput):
    return asyncio.run(suggest_kpis(step_input))


class TestTheStepStartsOneRunForTheSource:
    def test_it_records_a_run_for_the_source_and_executes_it_scoped_to_it(self, wired):
        output = _run(_step("ds_1"))

        assert wired.store.created_with == [(None, (), None, "ds_1")]
        (executed,) = wired.calls["executed"]
        assert executed["run_id"] == "sr_1"
        assert executed["snapshot"] == "registry-snapshot"
        assert executed["source_id"] == "ds_1"
        assert output.content == {
            "source_id": "ds_1",
            "suggestion_run_id": "sr_1",
            "status": "succeeded",
            "suggestions": 2,
        }

    def test_the_step_reports_success_and_does_not_stop_the_workflow(self, wired):
        output = _run(_step())
        assert output.success is True
        assert not output.stop

    def test_each_harvest_starts_its_own_run(self, wired):
        _run(_step("ds_1"))
        _run(_step("ds_2"))
        assert [c[3] for c in wired.store.created_with] == ["ds_1", "ds_2"]


class TestAFailedSuggestionRunIsNotAFailedHarvest:
    def test_a_run_that_fails_leaves_the_step_successful(self, wired):
        wired.outcome["error"] = "The suggestion run failed (RuntimeError)."
        output = _run(_step())
        assert output.success is True
        assert not output.stop
        assert output.error is None
        assert output.content["status"] == "failed"
        assert output.content["suggestions"] == 0

    def test_a_run_that_cannot_even_be_recorded_leaves_the_step_successful(
        self, wired, caplog: pytest.LogCaptureFixture
    ):
        wired.calls["store"] = _RunStore(fail_create=RuntimeError("db down"))
        output = _run(_step())
        assert output.success is True
        assert output.content["status"] == "not_started"
        assert wired.calls["executed"] == []  # nothing to execute without a row
        # The one failure with no row to say so is the one that is logged, with the traceback.
        assert any(r.exc_info for r in caplog.records)

    def test_the_failure_text_never_carries_the_exception_message(self, wired):
        wired.calls["store"] = _RunStore(
            fail_create=RuntimeError("password=hunter2 at db.internal")
        )
        output = _run(_step())
        assert "hunter2" not in str(output.content)
        assert "db.internal" not in str(output.content)

    def test_a_registry_snapshot_that_cannot_be_read_leaves_the_step_successful(
        self, wired, monkeypatch: pytest.MonkeyPatch
    ):
        def _boom():
            raise RuntimeError("control plane unreachable")

        monkeypatch.setattr(kpis_router, "_suggestion_registry_snapshot", _boom)
        output = _run(_step())
        assert output.success is True
        assert output.content["status"] == "not_started"
        assert wired.calls["executed"] == []


class TestSkipped:
    def test_no_source_id_does_nothing(self, wired):
        output = _run(_step(None))
        assert output.content["status"] == "skipped"
        assert wired.store.created_with == []
        assert wired.calls["executed"] == []

    def test_the_setting_turns_it_off_without_touching_anything(
        self, wired, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("EGPT_RETAIL_SUGGEST_AFTER_HARVEST", "false")
        output = _run(_step("ds_1"))
        assert output.content["status"] == "skipped"
        assert "EGPT_RETAIL_SUGGEST_AFTER_HARVEST" in output.content["detail"]
        assert output.success is True
        assert wired.store.created_with == []
        assert wired.calls["executed"] == []

    def test_it_is_on_by_default(self):
        from egpt_retail.app.settings import RetailSettings

        assert RetailSettings().retail_suggest_after_harvest is True


def test_the_step_is_named_the_way_the_contract_and_workflow_say():
    assert step_module.suggest_kpis.__name__.replace("_", "-") == "suggest-kpis"
