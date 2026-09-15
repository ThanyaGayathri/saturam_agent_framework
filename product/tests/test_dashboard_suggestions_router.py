"""Unit tests for `POST /dashboards/suggestions` + `GET /dashboards/suggestions/{run_id}` --
the run-then-poll shape mirrored from #254's `test_kpis_router.py` suggestion-run section. The
run row is faked in memory with the store's own transition rules; `_build_dashboard_suggestion`
is faked the same way `test_kpis_router.py` fakes `_list_kpi_suggestions`, so these tests never
touch a real model, Postgres, or Neo4j. The store's own SQL lives in `dashboard_runs.py` and is
exercised only by `test_dashboard_suggestions_live.py` (gated `EGPT_INTEGRATION_POSTGRES`).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from egpt_retail.api import models as wire_models
from egpt_retail.api.routers import dashboards as dashboards_module
from egpt_retail.api.routers.dashboards import _DashboardSuggestionSnapshot
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.contracts.vocabulary import load_vocabulary_set
from egpt_retail.skills import dashboard_suggestion as dsg
from egpt_retail.suggestions.dashboard_runs import (
    LOST_RUN_ERROR,
    DashboardSuggestionRunRecord,
    DashboardSuggestionRunStatus,
)

_VOCABULARIES = load_vocabulary_set()


def _kpi(**overrides) -> KpiDefinition:
    defaults = dict(
        identifier="session__active_analytics_user_count",
        name="Active analytics user count",
        description="Distinct analytics users active in the period.",
        sql="SELECT count(DISTINCT user_id) FROM sessions",
        grain="overall",
        business_area="marketing",
        source_tables=frozenset({"sessions"}),
    )
    defaults.update(overrides)
    return KpiDefinition(**defaults)


def _suggestion(**overrides) -> wire_models.DashboardSuggestion:
    defaults = dict(
        name="Weekly engagement",
        charts=[
            wire_models.DashboardChartSuggestion(
                title="Active analytics users",
                chart=wire_models.ChartKind.line,
                source=wire_models.TileSource.kpi,
                kpi_identifier="session__active_analytics_user_count",
                description="Distinct users active in the week.",
                rationale="Directly measures engagement.",
                guard=wire_models.QueryGuardVerdict(
                    status=wire_models.QueryGuardStatus.ok, detail=""
                ),
            )
        ],
        filters=[],
        notes="",
    )
    defaults.update(overrides)
    return wire_models.DashboardSuggestion(**defaults)


def _context(**overrides) -> dsg.SuggestionContext:
    defaults = dict(kpis=(), tables=(), joins=(), chart_budget=None)
    defaults.update(overrides)
    return dsg.SuggestionContext(**defaults)


class _FakeDashboardSuggestionRunStore:
    """`DashboardSuggestionRunStore` without Postgres: same rows, same guarded transitions."""

    _OPEN = (DashboardSuggestionRunStatus.QUEUED, DashboardSuggestionRunStatus.RUNNING)

    def __init__(self):
        self.rows: dict = {}

    def create(self, purpose):
        record = DashboardSuggestionRunRecord(
            identifier=f"dsr_{len(self.rows) + 1}",
            status=DashboardSuggestionRunStatus.QUEUED,
            purpose=purpose,
            created_at=datetime.now(timezone.utc),
        )
        self.rows[record.identifier] = record
        return record

    def _set(self, identifier, **changes):
        self.rows[identifier] = replace(self.rows[identifier], **changes)

    def mark_running(self, identifier):
        if self.rows[identifier].status is DashboardSuggestionRunStatus.QUEUED:
            self._set(
                identifier,
                status=DashboardSuggestionRunStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )

    def mark_stage(self, identifier, stage):
        if self.rows[identifier].status in self._OPEN:
            self._set(identifier, stage=stage)

    def mark_succeeded(self, identifier, result):
        if self.rows[identifier].status in self._OPEN:
            self._set(
                identifier,
                status=DashboardSuggestionRunStatus.SUCCEEDED,
                result=dict(result),
                finished_at=datetime.now(timezone.utc),
                stage="done",
            )

    def mark_failed(self, identifier, error):
        if self.rows[identifier].status in self._OPEN:
            self._set(
                identifier,
                status=DashboardSuggestionRunStatus.FAILED,
                error=error,
                finished_at=datetime.now(timezone.utc),
            )

    def get(self, identifier, *, stale_after=None):
        record = self.rows.get(identifier)
        if record is None:
            return None
        if (
            stale_after is not None
            and record.status in self._OPEN
            and record.created_at < datetime.now(timezone.utc) - stale_after
        ):
            self.mark_failed(identifier, LOST_RUN_ERROR)
        return self.rows[identifier]


def _run_client(monkeypatch, store, *, authorization=False, scopes=(), ready_kpis=()):
    @contextmanager
    def _fake_open_run_store(settings):
        yield store

    monkeypatch.setattr(
        dashboards_module, "_open_suggestion_run_store", _fake_open_run_store
    )
    # The context walk opens a semantic reader; these tests never have one. What it would
    # have built is handed straight to `_build_dashboard_suggestion`, which is faked too.
    monkeypatch.setattr(
        dashboards_module,
        "_build_context",
        lambda settings, snapshot, **kw: _context(kpis=snapshot.ready_kpis),
    )

    app = FastAPI()

    @app.middleware("http")
    async def _fake_auth(request: Request, call_next):
        request.state.authorization_enabled = authorization
        request.state.scopes = list(scopes)
        request.state.admin_scope = None
        return await call_next(request)

    app.include_router(dashboards_module.router)
    app.dependency_overrides[dashboards_module._dashboard_suggestion_snapshot] = (
        lambda: _DashboardSuggestionSnapshot(
            ready_kpis=tuple(ready_kpis), vocabularies=_VOCABULARIES
        )
    )
    return TestClient(app)


def _drain(client):
    """Wait for every executor task the app's loop has in flight."""
    client.portal.call(dashboards_module._await_dashboard_suggestion_runs)


