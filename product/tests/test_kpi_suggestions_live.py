"""Persistent KPI suggestions (contract 1.2.9) against real Postgres.

``SuggestionRunStore.current()`` is what decides which run's suggestions a person is shown, and
``SuggestionDismissalStore`` is what keeps a removed suggestion removed. Both are SQL --
``DISTINCT ON`` per scope, a stale-run guard, an upsert that returns the existing row -- so
they are proved here, on a real database, in the suite ``integration.yml`` runs. The route's own
merging and filtering is ``test_kpis_router.py``; the workflow step is
``test_kpi_suggestions_step.py``.

Gated like ``test_r15_rehearsal_live.py``: ``EGPT_INTEGRATION_POSTGRES``, skipped otherwise.
The control-plane DSN is ``EGPT_RETAIL_CP_DSN`` (default ``postgresql://ai:ai@localhost:5532/ai``).
Everything written is keyed to ``livetest_`` (a source id, or a run's ``ask``, or an
identifier) and removed after each test. The database may hold real runs of its own, so a test
only ever asserts about its own rows.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Iterator, List, Optional

import pytest

from egpt_retail.suggestions.dismissals import (
    SuggestionDismissalStore,
    normalise_identifier,
)
from egpt_retail.suggestions.runs import (
    LOST_RUN_ERROR,
    SuggestionRunStatus,
    SuggestionRunStore,
)

_LIVE = os.environ.get("EGPT_INTEGRATION_POSTGRES")
pytestmark = pytest.mark.skipif(
    not _LIVE, reason="needs EGPT_INTEGRATION_POSTGRES=1 with a live Postgres"
)

DSN = os.environ.get("EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai")


@pytest.fixture
def env() -> Iterator[SimpleNamespace]:
    psycopg = pytest.importorskip("psycopg")
    connection = psycopg.connect(DSN)
    runs = SuggestionRunStore(connection)
    runs.ensure_schema()
    dismissals = SuggestionDismissalStore(connection)
    dismissals.ensure_schema()
    marker = f"livetest_{uuid.uuid4().hex[:10]}"

    def source(name: str) -> str:
        return f"{marker}_{name}"

    def run(
        source_id: Optional[str],
        *,
        outcome: Optional[str] = "succeeded",
        suggestions: Optional[List[str]] = None,
        error: str = "it failed",
    ) -> Any:
        """Create a run for ``source_id`` and take it to ``outcome`` (``None``: leave it
        ``queued``; ``running``: mark it running)."""
        record = runs.create(None, (), marker, source_id)
        if outcome in ("running", "succeeded", "failed"):
            runs.mark_running(record.identifier)
        if outcome == "succeeded":
            runs.mark_succeeded(
                record.identifier,
                {
                    "suggestions": [{"identifier": i} for i in (suggestions or [])],
                    "notes": "",
                    "semantic_snapshot": "snap",
                },
            )
        elif outcome == "failed":
            runs.mark_failed(record.identifier, error)
        time.sleep(0.01)  # distinct now() values, so "newest" is unambiguous
        return record

    def mine(records: Any) -> list:
        return [
            r
            for r in records
            if (r.source_id or "").startswith(marker) or r.ask == marker
        ]

    yield SimpleNamespace(
        connection=connection,
        runs=runs,
        dismissals=dismissals,
        marker=marker,
        source=source,
        run=run,
        mine=mine,
    )

    connection.rollback()
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM semantic.kpi_suggestion_run WHERE ask = %s", (marker,)
        )
        cursor.execute(
            "DELETE FROM semantic.kpi_suggestion_dismissal WHERE identifier LIKE %s",
            (f"{marker}%",),
        )
    connection.commit()
    connection.close()


def _idents(record: Any) -> list:
    return [s["identifier"] for s in record.result["suggestions"]]


class TestRunsRememberTheirSource:
    def test_a_run_created_for_a_source_reads_back_with_it(self, env):
        record = env.run(env.source("a"), outcome=None)
        assert record.source_id == env.source("a")
        assert env.runs.get(record.identifier).source_id == env.source("a")

    def test_a_run_created_without_one_is_the_deployments_own(self, env):
        record = env.runs.create(None, (), env.marker)
        assert record.source_id is None
        assert env.runs.get(record.identifier).source_id is None

    def test_ensure_schema_adds_the_column_to_a_table_that_lacks_it(self, env):
        """A table provisioned before contract 1.2.9 gains ``source_id`` in place, and every
        older row then reads as the deployment's own warehouse."""
        try:
            with env.connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE semantic.kpi_suggestion_run DROP COLUMN source_id"
                )
            env.connection.commit()

            env.runs.ensure_schema()

            record = env.runs.create(None, (), env.marker, env.source("a"))
            assert record.source_id == env.source("a")
        finally:
            env.connection.rollback()
            env.runs.ensure_schema()

    def test_ensure_schema_is_idempotent(self, env):
        env.runs.ensure_schema()
        env.runs.ensure_schema()
        env.dismissals.ensure_schema()
        env.dismissals.ensure_schema()


