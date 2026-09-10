"""K9 -- business area detection, without a graph.

The detection is a pure function over the ontology and one query's output, which is what
makes "two runs agree exactly" a property of the code rather than a hope about the
environment. All of that is testable here; the graph query itself is in the live suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pytest
from egpt_retail.semantic.areas import (
    AreaStatus,
    areas_in_ontology,
    detect_areas,
    summary,
    supported_areas,
)


@dataclass(frozen=True)
class _Concept:
    name: str
    business_area: Optional[str] = None


@dataclass(frozen=True)
class _Ontology:
    concepts: Tuple[_Concept, ...]


ONTOLOGY = _Ontology(
    concepts=(
        _Concept("Customer", "customer"),
        _Concept("Contact", "customer"),
        _Concept("StockLevel", "inventory"),
        _Concept("Campaign", "marketing"),
        _Concept("Unplaced", None),
    )
)


def _evidence(**areas: Dict[str, List[str]]) -> Dict[str, Dict[str, List[str]]]:
    return dict(areas)


class TestAreasExistBeforeAnySource:
    def test_they_come_from_the_ontology_with_no_database(self) -> None:
        """The property that lets a customer configure areas up front."""
        assert areas_in_ontology(ONTOLOGY) == ("customer", "inventory", "marketing")

    def test_a_concept_with_no_area_contributes_none(self) -> None:
        assert "None" not in areas_in_ontology(ONTOLOGY)
        assert len(areas_in_ontology(ONTOLOGY)) == 3

    def test_nothing_is_supported_with_no_evidence(self) -> None:
        statuses = detect_areas(ONTOLOGY, {})
        assert len(statuses) == 3
        assert supported_areas(statuses) == ()

    def test_unsupported_areas_are_still_listed(self) -> None:
        """A screen showing only what works cannot tell a customer what connecting one more
        source would buy them."""
        statuses = detect_areas(
            ONTOLOGY, _evidence(customer={"concepts": ["Contact"], "tables": ["s.c"]})
        )
        assert [s.name for s in statuses] == ["customer", "inventory", "marketing"]
        assert [s.supported for s in statuses] == [True, False, False]


class TestSupportedMeansMapped:
    def test_an_area_with_a_mapped_concept_is_supported(self) -> None:
        statuses = detect_areas(
            ONTOLOGY,
            _evidence(
                inventory={"concepts": ["StockLevel"], "tables": ["silver.stock"]}
            ),
        )
        inventory = next(s for s in statuses if s.name == "inventory")
        assert inventory.supported
        assert inventory.tables == ("silver.stock",)

    def test_an_area_whose_evidence_names_no_concept_is_not_supported(self) -> None:
        """An empty concept list is what a query returns for an area nothing mapped. It must
        not read as support, or an area with no data behind it gets a green badge."""
        statuses = detect_areas(
            ONTOLOGY, _evidence(inventory={"concepts": [], "tables": []})
        )
        assert not next(s for s in statuses if s.name == "inventory").supported

    def test_evidence_for_an_area_the_ontology_lacks_is_ignored(self) -> None:
        """A stale area in the graph must not appear on a screen the ontology cannot explain."""
        statuses = detect_areas(
            ONTOLOGY, _evidence(retired={"concepts": ["X"], "tables": ["t"]})
        )
        assert "retired" not in [s.name for s in statuses]

    def test_a_supported_area_always_names_its_evidence(self) -> None:
        statuses = detect_areas(
            ONTOLOGY,
            _evidence(customer={"concepts": ["Contact"], "tables": ["silver.c"]}),
        )
        supported = [s for s in statuses if s.supported]
        assert supported and all(s.concepts and s.tables for s in supported)

    def test_the_reason_is_renderable_either_way(self) -> None:
        statuses = detect_areas(
            ONTOLOGY,
            _evidence(customer={"concepts": ["Contact"], "tables": ["silver.c"]}),
        )
        by_name = {s.name: s for s in statuses}
        assert "Contact" in by_name["customer"].reason
        assert "No table is mapped" in by_name["inventory"].reason


class TestDeterminism:
    def test_two_runs_agree_field_for_field(self) -> None:
        evidence = _evidence(
            customer={"concepts": ["Contact", "Customer"], "tables": ["s.a", "s.b"]},
            marketing={"concepts": ["Campaign"], "tables": ["s.c"]},
        )
        assert detect_areas(ONTOLOGY, evidence) == detect_areas(ONTOLOGY, evidence)

    def test_the_order_is_stable_and_alphabetical(self) -> None:
        """Two runs whose output differs only in order would make a diff between them
        meaningless, which is how a drift check stops being able to see drift."""
        statuses = detect_areas(ONTOLOGY, {})
        assert [s.name for s in statuses] == sorted(s.name for s in statuses)

    def test_no_model_no_io(self) -> None:
        """The guarantee behind the badge: nothing here can be persuaded."""
        import inspect

        from egpt_retail.semantic import areas

        source = inspect.getsource(areas)
        for forbidden in ("Agent(", "neo4j", "psycopg", "openai"):
            assert forbidden not in source, forbidden


class TestNarrowing:
    def test_only_restricts_to_the_named_areas(self) -> None:
        statuses = detect_areas(ONTOLOGY, {}, only=["inventory"])
        assert [s.name for s in statuses] == ["inventory"]

    def test_only_cannot_invent_an_area_the_ontology_lacks(self) -> None:
        assert detect_areas(ONTOLOGY, {}, only=["nonexistent"]) == ()


class TestSummary:
    def test_it_counts_both_halves(self) -> None:
        statuses = detect_areas(
            ONTOLOGY, _evidence(customer={"concepts": ["Contact"], "tables": ["s.c"]})
        )
        assert summary(statuses) == {
            "total": 3,
            "supported": 1,
            "unsupported": 2,
            "supported_areas": ["customer"],
            "unsupported_areas": ["inventory", "marketing"],
        }

    def test_an_empty_deployment_summarises_honestly(self) -> None:
        assert summary(detect_areas(ONTOLOGY, {}))["supported"] == 0


class TestAreaStatus:
    @pytest.mark.parametrize("supported", [True, False])
    def test_str_says_which(self, supported: bool) -> None:
        status = AreaStatus(
            name="customer", supported=supported, concepts=("C",), tables=("t",)
        )
        assert ("supported" in str(status)) and (
            ("not supported" in str(status)) != supported
        )


class TestAreaComesFromTheOntology:
    """The area is shipped knowledge, never what an agent wrote on the node (P3 verification:
    one K8 run put WebSession in `operations`, another in `customer`; the ontology says
    marketing)."""

    def test_mapped_concepts_are_grouped_by_the_ontology_area(self) -> None:
        from egpt_retail.semantic.areas import evidence_from_mappings

        evidence = evidence_from_mappings(
            ONTOLOGY,
            {"Contact": ["silver.crm_contact"], "StockLevel": ["silver.stock"]},
        )
        assert evidence == {
            "customer": {"concepts": ["Contact"], "tables": ["silver.crm_contact"]},
            "inventory": {"concepts": ["StockLevel"], "tables": ["silver.stock"]},
        }

    def test_a_mapped_concept_the_ontology_does_not_place_counts_for_nothing(
        self,
    ) -> None:
        from egpt_retail.semantic.areas import evidence_from_mappings

        assert evidence_from_mappings(ONTOLOGY, {"Unplaced": ["silver.x"]}) == {}

    def test_a_concept_mapped_to_no_table_supports_nothing(self) -> None:
        from egpt_retail.semantic.areas import evidence_from_mappings

        assert evidence_from_mappings(ONTOLOGY, {"Contact": []}) == {}
