"""Live regression tests for #229/B1's `kpis.py` router, against real Postgres and Neo4j --
the same split `test_semantic_live.py`/`test_r15_rehearsal_live.py` document: DB-free logic
(the mappers, the filter, the scope check) lives in `test_kpis_router.py`; anything that needs
a real `KpiRegistryStore.feasibility()` call against a real semantic graph lives here.

Not yet wired into `.github/workflows/integration.yml` -- a one-line follow-up once this is
reviewed (see the PR for #229).

Locally, against the compose stack (`deploy/compose.yaml`)'s Postgres and Neo4j:

    EGPT_INTEGRATION_POSTGRES=1 EGPT_INTEGRATION_NEO4J=1 \\
      EGPT_RETAIL_NEO4J_URI=neo4j://localhost:7687 EGPT_RETAIL_NEO4J_PASSWORD=password \\
      pytest product/tests/test_kpis_live.py

Everything written here is prefixed ``livetest_kpi__`` and removed at the end of the module.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Iterator

import pytest

from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.kpi.store import KpiRegistryStore
from egpt_retail.suggestions.runs import (
    LOST_RUN_ERROR,
    SuggestionRunPhase,
    SuggestionRunStatus,
    SuggestionRunStore,
)

_LIVE = os.environ.get("EGPT_INTEGRATION_POSTGRES") and os.environ.get(
    "EGPT_INTEGRATION_NEO4J"
)
pytestmark = pytest.mark.skipif(
    not _LIVE,
    reason="needs EGPT_INTEGRATION_POSTGRES=1 and EGPT_INTEGRATION_NEO4J=1 with live stores",
)

CONTROL_PLANE = os.environ.get(
    "EGPT_RETAIL_CP_DSN", "postgresql://ai:ai@localhost:5532/ai"
)
IDENT = "livetest_kpi__contacts"


@pytest.fixture(scope="module")
def control_plane() -> Iterator[Any]:
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(CONTROL_PLANE)
    yield conn
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM semantic.kpi_definition WHERE identifier LIKE 'livetest_kpi__%'"
        )
    conn.commit()
    conn.close()


@pytest.fixture
def store(control_plane: Any) -> Iterator[KpiRegistryStore]:
    registry = KpiRegistryStore(control_plane)
    registry.ensure_schema()
    with control_plane.cursor() as cur:
        cur.execute(
            "DELETE FROM semantic.kpi_definition WHERE identifier LIKE 'livetest_kpi__%'"
        )
    control_plane.commit()
    yield registry
    with control_plane.cursor() as cur:
        cur.execute(
            "DELETE FROM semantic.kpi_definition WHERE identifier LIKE 'livetest_kpi__%'"
        )
    control_plane.commit()


def _definition(**overrides: Any) -> KpiDefinition:
    fields: dict = {
        "identifier": IDENT,
        "name": "Livetest contacts",
        "description": "Contacts on file.",
        "sql": "SELECT count(*) FROM crm_contact",
        "grain": "overall",
        "business_area": "customer",
        "source_tables": frozenset({"crm_contact"}),
    }
    fields.update(overrides)
    return KpiDefinition(**fields)


class _FakeState:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeRequest:
    def __init__(self, **state_kwargs):
        self.state = _FakeState(**state_kwargs)


def test_get_kpi_returns_a_definition_saved_to_the_real_registry(
    store: KpiRegistryStore,
) -> None:
    from egpt_retail.api.routers.kpis import get_kpi

    store.save(_definition())
    wire = get_kpi(IDENT, _FakeRequest(authorization_enabled=False), store=store)
    assert wire.identifier == IDENT
    assert wire.sql == "SELECT count(*) FROM crm_contact"
    assert wire.feasibility.verdict.value in ("ready", "partial", "blocked")


def test_get_kpi_404s_on_a_missing_identifier(store: KpiRegistryStore) -> None:
    from fastapi import HTTPException

    from egpt_retail.api.routers.kpis import get_kpi

    with pytest.raises(HTTPException) as excinfo:
        get_kpi(
            "livetest_kpi__does_not_exist",
            _FakeRequest(authorization_enabled=False),
            store=store,
        )
    assert excinfo.value.status_code == 404


def test_list_kpis_includes_the_saved_definition_with_business_area_filter(
    store: KpiRegistryStore,
) -> None:
    from egpt_retail.api.routers.kpis import list_kpis

    store.save(_definition())
    results = list_kpis(
        _FakeRequest(authorization_enabled=False), business_area="customer", store=store
    )
    assert any(k.identifier == IDENT for k in results)

    other_area = list_kpis(
        _FakeRequest(authorization_enabled=False), business_area="sales", store=store
    )
    assert all(k.identifier != IDENT for k in other_area)


def test_get_kpi_feasibility_reports_blocked_for_a_table_the_graph_has_never_seen(
    store: KpiRegistryStore,
) -> None:
    from egpt_retail.api.routers.kpis import get_kpi_feasibility

    store.save(
        _definition(
            sql="SELECT 1 FROM livetest_kpi_nonexistent_table",
            source_tables=frozenset({"livetest_kpi_nonexistent_table"}),
        )
    )
    wire = get_kpi_feasibility(IDENT, store=store)
    assert wire.verdict.value == "blocked"
    assert "livetest_kpi_nonexistent_table" in wire.missing_tables
    assert wire.semantic_snapshot


def test_feasibility_map_reads_a_real_snapshot_version(store: KpiRegistryStore) -> None:
    settings = RetailSettings()
    with open_semantic_reader(settings) as reader:
        version = reader.snapshot().version
    assert isinstance(version, str)
    assert version


# ---------------------------------------------------------------------------
# #231/B2 -- create/update/delete against the real registry, and the new columns
# ---------------------------------------------------------------------------


def _write(**overrides: Any):
    from egpt_retail.api import models as wire

    defaults: dict = {
        "identifier": IDENT,
        "name": "Livetest contacts",
        "description": "Contacts on file.",
        "business_area": "customer",
        "grain": "overall",
        "sql": "SELECT count(*) FROM crm_contact",
        "source_tables": ["crm_contact"],
    }
    defaults.update(overrides)
    return wire.KpiWrite(**defaults)


def test_create_kpi_then_get_round_trips_the_new_columns(
    store: KpiRegistryStore,
) -> None:
    from egpt_retail.api.routers.kpis import create_kpi, get_kpi

    body = _write(subject_area="Customer Ops", unit="count", active=True)
    created = create_kpi(body, _FakeRequest(authorization_enabled=False), store=store)
    assert created.identifier == IDENT
    assert created.subject_area == "Customer Ops"
    assert created.unit == "count"
    assert created.active is True
    assert created.source.value == "customer"
    assert created.validated_against_snapshot is None

    fetched = get_kpi(IDENT, _FakeRequest(authorization_enabled=False), store=store)
    assert fetched.subject_area == "Customer Ops"
    assert fetched.source.value == "customer"


def test_create_kpi_refuses_a_duplicate_identifier_against_the_real_registry(
    store: KpiRegistryStore,
) -> None:
    from fastapi import HTTPException

    from egpt_retail.api.routers.kpis import create_kpi

    store.save(_definition())
    with pytest.raises(HTTPException) as excinfo:
        create_kpi(_write(), _FakeRequest(authorization_enabled=False), store=store)
    assert excinfo.value.status_code == 409


def test_update_kpi_metadata_only_preserves_validated_against_snapshot(
    store: KpiRegistryStore,
) -> None:
    from egpt_retail.api.routers.kpis import update_kpi

    store.save(_definition(validated_against_snapshot="snap.pin"))
    updated = update_kpi(
        IDENT,
        _write(active=False),
        _FakeRequest(authorization_enabled=False),
        store=store,
    )
    assert updated.active is False
    assert updated.validated_against_snapshot == "snap.pin"


def test_update_kpi_formula_edit_resets_validated_against_snapshot(
    store: KpiRegistryStore,
) -> None:
    from egpt_retail.api.routers.kpis import update_kpi

    store.save(_definition(validated_against_snapshot="snap.pin"))
    body = _write(sql="SELECT count(*) FROM crm_contact WHERE 1 = 1")
    updated = update_kpi(
        IDENT, body, _FakeRequest(authorization_enabled=False), store=store
    )
    assert updated.validated_against_snapshot is None


def test_update_kpi_preserves_a_shipped_source_that_write_cannot_set(
    store: KpiRegistryStore,
) -> None:
    from egpt_retail.api.routers.kpis import update_kpi
    from egpt_retail.contracts.kpi import KpiOrigin

    store.save(_definition(source=KpiOrigin.SHIPPED))
    updated = update_kpi(
        IDENT,
        _write(active=False),
        _FakeRequest(authorization_enabled=False),
        store=store,
    )
    assert updated.source.value == "shipped"


def test_delete_kpi_removes_the_row(store: KpiRegistryStore) -> None:
    from fastapi import HTTPException

    from egpt_retail.api.routers.kpis import delete_kpi, get_kpi

    store.save(_definition())
    delete_kpi(IDENT, _FakeRequest(authorization_enabled=False), store=store)
    with pytest.raises(HTTPException) as excinfo:
        get_kpi(IDENT, _FakeRequest(authorization_enabled=False), store=store)
    assert excinfo.value.status_code == 404


def test_delete_kpi_refuses_while_a_dashboard_tile_references_it(
    store: KpiRegistryStore, control_plane: Any
) -> None:
    from fastapi import HTTPException

    from egpt_retail.api.routers.kpis import delete_kpi
    from egpt_retail.contracts.dashboards import ChartKind, Tile, TilePosition
    from egpt_retail.dashboards.store import DashboardStore

    store.save(_definition())
    dashboard_store = DashboardStore(control_plane)
    dashboard_store.ensure_schema()
    dashboard = dashboard_store.create(
        name="livetest_kpi__dashboard",
        owner="local",
        tiles=[
            Tile(
                id="tl_livetest_kpi",
                kpi_identifier=IDENT,
                chart=ChartKind.NUMBER,
                position=TilePosition(x=0, y=0, w=1, h=1),
                title="x",
            )
        ],
    )
    try:
        with pytest.raises(HTTPException) as excinfo:
            delete_kpi(IDENT, _FakeRequest(authorization_enabled=False), store=store)
        assert excinfo.value.status_code == 409
        assert "livetest_kpi__dashboard" in excinfo.value.detail
    finally:
        dashboard_store.delete(dashboard.id, "local")


# ---------------------------------------------------------------------------
# #234/F1 -- GET /kpis/suggestions, against a real registry and semantic reader
#
# The K12/K13 agents are stood in for (`FakeAgent`, exactly `test_k12_k13_suggestion_pass.py`'s
# own), since this environment carries no model provider credentials and a real call is not
# what these tests are for -- they exist to prove the real-registry/real-reader plumbing
# (`run_suggestions`, `assemble`'s feasibility stamping, the business-area filter) works end to
# end, not to grade a live model's suggestions.
# ---------------------------------------------------------------------------


class _FakeSuggestionAgent:
    def __init__(self, content: Any) -> None:
        self.content = content

    def run(self, message: str) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(content=self.content)


def _fake_agents(kpi_drafts: Any) -> Any:
    from egpt_retail.skills import use_case_suggestion as k13

    return (
        _FakeSuggestionAgent(kpi_drafts),
        _FakeSuggestionAgent(k13.UseCaseSuggestionDraftSet()),
    )


def test_list_kpi_suggestions_reads_a_real_snapshot_and_stamps_real_feasibility(
    store: KpiRegistryStore,
) -> None:
    from egpt_retail.api.routers.kpis import _list_kpi_suggestions
    from egpt_retail.skills import kpi_suggestion as k12

    kpi_drafts = k12.KpiSuggestionDraftSet(
        suggestions=[
            k12.KpiSuggestionDraft(
                identifier="livetest_kpi_suggest__contacts",
                name="Livetest suggested contacts",
                description="Contacts on file.",
                business_area="customer",
                grain="overall",
                concept=None,
                result_shape="scalar",
                confidence="high",
                rationale="crm_contact has contact_id.",
                business_value="Shows how many customers can actually be reached.",
                derived_from_tables=["crm_contact"],
                proposed_sql="SELECT count(*) FROM crm_contact",
                catalogue_source=None,
            ),
        ],
        notes="",
    )
    result = _list_kpi_suggestions(
        RetailSettings(),
        definitions=tuple(store.list()),
        vocabularies=store.vocabularies,
        business_area=None,
        include_sql=True,
        agents=_fake_agents(kpi_drafts),
    )
    assert result.semantic_snapshot
    matches = [
        s
        for s in result.suggestions
        if s.identifier == "livetest_kpi_suggest__contacts"
    ]
    assert len(matches) == 1
    assert matches[0].feasibility is not None
    assert matches[0].feasibility.verdict.value in ("ready", "partial", "blocked")


def test_list_kpi_suggestions_filters_out_a_non_matching_business_area(
    store: KpiRegistryStore,
) -> None:
    from egpt_retail.api.routers.kpis import _list_kpi_suggestions
    from egpt_retail.skills import kpi_suggestion as k12

    kpi_drafts = k12.KpiSuggestionDraftSet(
        suggestions=[
            k12.KpiSuggestionDraft(
                identifier="livetest_kpi_suggest__contacts",
                name="Livetest suggested contacts",
                description="Contacts on file.",
                business_area="customer",
                grain="overall",
                concept=None,
                result_shape="scalar",
                confidence="high",
                rationale="crm_contact has contact_id.",
                business_value="Shows how many customers can actually be reached.",
                derived_from_tables=["crm_contact"],
                proposed_sql="SELECT count(*) FROM crm_contact",
                catalogue_source=None,
            ),
        ],
        notes="",
    )
    result = _list_kpi_suggestions(
        RetailSettings(),
        definitions=tuple(store.list()),
        vocabularies=store.vocabularies,
        business_area="sales",
        include_sql=True,
        agents=_fake_agents(kpi_drafts),
    )
    assert all(
        s.identifier != "livetest_kpi_suggest__contacts" for s in result.suggestions
    )


# ---------------------------------------------------------------------------
# #240 review, finding #4(a) -- the visibility gap. A KPI referenced only by a dashboard owned
# by (and private to) a *different* user must still block delete: the deleting caller cannot
# see that dashboard through `DashboardStore.list_visible_to`, but
# `KpiRegistryStore.delete_if_unreferenced` must not rely on that caller-scoped listing.
# ---------------------------------------------------------------------------


def test_delete_kpi_refuses_while_another_owners_private_dashboard_references_it(
    store: KpiRegistryStore, control_plane: Any
) -> None:
    from fastapi import HTTPException

    from egpt_retail.api.routers.kpis import delete_kpi
    from egpt_retail.contracts.dashboards import (
        ChartKind,
        Tile,
        TilePosition,
        Visibility,
    )
    from egpt_retail.dashboards.store import DashboardStore

    store.save(_definition())
    dashboard_store = DashboardStore(control_plane)
    dashboard_store.ensure_schema()
    dashboard = dashboard_store.create(
        name="livetest_kpi__someone_elses_dashboard",
        owner="someone_else",
        visibility=Visibility.PRIVATE,
        tiles=[
            Tile(
                id="tl_livetest_kpi_cross_owner",
                kpi_identifier=IDENT,
                chart=ChartKind.NUMBER,
                position=TilePosition(x=0, y=0, w=1, h=1),
                title="x",
            )
        ],
    )
    try:
        # Sanity check the premise: "local" (the deleting caller in every other test here)
        # genuinely cannot see this dashboard through the caller-scoped listing.
        assert dashboard.id not in {
            d.id for d in dashboard_store.list_visible_to("local")
        }
        with pytest.raises(HTTPException) as excinfo:
            delete_kpi(IDENT, _FakeRequest(authorization_enabled=False), store=store)
        assert excinfo.value.status_code == 409
        # Still blocked -- but the *message*, unlike the block decision, must not name a
        # dashboard "local" (the deleting caller) cannot see: reported only as a count
        # (#240 review, round 2 -- the message-scoping half of finding #4(a)).
        assert "livetest_kpi__someone_elses_dashboard" not in excinfo.value.detail
        assert "1 dashboard you do not have visibility into" in excinfo.value.detail
    finally:
        dashboard_store.delete(dashboard.id, "someone_else")


# ---------------------------------------------------------------------------
# #240 review, finding #7 -- path-vs-body identifier precedence, exercised against the real
# store: PATCHing with a differing `body.identifier` must not create a second row.
# ---------------------------------------------------------------------------


def test_update_kpi_with_a_differing_body_identifier_does_not_create_a_second_row(
    store: KpiRegistryStore,
) -> None:
    from egpt_retail.api.routers.kpis import update_kpi

    store.save(_definition())
    body = _write(identifier="livetest_kpi__a_totally_different_identifier")
    updated = update_kpi(
        IDENT, body, _FakeRequest(authorization_enabled=False), store=store
    )
    assert updated.identifier == IDENT
    assert store.get("livetest_kpi__a_totally_different_identifier") is None
    livetest_rows = [
        d for d in store.list() if d.identifier.startswith("livetest_kpi__")
    ]
    assert [d.identifier for d in livetest_rows] == [IDENT]


# ---------------------------------------------------------------------------
# #240 review, findings #1 and #2 -- real concurrency races against Postgres itself, not a
# fake double. Two independent connections/stores stand in for two concurrent requests.
# ---------------------------------------------------------------------------


def test_save_if_unchanged_returns_409_when_another_writer_moved_the_row(
    store: KpiRegistryStore, control_plane: Any
) -> None:
    """The lost-update race (finding #1), reproduced with two real connections: both "read"
    the same row, one writes and commits, and the other's conditional update -- still holding
    the now-stale ``updated_at`` from its own read -- must be refused rather than silently
    overwrite the first writer's change.
    """
    import psycopg

    from egpt_retail.kpi.store import KpiConcurrentUpdateError

    store.save(_definition())
    read_by_both = store.get(IDENT)
    assert read_by_both is not None

    other_connection = psycopg.connect(CONTROL_PLANE)
    try:
        other_store = KpiRegistryStore(other_connection)
        # Writer A commits first, using its own read of the row.
        winner = other_store.save_if_unchanged(
            _definition(description="Writer A won."), read_by_both.updated_at
        )
        assert winner.description == "Writer A won."

        # Writer B still holds the pre-A `updated_at` -- its conditional update must lose.
        with pytest.raises(KpiConcurrentUpdateError):
            store.save_if_unchanged(
                _definition(description="Writer B must not win."),
                read_by_both.updated_at,
            )

        # The row reflects Writer A's write, not a silent overwrite by Writer B.
        final = store.get(IDENT)
        assert final is not None
        assert final.description == "Writer A won."
    finally:
        other_connection.close()


def test_create_returns_none_for_the_loser_of_a_real_concurrent_create_race(
    store: KpiRegistryStore,
) -> None:
    """The check-then-upsert race on POST (finding #2), reproduced with real threads and real
    connections racing the same ``INSERT ... ON CONFLICT (identifier) DO NOTHING`` against
    Postgres: exactly one of two concurrent creates for the same identifier must succeed.
    """
    import threading

    import psycopg

    results: list = [None, None]
    barrier = threading.Barrier(2)

    def _attempt(index: int) -> None:
        connection = psycopg.connect(CONTROL_PLANE)
        try:
            racer_store = KpiRegistryStore(connection)
            barrier.wait()  # line both threads up so the INSERTs actually overlap
            results[index] = racer_store.create(_definition())
        finally:
            connection.close()

    threads = [threading.Thread(target=_attempt, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    succeeded = [r for r in results if r is not None]
    lost = [r for r in results if r is None]
    assert len(succeeded) == 1, (
        "exactly one concurrent create should win the identifier; a plain check-then-upsert "
        f"would let both through. results={results!r}"
    )
    assert len(lost) == 1
    # The row itself is exactly what the winner wrote -- not some interleaving of both.
    assert store.get(IDENT) is not None


# ---------------------------------------------------------------------------
# #240 review, round 2 -- the residual TOCTOU from the *other* direction: a dashboard
# create/update can still land a tile naming a KPI that `_check_tiles_ready` saw as
# registered and ready, but that a concurrent `DELETE /kpis/{id}` removed before the write
# actually landed. `DashboardStore.create`/`update`'s own atomic existence guard (mirroring
# `KpiRegistryStore.delete_if_unreferenced`'s guard in the other direction) closes it. These
# exercise the guard directly against a real Postgres connection rather than staging the
# precise timing of the race: the guard is a single atomic statement that does not care
# whether the KPI vanished a millisecond or an hour before it runs.
# ---------------------------------------------------------------------------


def test_dashboard_create_refuses_a_tile_naming_a_kpi_that_does_not_exist(
    control_plane: Any,
) -> None:
    from egpt_retail.contracts.dashboards import ChartKind, Tile, TilePosition
    from egpt_retail.dashboards.store import DashboardKpiVanishedError, DashboardStore

    dashboard_store = DashboardStore(control_plane)
    dashboard_store.ensure_schema()
    # Deliberately never saved to the registry -- stands in for a KPI that existed when
    # `_check_tiles_ready` ran and was deleted before this write landed.
    vanished_identifier = "livetest_kpi__vanished_before_write"
    with pytest.raises(DashboardKpiVanishedError):
        dashboard_store.create(
            name="livetest_kpi__dashboard_over_a_vanished_kpi",
            owner="local",
            tiles=[
                Tile(
                    id="tl_livetest_kpi_vanished",
                    kpi_identifier=vanished_identifier,
                    chart=ChartKind.NUMBER,
                    position=TilePosition(x=0, y=0, w=1, h=1),
                    title="x",
                )
            ],
        )
    # Nothing was left behind: the guard tripped inside the same statement as the INSERT,
    # not after a partial write.
    assert not any(
        d.name == "livetest_kpi__dashboard_over_a_vanished_kpi"
        for d in dashboard_store.list_visible_to("local")
    )


def test_dashboard_update_refuses_replacing_tiles_with_one_naming_a_vanished_kpi(
    store: KpiRegistryStore, control_plane: Any
) -> None:
    from egpt_retail.contracts.dashboards import ChartKind, Tile, TilePosition
    from egpt_retail.dashboards.store import DashboardKpiVanishedError, DashboardStore

    store.save(_definition())
    dashboard_store = DashboardStore(control_plane)
    dashboard_store.ensure_schema()
    dashboard = dashboard_store.create(
        name="livetest_kpi__dashboard_to_update",
        owner="local",
        tiles=[
            Tile(
                id="tl_livetest_kpi_original",
                kpi_identifier=IDENT,
                chart=ChartKind.NUMBER,
                position=TilePosition(x=0, y=0, w=1, h=1),
                title="x",
            )
        ],
    )
    try:
        with pytest.raises(DashboardKpiVanishedError):
            dashboard_store.update(
                dashboard.id,
                "local",
                tiles=[
                    Tile(
                        id="tl_livetest_kpi_replacement",
                        kpi_identifier="livetest_kpi__vanished_before_write",
                        chart=ChartKind.NUMBER,
                        position=TilePosition(x=0, y=0, w=1, h=1),
                        title="x",
                    )
                ],
            )
        # The original tiles are untouched -- the failed update did not clobber them.
        current = dashboard_store.get(dashboard.id)
        assert current is not None
        assert [t.id for t in current.tiles] == ["tl_livetest_kpi_original"]
    finally:
        dashboard_store.delete(dashboard.id, "local")


# ---------------------------------------------------------------------------
# #254: `SuggestionRunStore` against the real control plane -- the DDL, the guarded
# transitions and the lost-run guard, which the DB-free suite only fakes.
# ---------------------------------------------------------------------------


@pytest.fixture
def run_store(control_plane: Any) -> Iterator[SuggestionRunStore]:
    store = SuggestionRunStore(control_plane)
    store.ensure_schema()
    store.ensure_schema()  # idempotent: the second boot must not fail
    yield store
    # By area, not by age: the lost-run test ages its own row two hours back, which an
    # age-bounded delete would have left behind (it did, once).
    with control_plane.cursor() as cur:
        cur.execute(
            "DELETE FROM semantic.kpi_suggestion_run WHERE business_area = 'livetest'"
        )
    control_plane.commit()


def test_suggestion_run_store_round_trips_a_run_to_succeeded(
    run_store: SuggestionRunStore,
) -> None:
    created = run_store.create("livetest")
    assert created.status is SuggestionRunStatus.QUEUED
    assert created.business_area == "livetest"
    assert created.created_at.tzinfo is not None
    assert created.started_at is None and created.result is None

    run_store.mark_running(created.identifier)
    running = run_store.get(created.identifier)
    assert running is not None
    assert running.status is SuggestionRunStatus.RUNNING
    assert running.started_at is not None

    payload = {"suggestions": [], "notes": "", "semantic_snapshot": "live.1"}
    run_store.mark_succeeded(created.identifier, payload)
    done = run_store.get(created.identifier)
    assert done is not None
    assert done.status is SuggestionRunStatus.SUCCEEDED
    assert done.result == payload
    assert done.finished_at is not None and done.error is None

    # terminal is terminal: a late failure cannot reopen the row
    run_store.mark_failed(created.identifier, "too late")
    still_done = run_store.get(created.identifier)
    assert still_done is not None
    assert still_done.status is SuggestionRunStatus.SUCCEEDED


@pytest.mark.anyio
async def test_suggestion_run_store_async_variants_agree_with_their_sync_twins(
    run_store: SuggestionRunStore,
) -> None:
    """Both variants exist and do the same thing, per the repository's standing rule --
    the router never calls ``aget``/``acreate``/``amark_*``/``aensure_schema`` (it threads the
    sync methods itself via ``_with_run_store``), so nothing else exercised them (#256 review).
    """
    await run_store.aensure_schema()  # idempotent, same as the sync call in the fixture

    created = await run_store.acreate("livetest")
    assert created.status is SuggestionRunStatus.QUEUED
    assert created.business_area == "livetest"

    await run_store.amark_running(created.identifier)
    running = await run_store.aget(created.identifier)
    assert running is not None
    assert running.status is SuggestionRunStatus.RUNNING
    assert running.started_at is not None

    payload = {"suggestions": [], "notes": "", "semantic_snapshot": "live.1"}
    await run_store.amark_succeeded(created.identifier, payload)
    done = await run_store.aget(created.identifier)
    assert done is not None
    assert done.status is SuggestionRunStatus.SUCCEEDED
    assert done.result == payload

    # terminal is terminal here too: a late failure cannot reopen the row
    await run_store.amark_failed(created.identifier, "too late")
    still_done = await run_store.aget(created.identifier)
    assert still_done is not None
    assert still_done.status is SuggestionRunStatus.SUCCEEDED


def test_suggestion_run_store_get_closes_a_lost_run_only_past_the_window(
    run_store: SuggestionRunStore, control_plane: Any
) -> None:
    created = run_store.create("livetest")
    run_store.mark_running(created.identifier)

    # inside the window: untouched
    fresh = run_store.get(created.identifier, stale_after=timedelta(hours=1))
    assert fresh is not None and fresh.status is SuggestionRunStatus.RUNNING

    # age the row past any window, then read with one
    with control_plane.cursor() as cur:
        cur.execute(
            "UPDATE semantic.kpi_suggestion_run SET created_at = now() - interval '2 hours'"
            " WHERE id = %s",
            (created.identifier,),
        )
    control_plane.commit()
    lost = run_store.get(created.identifier, stale_after=timedelta(hours=1))
    assert lost is not None
    assert lost.status is SuggestionRunStatus.FAILED
    assert lost.error == LOST_RUN_ERROR
    assert lost.finished_at is not None


def test_suggestion_run_store_get_returns_none_for_an_unknown_id(
    run_store: SuggestionRunStore,
) -> None:
    assert run_store.get("sr_livetest_missing") is None


def test_suggestion_run_store_keeps_the_focus_a_run_was_started_with(
    run_store: SuggestionRunStore,
) -> None:
    created = run_store.create("livetest", ["sales", "marketing"])
    assert created.business_areas == ("sales", "marketing")
    read = run_store.get(created.identifier)
    assert read is not None and read.business_areas == ("sales", "marketing")

    # no focus: an empty tuple, not None -- and an older row reads the same
    plain = run_store.create("livetest")
    assert plain.business_areas == ()


# ---------------------------------------------------------------------------
# contract 1.1.9 -- POST /kpis/{id}/compute then GET /kpis/{id}/series, against the real
# warehouse: the pass writes gold for exactly this KPI and the series reads it back.
# ---------------------------------------------------------------------------


@pytest.fixture
def warehouse() -> Iterator[Any]:
    """The warehouse connection the gold pass writes to, or a skip when the deployment has
    none; every gold row and view this module writes is removed afterwards."""
    psycopg = pytest.importorskip("psycopg")
    from egpt_retail.app.db import psycopg_dsn

    url = RetailSettings().retail_warehouse_db_url
    if not url:
        pytest.skip(
            "EGPT_RETAIL_WAREHOUSE_DB_URL unset; the gold pass has nowhere to write"
        )
    conn = psycopg.connect(psycopg_dsn(str(url)))
    yield conn
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM gold.kpi_value WHERE kpi_identifier LIKE 'livetest_kpi__%'"
        )
        cur.execute(
            "DELETE FROM gold.kpi_run WHERE kpi_identifier LIKE 'livetest_kpi__%'"
        )
        cur.execute(f"DROP VIEW IF EXISTS gold.kpi_{IDENT}")
    conn.commit()
    conn.close()


def test_compute_kpi_writes_gold_and_the_series_reads_it_back(
    store: KpiRegistryStore, warehouse: Any
) -> None:
    import asyncio

    from fastapi import HTTPException

    from egpt_retail.api.routers.kpis import compute_kpi, get_kpi_series

    store.save(_definition(sql="SELECT count(*) FROM crm_contact"))
    try:
        result = asyncio.run(compute_kpi(IDENT, store=store))
    except HTTPException as exc:
        # 409: the graph this test runs against does not carry `crm_contact`, so the KPI is
        # not `ready` and there is nothing to compute; 503: the last load did not pass or a
        # load is running. Both are the route doing its job, not a failure of this test.
        pytest.skip(f"compute refused ({exc.status_code}): {exc.detail}")
    assert result.kpi_identifier == IDENT
    assert result.run_id.startswith("api_")
    assert result.status.value in ("materialised", "failed"), result
    if result.status.value == "failed":
        pytest.skip(f"the formula failed against this warehouse: {result.error}")
    assert result.rows_written >= 1

    series = asyncio.run(get_kpi_series(IDENT, days=30, dimension=None, store=store))
    assert series.kpi_identifier == IDENT
    assert series.status.value == "fresh"
    assert series.dimension is None and series.dimensions == []
    assert len(series.points) >= 1
    assert series.points[-1].value is not None and series.points[-1].value >= 0
    assert series.value == series.points[-1].value
    assert series.provenance_ref == result.run_id
    assert series.as_of is not None and series.as_of.date() == result.as_of


def test_suggestion_run_store_phase_round_trips_and_only_a_running_row_takes_one(
    run_store: SuggestionRunStore,
) -> None:
    created = run_store.create("livetest")

    # queued: no stage has begun, a report is ignored
    run_store.mark_phase(created.identifier, SuggestionRunPhase.READING)
    queued = run_store.get(created.identifier)
    assert queued is not None and queued.phase is None

    run_store.mark_running(created.identifier)
    run_store.mark_phase(created.identifier, SuggestionRunPhase.PROPOSING)
    running = run_store.get(created.identifier)
    assert running is not None
    assert running.status is SuggestionRunStatus.RUNNING
    assert running.phase is SuggestionRunPhase.PROPOSING

    # terminal keeps the last stage, and a late report cannot relabel it
    run_store.mark_succeeded(
        created.identifier, {"suggestions": [], "notes": "", "semantic_snapshot": "l"}
    )
    run_store.mark_phase(created.identifier, SuggestionRunPhase.CHECKING)
    done = run_store.get(created.identifier)
    assert done is not None
    assert done.status is SuggestionRunStatus.SUCCEEDED
    assert done.phase is SuggestionRunPhase.PROPOSING


@pytest.mark.anyio
async def test_suggestion_run_store_amark_phase_agrees_with_its_sync_twin(
    run_store: SuggestionRunStore,
) -> None:
    created = await run_store.acreate("livetest")
    await run_store.amark_running(created.identifier)
    await run_store.amark_phase(created.identifier, SuggestionRunPhase.CHECKING)
    running = await run_store.aget(created.identifier)
    assert running is not None and running.phase is SuggestionRunPhase.CHECKING


def test_suggestion_run_store_typical_duration_is_a_median_of_the_newest_successes(
    run_store: SuggestionRunStore, control_plane: Any
) -> None:
    """Three successes of 10s, 100s and 30s, made the newest rows in the table (the demo
    stack shares this database, so older successes may exist): the median of the newest
    three is 30, a failed run of any length is not counted, and the newest two alone
    give the midpoint of those two."""
    seconds = {}
    for duration in (10, 100, 30):
        created = run_store.create("livetest")
        run_store.mark_running(created.identifier)
        run_store.mark_succeeded(
            created.identifier,
            {"suggestions": [], "notes": "", "semantic_snapshot": "l"},
        )
        seconds[created.identifier] = duration
    failed = run_store.create("livetest")
    run_store.mark_running(failed.identifier)
    run_store.mark_failed(failed.identifier, "timed out")
    with control_plane.cursor() as cur:
        # newest first in `finished_at` order: 30s (the last created) is the newest
        for offset, (identifier, duration) in enumerate(reversed(seconds.items())):
            cur.execute(
                "UPDATE semantic.kpi_suggestion_run"
                " SET started_at = now() + interval '1 day' - make_interval(secs => %s),"
                "     finished_at = now() + interval '1 day' - make_interval(secs => %s)"
                " WHERE id = %s",
                (duration + offset * 1000, offset * 1000, identifier),
            )
        cur.execute(
            "UPDATE semantic.kpi_suggestion_run"
            " SET started_at = now() + interval '1 day' - interval '5000 seconds',"
            "     finished_at = now() + interval '1 day'"
            " WHERE id = %s",
            (failed.identifier,),
        )
    control_plane.commit()

    assert run_store.typical_duration(sample=3) == 30.0
    assert run_store.typical_duration(sample=2) == 65.0  # newest two: 30s and 100s


@pytest.mark.anyio
async def test_suggestion_run_store_atypical_duration_agrees_with_its_sync_twin(
    run_store: SuggestionRunStore,
) -> None:
    assert await run_store.atypical_duration() == run_store.typical_duration()
