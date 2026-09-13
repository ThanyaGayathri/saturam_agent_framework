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
                derived_from_tables=["crm_contact"],
                proposed_sql="SELECT count(*) FROM crm_contact",
                catalogue_source=None,
            ),
        ],
        notes="",
    )
    result = _list_kpi_suggestions(
        RetailSettings(),
        store,
        business_area=None,
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
                derived_from_tables=["crm_contact"],
                proposed_sql="SELECT count(*) FROM crm_contact",
                catalogue_source=None,
            ),
        ],
        notes="",
    )
    result = _list_kpi_suggestions(
        RetailSettings(),
        store,
        business_area="sales",
        agents=_fake_agents(kpi_drafts),
    )
    assert all(
        s.identifier != "livetest_kpi_suggest__contacts" for s in result.suggestions
    )
