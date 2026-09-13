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