def _suggesting(suggestion):
    def _fake_helper(
        settings, *, snapshot, purpose, agent=None, context=None, on_stage=None
    ):
        if on_stage:
            on_stage("drafting")
        return suggestion

    return _fake_helper


def test_start_dashboard_suggestion_run_returns_202_queued_then_the_poll_reaches_succeeded(
    monkeypatch,
):
    monkeypatch.setattr(
        dashboards_module, "_build_dashboard_suggestion", _suggesting(_suggestion())
    )
    store = _FakeDashboardSuggestionRunStore()

    with _run_client(monkeypatch, store) as client:
        started = client.post(
            "/dashboards/suggestions",
            json={"purpose": "Track weekly engagement across our APAC accounts."},
        )
        assert started.status_code == 202
        body = started.json()
        assert body["status"] == "queued"
        assert body["purpose"] == "Track weekly engagement across our APAC accounts."
        assert body["result"] is None and body["error"] is None
        run_id = body["id"]

        _drain(client)

        polled = client.get(f"/dashboards/suggestions/{run_id}")

    assert polled.status_code == 200
    done = polled.json()
    assert done["status"] == "succeeded"
    assert done["started_at"] is not None and done["finished_at"] is not None
    assert done["error"] is None
    assert done["stage"] == "done"
    assert done["result"]["name"] == "Weekly engagement"
    assert [c["kpi_identifier"] for c in done["result"]["charts"]] == [
        "session__active_analytics_user_count"
    ]
    assert done["result"]["charts"][0]["source"] == "kpi"
    assert done["result"]["charts"][0]["guard"]["status"] == "ok"


