"""Live regression tests for `DashboardSuggestionRunStore` against real Postgres -- the DDL,
the guarded transitions and the lost-run guard, which `test_dashboard_suggestions_router.py`'s
DB-free suite only fakes. Mirrors `test_kpis_live.py`'s own `SuggestionRunStore` section
exactly, `purpose` standing in for `business_area`.

Locally, against the compose stack (`deploy/compose.yaml`)'s Postgres:

    EGPT_INTEGRATION_POSTGRES=1 pytest product/tests/test_dashboard_suggestions_live.py

Everything written here uses ``purpose = 'livetest'`` and is removed at the end of the module,
by that value -- not by age, the same lesson #256's review left on the KPI suggestion-run
store: the lost-run test ages its own row two hours back, which an age-bounded delete would
leave behind.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Iterator

import pytest

from egpt_retail.suggestions.dashboard_runs import (
    LOST_RUN_ERROR,
    DashboardSuggestionRunStatus,
    DashboardSuggestionRunStore,
)

_LIVE = os.environ.get("EGPT_INTEGRATION_POSTGRES")
pytestmark = pytest.mark.skipif(
    not _LIVE,
    reason="needs EGPT_INTEGRATION_POSTGRES=1 with a live control-plane Postgres",
)

CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)


@pytest.fixture(scope="module")
def control_plane() -> Iterator[Any]:
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(CONTROL_PLANE)
    yield conn
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM semantic.dashboard_suggestion_run WHERE purpose = 'livetest'"
        )
    conn.commit()
    conn.close()


@pytest.fixture
def run_store(control_plane: Any) -> Iterator[DashboardSuggestionRunStore]:
    store = DashboardSuggestionRunStore(control_plane)
    store.ensure_schema()
    store.ensure_schema()  # idempotent: the second boot must not fail
    yield store
    with control_plane.cursor() as cur:
        cur.execute(
            "DELETE FROM semantic.dashboard_suggestion_run WHERE purpose = 'livetest'"
        )
    control_plane.commit()


def test_suggestion_run_store_round_trips_a_run_to_succeeded(
    run_store: DashboardSuggestionRunStore,
) -> None:
    created = run_store.create("livetest")
    assert created.status is DashboardSuggestionRunStatus.QUEUED
    assert created.purpose == "livetest"
    assert created.created_at.tzinfo is not None
    assert created.started_at is None and created.result is None

    run_store.mark_running(created.identifier)
    running = run_store.get(created.identifier)
    assert running is not None
    assert running.status is DashboardSuggestionRunStatus.RUNNING
    assert running.started_at is not None

    payload = {"name": "Weekly", "tiles": [], "filters": [], "notes": ""}
    run_store.mark_succeeded(created.identifier, payload)
    done = run_store.get(created.identifier)
    assert done is not None
    assert done.status is DashboardSuggestionRunStatus.SUCCEEDED
    assert done.result == payload
    assert done.finished_at is not None and done.error is None

    # terminal is terminal: a late failure cannot reopen the row
    run_store.mark_failed(created.identifier, "too late")
    still_done = run_store.get(created.identifier)
    assert still_done is not None
    assert still_done.status is DashboardSuggestionRunStatus.SUCCEEDED


@pytest.mark.anyio
async def test_suggestion_run_store_async_variants_agree_with_their_sync_twins(
    run_store: DashboardSuggestionRunStore,
) -> None:
    """Both variants exist and do the same thing, per the repository's standing rule -- the
    router never calls `aget`/`acreate`/`amark_*`/`aensure_schema` (it threads the sync
    methods itself), so nothing else exercises them -- the same gap #256's review caught on
    `SuggestionRunStore`, closed here before it was ever open."""
    await run_store.aensure_schema()  # idempotent, same as the sync call in the fixture

    created = await run_store.acreate("livetest")
    assert created.status is DashboardSuggestionRunStatus.QUEUED
    assert created.purpose == "livetest"

    await run_store.amark_running(created.identifier)
    running = await run_store.aget(created.identifier)
    assert running is not None
    assert running.status is DashboardSuggestionRunStatus.RUNNING
    assert running.started_at is not None

    payload = {"name": "Weekly", "tiles": [], "filters": [], "notes": ""}
    await run_store.amark_succeeded(created.identifier, payload)
    done = await run_store.aget(created.identifier)
    assert done is not None
    assert done.status is DashboardSuggestionRunStatus.SUCCEEDED
    assert done.result == payload

    # terminal is terminal here too: a late failure cannot reopen the row
    await run_store.amark_failed(created.identifier, "too late")
    still_done = await run_store.aget(created.identifier)
    assert still_done is not None
    assert still_done.status is DashboardSuggestionRunStatus.SUCCEEDED


def test_suggestion_run_store_get_closes_a_lost_run_only_past_the_window(
    run_store: DashboardSuggestionRunStore, control_plane: Any
) -> None:
    created = run_store.create("livetest")
    run_store.mark_running(created.identifier)

    # inside the window: untouched
    fresh = run_store.get(created.identifier, stale_after=timedelta(hours=1))
    assert fresh is not None and fresh.status is DashboardSuggestionRunStatus.RUNNING

    # age the row past any window, then read with one
    with control_plane.cursor() as cur:
        cur.execute(
            "UPDATE semantic.dashboard_suggestion_run SET created_at = now() - interval '2 hours'"
            " WHERE id = %s",
            (created.identifier,),
        )
    control_plane.commit()
    lost = run_store.get(created.identifier, stale_after=timedelta(hours=1))
    assert lost is not None
    assert lost.status is DashboardSuggestionRunStatus.FAILED
    assert lost.error == LOST_RUN_ERROR


def test_suggestion_run_store_unknown_id_returns_none(
    run_store: DashboardSuggestionRunStore,
) -> None:
    assert run_store.get("dsr_does_not_exist") is None
