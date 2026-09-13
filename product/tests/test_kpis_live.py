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
from typing import Any, Iterator

import pytest

from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.kpi.store import KpiRegistryStore

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
        assert "livetest_kpi__someone_elses_dashboard" in excinfo.value.detail
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