def test_start_dashboard_suggestion_run_passes_the_selection_through_to_the_context(
    monkeypatch,
):
    seen = {}

    def _fake_context(settings, snapshot, **kw):
        seen.update(kw)
        return _context(kpis=snapshot.ready_kpis)

    monkeypatch.setattr(
        dashboards_module, "_build_dashboard_suggestion", _suggesting(_suggestion())
    )
    store = _FakeDashboardSuggestionRunStore()

    with _run_client(monkeypatch, store, ready_kpis=(_kpi(),)) as client:
        monkeypatch.setattr(dashboards_module, "_build_context", _fake_context)
        started = client.post(
            "/dashboards/suggestions",
            json={
                "purpose": "Where are stock-outs hurting margin?",
                "kpi_identifiers": ["session__active_analytics_user_count"],
                "table_names": ["orders", " products "],
                "chart_budget": 3,
            },
        )
        assert started.status_code == 202
        _drain(client)

    assert seen["kpi_identifiers"] == ["session__active_analytics_user_count"]
    assert seen["table_names"] == ["orders", "products"]
    assert seen["chart_budget"] == 3


def test_start_dashboard_suggestion_run_422s_on_a_kpi_outside_the_ready_set(
    monkeypatch,
):
    monkeypatch.setattr(
        dashboards_module, "_build_dashboard_suggestion", _suggesting(_suggestion())
    )
    store = _FakeDashboardSuggestionRunStore()

    with _run_client(monkeypatch, store, ready_kpis=(_kpi(),)) as client:
        response = client.post(
            "/dashboards/suggestions",
            json={"purpose": "anything", "kpi_identifiers": ["not_ready__kpi"]},
        )

    assert response.status_code == 422
    assert "not_ready__kpi" in response.json()["detail"]
    assert store.rows == {}


def test_get_dashboard_suggestion_run_exposes_the_stage_while_running(monkeypatch):
    store = _FakeDashboardSuggestionRunStore()
    record = store.create("in-flight purpose")
    store.mark_running(record.identifier)
    store.mark_stage(record.identifier, "guarding")

    with _run_client(monkeypatch, store) as client:
        polled = client.get(f"/dashboards/suggestions/{record.identifier}")

    assert polled.status_code == 200
    assert polled.json()["status"] == "running"
    assert polled.json()["stage"] == "guarding"


def test_start_dashboard_suggestion_run_422s_on_an_empty_purpose_before_any_row_exists(
    monkeypatch,
):
    monkeypatch.setattr(
        dashboards_module, "_build_dashboard_suggestion", _suggesting(_suggestion())
    )
    store = _FakeDashboardSuggestionRunStore()

    with _run_client(monkeypatch, store) as client:
        response = client.post("/dashboards/suggestions", json={"purpose": "   "})

    assert response.status_code == 422
    assert store.rows == {}, "a 422 must leave no row behind"


def test_get_dashboard_suggestion_run_reports_failure_from_the_executor(monkeypatch):
    def _failing(
        settings, *, snapshot, purpose, agent=None, context=None, on_stage=None
    ):
        raise ValueError("no ready KPI in this registry matches the purpose")

    monkeypatch.setattr(dashboards_module, "_build_dashboard_suggestion", _failing)
    store = _FakeDashboardSuggestionRunStore()

    with _run_client(monkeypatch, store) as client:
        run_id = client.post(
            "/dashboards/suggestions", json={"purpose": "something obscure"}
        ).json()["id"]
        _drain(client)
        polled = client.get(f"/dashboards/suggestions/{run_id}")

    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "failed"
    assert body["result"] is None
    assert "no ready KPI" in body["error"]


def test_get_dashboard_suggestion_run_404s_for_an_unknown_id(monkeypatch):
    store = _FakeDashboardSuggestionRunStore()

    with _run_client(monkeypatch, store) as client:
        response = client.get("/dashboards/suggestions/dsr_missing")

    assert response.status_code == 404


def test_get_dashboard_suggestion_run_closes_a_run_whose_executor_is_gone(monkeypatch):
    store = _FakeDashboardSuggestionRunStore()
    record = store.create("stuck purpose")
    store.mark_running(record.identifier)
    # Age the row well past any real timeout, so the stale guard the read side applies fires.
    store.rows[record.identifier] = replace(
        store.rows[record.identifier],
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )

    with _run_client(monkeypatch, store) as client:
        polled = client.get(f"/dashboards/suggestions/{record.identifier}")

    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "failed"
    assert body["error"] == LOST_RUN_ERROR