class TestCurrentKeepsTheNewestSuccessOfEachSource:
    def test_the_newest_success_of_a_source_supersedes_its_older_one(self, env):
        source = env.source("a")
        old = env.run(source, suggestions=["old__one"])
        new = env.run(source, suggestions=["new__one", "new__two"])

        current = env.mine(env.runs.current().succeeded)

        assert [r.identifier for r in current] == [new.identifier]
        assert _idents(current[0]) == ["new__one", "new__two"]
        assert old.identifier not in [r.identifier for r in current]

    def test_two_sources_each_keep_their_own_run(self, env):
        a = env.run(env.source("a"), suggestions=["a__one"])
        b = env.run(env.source("b"), suggestions=["b__one"])

        current = env.mine(env.runs.current().succeeded)

        assert {r.identifier for r in current} == {a.identifier, b.identifier}
        assert {r.source_id for r in current} == {env.source("a"), env.source("b")}

    def test_they_come_back_newest_first(self, env):
        a = env.run(env.source("a"), suggestions=["a__one"])
        b = env.run(env.source("b"), suggestions=["b__one"])
        c = env.run(env.source("c"), suggestions=["c__one"])

        current = env.mine(env.runs.current().succeeded)

        assert [r.identifier for r in current] == [
            c.identifier,
            b.identifier,
            a.identifier,
        ]

    def test_a_later_failure_does_not_take_a_sources_suggestions_away(self, env):
        source = env.source("a")
        good = env.run(source, suggestions=["a__one"])
        env.run(source, outcome="failed")

        current = env.runs.current()

        assert [r.identifier for r in env.mine(current.succeeded)] == [good.identifier]

    def test_the_deployments_own_warehouse_is_a_scope_of_its_own(self, env):
        """A run started by ``POST /kpis/suggestions`` has no source; it does not supersede,
        and is not superseded by, any source's run."""
        own = env.run(None, suggestions=["own__one"])
        other = env.run(env.source("a"), suggestions=["a__one"])

        current = env.runs.current().succeeded

        ids = [r.identifier for r in current]
        assert own.identifier in ids
        assert other.identifier in ids


