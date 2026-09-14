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

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from egpt_retail.api import models as wire_models
from egpt_retail.api.routers import dashboards as dashboards_module
from egpt_retail.api.routers.dashboards import _DashboardSuggestionSnapshot
from egpt_retail.contracts.vocabulary import load_vocabulary_set
from egpt_retail.suggestions.dashboard_runs import (
    LOST_RUN_ERROR,
    DashboardSuggestionRunRecord,
    DashboardSuggestionRunStatus,
)

_VOCABULARIES = load_vocabulary_set()


def _suggestion(**overrides) -> wire_models.DashboardSuggestion:
    defaults = dict(
        name="Weekly engagement",
        tiles=[
            wire_models.DashboardTileSuggestion(
                kpi_identifier="session__active_analytics_user_count",
                name="Active analytics user count",
                rationale="Directly measures engagement.",
            )
        ],
        filters=[],
        notes="",
    )
    defaults.update(overrides)
    return wire_models.DashboardSuggestion(**defaults)


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

    def mark_succeeded(self, identifier, result):
        if self.rows[identifier].status in self._OPEN:
            self._set(
                identifier,
                status=DashboardSuggestionRunStatus.SUCCEEDED,
                result=dict(result),
                finished_at=datetime.now(timezone.utc),
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


def _run_client(monkeypatch, store, *, authorization=False, scopes=()):
    @contextmanager
    def _fake_open_run_store(settings):
        yield store

    monkeypatch.setattr(
        dashboards_module, "_open_suggestion_run_store", _fake_open_run_store
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
        lambda: _DashboardSuggestionSnapshot(ready_kpis=(), vocabularies=_VOCABULARIES)
    )
    return TestClient(app)


def _drain(client):
    """Wait for every executor task the app's loop has in flight."""
    client.portal.call(dashboards_module._await_dashboard_suggestion_runs)


def _suggesting(suggestion):
    def _fake_helper(settings, *, snapshot, purpose, agent=None):
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
    assert done["result"]["name"] == "Weekly engagement"
    assert [t["kpi_identifier"] for t in done["result"]["tiles"]] == [
        "session__active_analytics_user_count"
    ]


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
    def _failing(settings, *, snapshot, purpose, agent=None):
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