def test_get_dashboard_suggestion_run_leaves_a_recent_running_row_alone(monkeypatch):
    store = _FakeDashboardSuggestionRunStore()
    record = store.create("in-flight purpose")
    store.mark_running(record.identifier)

    with _run_client(monkeypatch, store) as client:
        polled = client.get(f"/dashboards/suggestions/{record.identifier}")

    assert polled.status_code == 200
    assert polled.json()["status"] == "running"


# ---------------------------------------------------------------------------
# `_cached_dashboard_suggestion_agent` -- the same reuse-across-requests contract
# `_cached_suggestion_agents` has in `kpis.py` (Finding 4, PR #242 review: CLAUDE.md says
# never create agents in loops). The routes above monkeypatch `_build_dashboard_suggestion`
# entirely, so they never exercise the cache itself -- these do, directly, the same way
# `test_kpis_router.py`'s own two tests of the same shape do for their counterpart.
# ---------------------------------------------------------------------------


def _fake_agent_builder(monkeypatch):
    build_calls = []

    def _fake_build(settings, *, context, vocabularies, db=None):
        agent = object()
        build_calls.append((agent, context))
        return agent

    monkeypatch.setattr(
        dashboards_module, "build_dashboard_suggestion_agent", _fake_build
    )
    monkeypatch.setattr(dashboards_module, "_dash_agent_cache_key", None)
    monkeypatch.setattr(dashboards_module, "_dash_agent_cache_agent", None)
    return build_calls


def test_cached_dashboard_suggestion_agent_reuses_the_same_agent_for_an_unchanged_context(
    monkeypatch,
):
    build_calls = _fake_agent_builder(monkeypatch)
    snapshot = _DashboardSuggestionSnapshot(
        ready_kpis=(_kpi(identifier="a"), _kpi(identifier="b")),
        vocabularies=_VOCABULARIES,
    )
    context = _context(kpis=(_kpi(identifier="a"),), chart_budget=4)
    first = dashboards_module._cached_dashboard_suggestion_agent(
        RetailSettings(), snapshot, context
    )
    second = dashboards_module._cached_dashboard_suggestion_agent(
        RetailSettings(),
        snapshot,
        _context(kpis=(_kpi(identifier="a"),), chart_budget=4),
    )

    assert first is second
    assert len(build_calls) == 1
    assert build_calls[0][1] == context


def test_cached_dashboard_suggestion_agent_rebuilds_when_the_selection_changes(
    monkeypatch,
):
    build_calls = _fake_agent_builder(monkeypatch)
    snapshot = _DashboardSuggestionSnapshot(
        ready_kpis=(_kpi(identifier="a"), _kpi(identifier="b")),
        vocabularies=_VOCABULARIES,
    )
    first = dashboards_module._cached_dashboard_suggestion_agent(
        RetailSettings(), snapshot, _context(kpis=(_kpi(identifier="a"),))
    )
    second = dashboards_module._cached_dashboard_suggestion_agent(
        RetailSettings(),
        snapshot,
        _context(kpis=(_kpi(identifier="a"), _kpi(identifier="b"))),
    )
    third = dashboards_module._cached_dashboard_suggestion_agent(
        RetailSettings(),
        snapshot,
        _context(kpis=(_kpi(identifier="a"), _kpi(identifier="b")), chart_budget=2),
    )

    assert first is not second and second is not third
    assert len(build_calls) == 3


def test_drafts_of_raises_when_the_agent_returns_something_other_than_the_draft_schema():
    """The guard `_build_dashboard_suggestion` relies on before handing a response to
    `dsg.assemble` -- a run that fails structured output (no model configured, a refusal
    string) must not reach `assemble` as if it were a real draft."""

    class _FakeResponse:
        content = "no model configured"

    with pytest.raises(ValueError, match="DashboardSuggestionDraft expected"):
        dashboards_module._drafts_of(_FakeResponse(), dsg.DashboardSuggestionDraft)
