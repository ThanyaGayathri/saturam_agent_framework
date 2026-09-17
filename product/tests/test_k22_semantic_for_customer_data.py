"""K22: the semantic layer for a customer's own data, end to end.

What was missing between "harvest" and "suggest": the K8 proposers had no runner, an
accepted mapping never reached the graph over HTTP, the registry ignored the overlay's
vocabulary, and a dashboard could not show a KPI's breakdown across its grain. Offline
tests for the deterministic halves; the agents themselves run in the PA04/PA05 cookbooks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from egpt_retail.contracts.dashboards import ChartKind, Tile, TilePosition
from egpt_retail.contracts.vocabulary import load_vocabulary_set
from egpt_retail.dashboards.data import _tile_data
from egpt_retail.semantic.proposals import (
    _APPLY_ORDER,
    GRAPH_KINDS,
    Proposal,
    ProposalKind,
    ReviewState,
)
from tests.test_semantic_router import (
    _FakeKpiRegistryStore,
    _FakeProposalStore,
    _FakeRequest,
    _kpi_proposal,
)

OVERLAY = Path("product/egpt_retail/ontology/overlays/middleby.json")


def _mapping_proposal(identifier: str = "prop_map_1", **overrides: Any) -> Proposal:
    defaults: Dict[str, Any] = dict(
        kind=ProposalKind.MAPPING,
        target="silver.dc_rfq_quotes",
        payload={
            "table": "silver.dc_rfq_quotes",
            "concept": "Quote",
            "confidence": "high",
        },
        proposer="mapping_proposer",
        identifier=identifier,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Proposal(**defaults)


class TestAcceptAppliesToTheGraph:
    """An accepted mapping is written to the semantic graph in the same request."""

    def test_accept_route_applies_graph_bound_kinds(self, monkeypatch):
        from egpt_retail.api.routers import semantic as semantic_module

        store = _FakeProposalStore([_mapping_proposal(), _kpi_proposal()])
        applied: List[Sequence[Proposal]] = []

        class _Graph:
            pass

        def fake_apply_accepted(graph_store):
            accepted = [
                p
                for p in store.list(state=ReviewState.ACCEPTED)
                if p.kind in GRAPH_KINDS
            ]
            applied.append(accepted)
            return None

        store.apply_accepted = fake_apply_accepted  # type: ignore[attr-defined]
        monkeypatch.setattr(
            semantic_module,
            "open_semantic_reader",
            lambda settings: _context(_Graph()),
        )
        body = semantic_module.wire.BulkProposalDecisionRequest(
            ids=["prop_map_1", "prop_kpi_1"]
        )
        semantic_module.accept_semantic_proposals_bulk(
            body,
            _FakeRequest(authorization_enabled=False),
            store=store,
            kpi_store=_FakeKpiRegistryStore(),
        )
        assert len(applied) == 1 and [p.identifier for p in applied[0]] == [
            "prop_map_1"
        ]

    def test_a_graph_that_cannot_open_is_recorded_not_a_500(self, monkeypatch):
        from egpt_retail.api.routers import semantic as semantic_module

        store = _FakeProposalStore([_mapping_proposal()])

        def refuse(settings):
            raise RuntimeError("EGPT_RETAIL_NEO4J_URI is not set")

        monkeypatch.setattr(semantic_module, "open_semantic_reader", refuse)
        out = semantic_module.accept_semantic_proposal(
            "prop_map_1",
            _FakeRequest(authorization_enabled=False),
            None,
            store=store,
            kpi_store=_FakeKpiRegistryStore(),
        )
        assert out.state.value == "accepted"
        assert "NEO4J" in store.apply_errors["prop_map_1"]

    def test_enrichment_applies_before_what_points_at_its_concept(self):
        order = sorted(GRAPH_KINDS, key=lambda k: _APPLY_ORDER.get(k, 9))
        assert order[0] is ProposalKind.ENRICHMENT
        assert order.index(ProposalKind.MAPPING) < order.index(
            ProposalKind.COLUMN_MEANING
        )


class TestOverlayVocabulary:
    def test_the_customer_overlay_adds_grains_without_redefining_core_ones(self):
        core = load_vocabulary_set()
        merged = load_vocabulary_set(overlay_path=OVERLAY)
        core_grains = {t.value for t in core.vocabularies["grain"].terms}
        merged_grains = {t.value for t in merged.vocabularies["grain"].terms}
        assert core_grains < merged_grains
        assert {"lost_reason", "pipeline_stage", "sales_territory"} <= merged_grains
        merged.validate("grain", "lost_reason")

    def test_the_registry_reads_the_deployment_overlay(self, monkeypatch):
        from egpt_retail.kpi import store as kpi_store_module

        monkeypatch.setenv("EGPT_RETAIL_ONTOLOGY_OVERLAY", str(OVERLAY))
        vocab = kpi_store_module._deployment_vocabularies()
        vocab.validate("grain", "sales_territory")


class TestBreakdownTiles:
    def _rows(self) -> List[Dict[str, Any]]:
        return [
            {
                "value": "20",
                "brand": "Adidas",
                "as_of": "x",
                "computed_at": "x",
                "run_id": "x",
            },
            {
                "value": "13",
                "brand": "Apple",
                "as_of": "x",
                "computed_at": "x",
                "run_id": "x",
            },
            {
                "value": "31",
                "brand": "Bosch",
                "as_of": "x",
                "computed_at": "x",
                "run_id": "x",
            },
        ]

    def test_a_bar_tile_at_a_grain_with_no_grain_value_is_the_breakdown(self):
        tile = Tile(
            id="t",
            kpi_identifier="rfq__quotes_by_brand",
            chart=ChartKind.BAR,
            position=TilePosition(x=0, y=0, w=6, h=4),
        )
        data = _tile_data(
            tile,
            {
                "as_of": "2026-09-17",
                "computed_at": "2026-09-17T06:38:00Z",
                "run_id": "r",
                "rows": self._rows(),
            },
            "",
        )
        assert data.status.value == "fresh" and data.value is None and data.series == []
        assert [r["brand"] for r in data.rows] == ["Bosch", "Adidas", "Apple"]
        assert data.rows[0] == {"brand": "Bosch", "value": 31.0}

    def test_a_number_tile_at_a_grain_with_no_grain_value_is_still_a_gap(self):
        tile = Tile(
            id="t",
            kpi_identifier="rfq__quotes_by_brand",
            chart=ChartKind.NUMBER,
            position=TilePosition(x=0, y=0, w=3, h=2),
        )
        data = _tile_data(
            tile,
            {
                "as_of": "2026-09-17",
                "computed_at": "2026-09-17T06:38:00Z",
                "run_id": "r",
                "rows": self._rows(),
            },
            "",
        )
        assert data.status.value == "never_computed" and data.rows == []


def _context(value: Any):
    from contextlib import contextmanager

    @contextmanager
    def cm():
        yield value

    return cm()
