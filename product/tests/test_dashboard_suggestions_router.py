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
    def _fake_helper(
        settings, *, snapshot, purpose, preselected_kpi_identifiers=(), agent=None
    ):
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


def test_start_dashboard_suggestion_run_threads_preselected_kpis_to_the_builder(
    monkeypatch,
):
    """KPIs the caller already picked on the discovery grid (`preselected_kpi_identifiers`)
    reach `_build_dashboard_suggestion` -- the request field exists only to carry them there;
    see `_compose_suggestion_message`'s own tests for what the agent is actually told."""
    captured = {}

    def _capturing(
        settings, *, snapshot, purpose, preselected_kpi_identifiers=(), agent=None
    ):
        captured["preselected_kpi_identifiers"] = preselected_kpi_identifiers
        return _suggestion()

    monkeypatch.setattr(dashboards_module, "_build_dashboard_suggestion", _capturing)
    store = _FakeDashboardSuggestionRunStore()

    with _run_client(monkeypatch, store) as client:
        client.post(
            "/dashboards/suggestions",
            json={
                "purpose": "Track weekly engagement across our APAC accounts.",
                "preselected_kpi_identifiers": ["session__active_analytics_user_count"],
            },
        )
        _drain(client)

    assert list(captured["preselected_kpi_identifiers"]) == [
        "session__active_analytics_user_count"
    ]


def test_start_dashboard_suggestion_run_defaults_preselected_kpis_to_empty(monkeypatch):
    """The field is optional -- the plain `Create dashboard` entry point (no KPI-page
    selection) omits it entirely."""
    captured = {}

    def _capturing(
        settings, *, snapshot, purpose, preselected_kpi_identifiers=(), agent=None
    ):
        captured["preselected_kpi_identifiers"] = preselected_kpi_identifiers
        return _suggestion()

    monkeypatch.setattr(dashboards_module, "_build_dashboard_suggestion", _capturing)
    store = _FakeDashboardSuggestionRunStore()

    with _run_client(monkeypatch, store) as client:
        client.post(
            "/dashboards/suggestions",
            json={"purpose": "Track weekly engagement across our APAC accounts."},
        )
        _drain(client)

    assert list(captured["preselected_kpi_identifiers"]) == []


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
        settings, *, snapshot, purpose, preselected_kpi_identifiers=(), agent=None
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


def test_cached_dashboard_suggestion_agent_reuses_the_same_agent_for_an_unchanged_ready_set(
    monkeypatch,
):
    build_calls = []

    def _fake_build(settings, *, ready_kpis, vocabularies):
        agent = object()
        build_calls.append(agent)
        return agent

    monkeypatch.setattr(
        dashboards_module, "build_dashboard_suggestion_agent", _fake_build
    )
    monkeypatch.setattr(dashboards_module, "_dash_agent_cache_key", None)
    monkeypatch.setattr(dashboards_module, "_dash_agent_cache_agent", None)

    snapshot = _DashboardSuggestionSnapshot(
        ready_kpis=(_kpi(identifier="a"), _kpi(identifier="b")),
        vocabularies=_VOCABULARIES,
    )
    first = dashboards_module._cached_dashboard_suggestion_agent(
        RetailSettings(), snapshot
    )
    second = dashboards_module._cached_dashboard_suggestion_agent(
        RetailSettings(), snapshot
    )

    assert first is second
    assert len(build_calls) == 1


def test_cached_dashboard_suggestion_agent_rebuilds_when_the_ready_set_changes(
    monkeypatch,
):
    build_calls = []

    def _fake_build(settings, *, ready_kpis, vocabularies):
        agent = object()
        build_calls.append(agent)
        return agent

    monkeypatch.setattr(
        dashboards_module, "build_dashboard_suggestion_agent", _fake_build
    )
    monkeypatch.setattr(dashboards_module, "_dash_agent_cache_key", None)
    monkeypatch.setattr(dashboards_module, "_dash_agent_cache_agent", None)

    first = dashboards_module._cached_dashboard_suggestion_agent(
        RetailSettings(),
        _DashboardSuggestionSnapshot(
            ready_kpis=(_kpi(identifier="a"),), vocabularies=_VOCABULARIES
        ),
    )
    second = dashboards_module._cached_dashboard_suggestion_agent(
        RetailSettings(),
        _DashboardSuggestionSnapshot(
            ready_kpis=(_kpi(identifier="a"), _kpi(identifier="b")),
            vocabularies=_VOCABULARIES,
        ),
    )

    assert first is not second
    assert len(build_calls) == 2


