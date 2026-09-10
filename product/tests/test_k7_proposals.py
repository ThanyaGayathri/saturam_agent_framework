"""K7 -- the parts of the proposals store that need no database (G5, D10).

The state machine and the graph writes are proved by the cookbook against real Postgres and
Neo4j. What is worth testing here is the logic a review screen depends on and that a wrong
change would break silently: payload validation, and the content-derived identity that stops
a re-run resurrecting a rejected proposal.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from egpt_retail.contracts.semantic import Concept, JoinEdge, ProposalStatus
from egpt_retail.semantic.proposals import (
    _VALIDATORS,
    GRAPH_KINDS,
    PayloadError,
    Proposal,
    ProposalKind,
    Refusal,
    ReviewOutcome,
    ReviewState,
    validate_payload,
)


def _enrichment(**overrides: Any) -> Proposal:
    payload: Dict[str, Any] = {
        "name": "Contact",
        "description": "A person we can reach.",
    }
    payload.update(overrides.pop("payload", {}))
    return Proposal(
        kind=ProposalKind.ENRICHMENT,
        target=str(overrides.pop("target", "Contact")),
        payload=payload,
        proposer=str(overrides.pop("proposer", "enricher")),
    )


class TestIdentity:
    def test_same_content_gives_same_id(self) -> None:
        assert _enrichment().identifier == _enrichment().identifier

    def test_different_payload_gives_different_id(self) -> None:
        a = _enrichment()
        b = _enrichment(payload={"description": "Something else."})
        assert a.identifier != b.identifier

    def test_key_order_does_not_change_the_id(self) -> None:
        """The digest is over canonical JSON, so a proposer that emits keys in a different
        order on the second run must not produce a second review item."""
        first = Proposal(
            kind=ProposalKind.JOIN,
            target="a->b",
            payload={"left_table": "a", "right_table": "b", "on": "a.id = b.id"},
            proposer="mapper",
        )
        second = Proposal(
            kind=ProposalKind.JOIN,
            target="a->b",
            payload={"on": "a.id = b.id", "right_table": "b", "left_table": "a"},
            proposer="mapper",
        )
        assert first.identifier == second.identifier

    def test_proposer_is_not_part_of_the_identity(self) -> None:
        """Two agents proposing the same thing are proposing one thing, and a reviewer should
        see it once."""
        assert _enrichment().identifier == _enrichment(proposer="other").identifier

    def test_an_explicit_id_is_kept(self) -> None:
        """A proposal read back from the table carries its stored id, which must not be
        recomputed -- a payload that round-tripped through jsonb could hash differently."""
        stored = Proposal(
            kind=ProposalKind.ENRICHMENT,
            target="Contact",
            payload={"name": "Contact"},
            proposer="enricher",
            identifier="an_id_from_the_table",
        )
        assert stored.identifier == "an_id_from_the_table"


class TestProvenance:
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_proposer_is_refused(self, blank: str) -> None:
        with pytest.raises(PayloadError, match="proposer"):
            Proposal(
                kind=ProposalKind.ENRICHMENT,
                target="Contact",
                payload={"name": "Contact"},
                proposer=blank,
            )

    def test_blank_target_is_refused(self) -> None:
        with pytest.raises(PayloadError, match="target"):
            Proposal(
                kind=ProposalKind.ENRICHMENT,
                target=" ",
                payload={"name": "Contact"},
                proposer="enricher",
            )


class TestPayloadValidation:
    """Validation happens at propose time so a person never accepts something unappliable."""

    def test_enrichment_builds_a_concept(self) -> None:
        built = validate_payload(
            ProposalKind.ENRICHMENT,
            {"name": "Quote", "synonyms": ["bid"], "sql_filter": "status = 'open'"},
        )
        assert isinstance(built, Concept)
        assert built.synonyms == ("bid",)
        assert built.status is ProposalStatus.ACCEPTED

    def test_join_builds_an_edge(self) -> None:
        built = validate_payload(
            ProposalKind.JOIN,
            {
                "left_table": "crm_deal",
                "right_table": "crm_contact",
                "on": "a.id = b.id",
            },
        )
        assert isinstance(built, JoinEdge)
        assert built.cardinality == "many-to-one"

    @pytest.mark.parametrize(
        "kind,payload",
        [
            (ProposalKind.ENRICHMENT, {}),
            (ProposalKind.JOIN, {"left_table": "a", "right_table": "b"}),
            (ProposalKind.MAPPING, {"table": "silver.crm_deal"}),
            (ProposalKind.AUTHORITATIVE_SOURCE, {"concept": "Deal"}),
            (ProposalKind.KPI, {}),
            (ProposalKind.USE_CASE, {"question": "   "}),
        ],
    )
    def test_missing_required_field_is_refused(
        self, kind: ProposalKind, payload: dict
    ) -> None:
        with pytest.raises(PayloadError):
            validate_payload(kind, payload)

    def test_every_kind_has_a_validator(self) -> None:
        """A kind added without one would fail at apply, in a background job, after a person
        had already approved it.

        Checks the registry rather than calling ``validate_payload`` with an empty payload:
        a missing validator raises ``PayloadError`` too, so that weaker check would pass on
        exactly the bug it is meant to catch.
        """
        assert set(_VALIDATORS) == set(ProposalKind)

    def test_graph_kinds_are_a_subset_of_all_kinds(self) -> None:
        assert GRAPH_KINDS < set(ProposalKind)
        assert ProposalKind.KPI not in GRAPH_KINDS
        assert ProposalKind.USE_CASE not in GRAPH_KINDS


class TestReviewOutcome:
    def test_a_clean_outcome_is_all_applied(self) -> None:
        assert ReviewOutcome(decided=("a", "b")).all_applied

    def test_a_refusal_makes_it_not_all_applied(self) -> None:
        outcome = ReviewOutcome(
            decided=("a",),
            refused=(Refusal("b", "already rejected", ReviewState.REJECTED, "dana"),),
        )
        assert not outcome.all_applied
        assert "dana" in str(outcome.refused[0])


class TestState:
    def test_pending_is_not_decided(self) -> None:
        assert not _enrichment().is_decided

    @pytest.mark.parametrize("state", [ReviewState.ACCEPTED, ReviewState.REJECTED])
    def test_a_decided_proposal_says_so(self, state: ReviewState) -> None:
        proposal = Proposal(
            kind=ProposalKind.ENRICHMENT,
            target="Contact",
            payload={"name": "Contact"},
            proposer="enricher",
            state=state,
            reviewer="kiruthiga",
        )
        assert proposal.is_decided

    def test_review_state_has_a_value_the_graph_enum_does_not(self) -> None:
        """The two enums are deliberately different sizes. If someone collapses them, the
        graph gains a vocabulary for a state it can never hold."""
        assert ReviewState.REJECTED.value not in {s.value for s in ProposalStatus}


class TestKpiProposalsCarryTheWholeDefinition:
    """P3 verification: a KPI proposal that was only an identifier could be accepted and
    never adopted. It is now built into a KpiDefinition at propose time, so it fails there."""

    def test_a_full_definition_validates(self) -> None:
        from egpt_retail.contracts.kpi import KpiDefinition

        built = validate_payload(
            ProposalKind.KPI,
            {
                "identifier": "deal__won",
                "name": "Won deals",
                "sql": "SELECT count(*) FROM crm_deal",
                "grain": "overall",
                "business_area": "sales",
                "source_tables": ["crm_deal"],
            },
        )
        assert isinstance(built, KpiDefinition)

    def test_a_formula_reading_an_undeclared_table_is_refused(self) -> None:
        with pytest.raises(PayloadError, match="crm_contact"):
            validate_payload(
                ProposalKind.KPI,
                {
                    "identifier": "deal__won",
                    "name": "n",
                    "sql": "SELECT count(*) FROM crm_deal JOIN crm_contact ON true",
                    "grain": "overall",
                    "business_area": "sales",
                    "source_tables": ["crm_deal"],
                },
            )

    def test_an_identifier_alone_is_no_longer_enough(self) -> None:
        with pytest.raises(PayloadError):
            validate_payload(ProposalKind.KPI, {"identifier": "deal__won"})


class TestColumnMeaningKind:
    def test_it_is_a_graph_kind_with_a_validator(self) -> None:
        from egpt_retail.semantic.proposals import GRAPH_KINDS

        assert ProposalKind.COLUMN_MEANING in GRAPH_KINDS
        built = validate_payload(
            ProposalKind.COLUMN_MEANING,
            {"table": "silver.a", "column": "id", "concept": "C", "means": "the a id"},
        )
        assert built["joinable"] is False

    def test_a_meaning_without_a_concept_is_refused(self) -> None:
        with pytest.raises(PayloadError, match="concept"):
            validate_payload(
                ProposalKind.COLUMN_MEANING,
                {"table": "silver.a", "column": "id", "means": "x"},
            )