class TestCurrentReportsWhatIsRunningAndWhatFailed:
    def test_a_run_in_flight_is_generating(self, env):
        run = env.run(env.source("a"), outcome="running")
        current = env.runs.current()
        assert current.generating is not None
        assert current.generating.identifier == run.identifier
        assert current.generating.status is SuggestionRunStatus.RUNNING
        assert current.generating.result is None

    def test_a_queued_run_is_generating_too(self, env):
        run = env.run(env.source("a"), outcome=None)
        assert env.runs.current().generating.identifier == run.identifier

    def test_the_newest_open_run_is_the_one_reported(self, env):
        env.run(env.source("a"), outcome="running")
        newest = env.run(env.source("b"), outcome=None)
        assert env.runs.current().generating.identifier == newest.identifier

    def test_nothing_is_generating_once_every_run_has_finished(self, env):
        env.run(env.source("a"), suggestions=["a__one"])
        generating = env.runs.current().generating
        # (the database may hold someone else's open run; ours is not it)
        assert generating is None or not (generating.source_id or "").startswith(
            env.marker
        )

    def test_a_failure_with_no_later_success_is_reported(self, env):
        source = env.source("a")
        bad = env.run(source, outcome="failed", error="no model configured")

        failed = env.runs.current().failed

        assert failed is not None
        assert failed.identifier == bad.identifier
        assert failed.error == "no model configured"

    def test_a_failure_a_later_success_got_past_is_history_not_reported(self, env):
        source = env.source("a")
        env.run(source, outcome="failed")
        env.run(source, suggestions=["a__one"])

        failed = env.runs.current().failed

        assert failed is None or failed.source_id != source

    def test_a_failure_older_than_the_success_of_another_source_still_stands(self, env):
        """Success supersedes failure *within a scope*: b's success does not explain away a's
        failure -- a is still missing its suggestions."""
        bad = env.run(env.source("a"), outcome="failed", error="a failed")
        env.run(env.source("b"), suggestions=["b__one"])

        failed = env.runs.current().failed

        assert failed is not None and failed.identifier == bad.identifier

    def test_a_run_whose_executor_is_gone_is_closed_as_failed_not_left_generating(
        self, env
    ):
        run = env.run(env.source("a"), outcome="running")
        with env.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE semantic.kpi_suggestion_run SET created_at = now() - interval '2 hours' "
                "WHERE id = %s",
                (run.identifier,),
            )
        env.connection.commit()

        current = env.runs.current(stale_after=timedelta(minutes=10))

        assert env.runs.get(run.identifier).status is SuggestionRunStatus.FAILED
        assert env.runs.get(run.identifier).error == LOST_RUN_ERROR
        assert (
            current.generating is None
            or current.generating.identifier != run.identifier
        )
        assert current.failed is not None
        assert current.failed.identifier == run.identifier

    def test_a_run_inside_its_window_is_left_alone(self, env):
        run = env.run(env.source("a"), outcome="running")
        env.runs.current(stale_after=timedelta(minutes=10))
        assert env.runs.get(run.identifier).status is SuggestionRunStatus.RUNNING

    def test_async_twin(self, env):
        run = env.run(env.source("a"), suggestions=["a__one"])
        current = asyncio.run(env.runs.acurrent())
        assert run.identifier in [r.identifier for r in current.succeeded]


class TestDismissals:
    def test_a_dismissed_identifier_is_remembered(self, env):
        key = f"{env.marker}__orders"
        dismissal = env.dismissals.dismiss(key, "alice")

        assert dismissal.identifier == key
        assert dismissal.dismissed_by == "alice"
        assert key in env.dismissals.identifiers()

    def test_an_identifier_that_was_never_dismissed_is_not_in_the_set(self, env):
        assert f"{env.marker}__never" not in env.dismissals.identifiers()

    def test_case_and_surrounding_space_do_not_matter(self, env):
        key = f"{env.marker}__mixed"
        env.dismissals.dismiss(f"  {key.upper()}  ")
        assert key in env.dismissals.identifiers()

    def test_dismissing_again_returns_the_original_not_a_new_record(self, env):
        key = f"{env.marker}__twice"
        first = env.dismissals.dismiss(key, "alice")
        time.sleep(0.02)
        second = env.dismissals.dismiss(key.upper(), "bob")

        assert second == first  # same time, same person: history is not rewritten
        with env.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM semantic.kpi_suggestion_dismissal WHERE identifier = %s",
                (key,),
            )
            assert cursor.fetchone()[0] == 1
        env.connection.rollback()

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_a_blank_identifier_is_refused_and_stores_nothing(self, env, blank):
        with pytest.raises(ValueError):
            env.dismissals.dismiss(blank)
        assert "" not in env.dismissals.identifiers()

    def test_the_async_twins(self, env):
        key = f"{env.marker}__async"

        async def scenario():
            dismissal = await env.dismissals.adismiss(key, "carol")
            assert dismissal.identifier == key
            assert key in await env.dismissals.aidentifiers()
            await env.dismissals.aensure_schema()

        asyncio.run(scenario())


def test_normalise_identifier_matches_how_the_registry_compares():
    assert normalise_identifier("  Orders__AOV ") == "orders__aov"