def test_drafts_of_raises_when_the_agent_returns_something_other_than_the_draft_schema():
    """The guard `_build_dashboard_suggestion` relies on before handing a response to
    `dsg.assemble` -- a run that fails structured output (no model configured, a refusal
    string) must not reach `assemble` as if it were a real draft."""

    class _FakeResponse:
        content = "no model configured"

    with pytest.raises(ValueError, match="DashboardSuggestionDraft expected"):
        dashboards_module._drafts_of(_FakeResponse(), dsg.DashboardSuggestionDraft)


# ---------------------------------------------------------------------------
# `_compose_suggestion_message` -- how a KPI-page selection reaches the agent. It rides in the
# per-run message, not the agent's own instructions: those are built once and cached per ready
# set (the section above), shared across concurrent requests, so they cannot carry one
# caller's selection.
# ---------------------------------------------------------------------------


def test_compose_suggestion_message_is_unchanged_purpose_with_no_preselection():
    assert (
        dashboards_module._compose_suggestion_message(
            "Track engagement.", (), (_kpi(),)
        )
        == "Track engagement."
    )


def test_compose_suggestion_message_names_preselected_ready_kpis():
    kpis = (
        _kpi(identifier="a", name="Metric A"),
        _kpi(identifier="b", name="Metric B"),
    )
    message = dashboards_module._compose_suggestion_message(
        "Track engagement.", ["a", "b"], kpis
    )
    assert message.startswith("Track engagement.\n\n")
    assert "Metric A, Metric B" in message


def test_compose_suggestion_message_drops_identifiers_the_ready_set_does_not_have():
    """An identifier the frontend sent that is no longer ready (or never was) is dropped, not
    surfaced as an error -- naming a KPI outside the agent's own closed list (section 3 of the
    prompt) would only confuse it, not help it."""
    kpis = (_kpi(identifier="a", name="Metric A"),)
    message = dashboards_module._compose_suggestion_message(
        "Track engagement.", ["a", "no-longer-ready"], kpis
    )
    assert (
        message
        == "Track engagement.\n\nAlready selected by the caller before asking for this suggestion -- include these unless clearly unrelated to the purpose above: Metric A."
    )


def test_compose_suggestion_message_falls_back_to_the_bare_purpose_when_none_are_ready():
    kpis = (_kpi(identifier="a"),)
    assert (
        dashboards_module._compose_suggestion_message(
            "Track engagement.", ["not-ready"], kpis
        )
        == "Track engagement."
    )


def test_compose_suggestion_message_dedupes_repeated_identifiers():
    kpis = (_kpi(identifier="a", name="Metric A"),)
    message = dashboards_module._compose_suggestion_message(
        "Track engagement.", ["a", "a"], kpis
    )
    assert message.count("Metric A") == 1


def test_build_dashboard_suggestion_sends_the_composed_message_to_the_agent():
    """End to end through the real seam (the `agent=` override `_build_dashboard_suggestion`
    documents as test-only): a preselected KPI reaches `.run()`, not just the helper that
    composes it."""

    class _FakeResponse:
        def __init__(self, content):
            self.content = content

    class _FakeAgent:
        def __init__(self):
            self.received = None

        def run(self, message):
            self.received = message
            return _FakeResponse(
                dsg.DashboardSuggestionDraft(
                    name="Weekly engagement",
                    tiles=[
                        dsg.DashboardTileDraft(
                            kpi_identifier="a", rationale="Directly measures it."
                        )
                    ],
                )
            )

    kpi = _kpi(identifier="a", name="Metric A")
    fake_agent = _FakeAgent()
    dashboards_module._build_dashboard_suggestion(
        RetailSettings(),
        snapshot=_DashboardSuggestionSnapshot(
            ready_kpis=(kpi,), vocabularies=_VOCABULARIES
        ),
        purpose="Track engagement.",
        preselected_kpi_identifiers=["a"],
        agent=fake_agent,
    )

    assert fake_agent.received == (
        "Track engagement.\n\n"
        "Already selected by the caller before asking for this suggestion -- include these "
        "unless clearly unrelated to the purpose above: Metric A."
    )
