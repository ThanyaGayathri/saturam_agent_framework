"""K8 -- what the two proposers guarantee without a model in the loop.

The agents' *quality* is judged by eye in the cookbook, and that number is the baseline for
every later prompt change. What is testable here is the machinery around them: that the
prompts carry their closed sets in full, that a response becomes proposals of the right shape,
and that the three things the schema must refuse are refused.
"""

from __future__ import annotations

from typing import List

import pytest
from egpt_retail.contracts.vocabulary import load_vocabulary_set
from egpt_retail.semantic.proposals import ProposalKind, ReviewState
from egpt_retail.skills import enrichment_proposer as enrich
from egpt_retail.skills import mapping_proposer as mapping


def _proposal(
    kind: enrich.EnrichmentKind, value: str, **kw
) -> enrich.EnrichmentProposal:
    return enrich.EnrichmentProposal(
        value=value,
        kind=kind,
        confidence=kw.get("confidence", enrich.Confidence.HIGH),
        evidence=kw.get("evidence", "crm_contact.email"),
    )


class TestPromptsCarryTheirClosedSets:
    """A model that can only see a field name will invent a plausible value for it."""

    def test_every_enrichment_kind_is_written_out(self) -> None:
        prompt = enrich.render_prompt()
        for member in enrich.EnrichmentKind:
            assert f"`{member.value}`" in prompt, member

    def test_every_confidence_value_is_written_out(self) -> None:
        prompt = enrich.render_prompt()
        for member in enrich.Confidence:
            assert f"`{member.value}`" in prompt, member

    def test_the_vocabularies_are_rendered_not_hand_typed(self) -> None:
        """A hand-typed list is correct until the first customer extends the ontology."""
        vocabularies = load_vocabulary_set()
        prompt = enrich.render_prompt(vocabularies=vocabularies)
        for name in ("business_area", "compliance_tag"):
            for term in vocabularies[name].values:
                assert f"`{term}`" in prompt, (name, term)

    def test_no_placeholder_survives_rendering(self) -> None:
        assert "{{" not in enrich.render_prompt()
        assert "{{" not in mapping.render_prompt(
            concepts=["Contact"], collisions=["brand"]
        )

    def test_a_prompt_missing_its_placeholder_is_refused(self, tmp_path) -> None:
        """Silently rendering a prompt with no slot for the vocabulary would ship an agent
        that invents terms, and nothing downstream would say so."""
        broken = tmp_path / "broken.md"
        broken.write_text("no placeholders here")
        with pytest.raises(ValueError, match="business_area"):
            enrich.render_prompt(prompt_path=broken)
        with pytest.raises(ValueError, match="concepts"):
            mapping.render_prompt(concepts=["Contact"], prompt_path=broken)

    def test_the_mapping_prompt_lists_every_permitted_concept(self) -> None:
        prompt = mapping.render_prompt(
            concepts=["Contact", "QuoteRequest"], collisions=[]
        )
        assert "`Contact`" in prompt and "`QuoteRequest`" in prompt

    def test_collisions_reach_the_prompt(self) -> None:
        prompt = mapping.render_prompt(concepts=["Contact"], collisions=["session_id"])
        assert "`session_id`" in prompt


class TestEnrichmentToProposals:
    def _response(self, **kw) -> enrich.EnrichmentProposalSet:
        return enrich.EnrichmentProposalSet(
            concept=kw.get("concept", "Contact"),
            business_area=kw.get("business_area", "customer"),
            proposals=kw.get(
                "proposals",
                [
                    _proposal(enrich.EnrichmentKind.SYNONYM, "buyer"),
                    _proposal(enrich.EnrichmentKind.COMPLIANCE_TAG, "pii"),
                    _proposal(enrich.EnrichmentKind.SQL_FILTER, "c.deleted = false"),
                ],
            ),
        )

    def test_one_concept_becomes_one_proposal(self) -> None:
        """A reviewer accepts a concept as enriched, not a fragment of one: accepting the
        synonyms and rejecting the filter would leave a state no proposal describes."""
        rows = enrich.to_proposals(self._response(), proposer="agent")
        assert len(rows) == 1
        assert rows[0].kind is ProposalKind.ENRICHMENT
        assert rows[0].state is ReviewState.PENDING

    def test_each_kind_lands_on_its_concept_field(self) -> None:
        payload = enrich.to_proposals(self._response(), proposer="agent")[0].payload
        assert payload["synonyms"] == ["buyer"]
        assert payload["compliance_tags"] == ["pii"]
        assert payload["sql_filter"] == "c.deleted = false"
        assert payload["business_area"] == "customer"

    def test_a_refusal_proposes_nothing(self) -> None:
        """A row saying "nothing" is a review item with no decision in it."""
        empty = enrich.EnrichmentProposalSet(
            concept="Contact", notes="no related tables"
        )
        assert enrich.to_proposals(empty, proposer="agent") == []

    def test_a_compliance_tag_outside_the_vocabulary_is_refused(self) -> None:
        """The model was given the list. A term outside it is the closed-set rule failing,
        and passing it on would put an unreviewable tag in front of a person."""
        response = self._response(
            proposals=[_proposal(enrich.EnrichmentKind.COMPLIANCE_TAG, "top_secret")]
        )
        with pytest.raises(ValueError, match="not a permitted compliance_tag"):
            enrich.to_proposals(response, proposer="agent")

    def test_a_business_area_outside_the_vocabulary_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a permitted business_area"):
            enrich.to_proposals(
                self._response(business_area="marketng"), proposer="agent"
            )

    def test_duplicate_values_collapse(self) -> None:
        response = self._response(
            proposals=[
                _proposal(enrich.EnrichmentKind.SYNONYM, "buyer"),
                _proposal(enrich.EnrichmentKind.SYNONYM, "buyer"),
            ]
        )
        assert enrich.to_proposals(response, proposer="agent")[0].payload[
            "synonyms"
        ] == ["buyer"]


class TestMappingToProposals:
    def _mapping(self, table: str, concept: str, **kw) -> mapping.MappingProposal:
        return mapping.MappingProposal(
            table=table,
            concept=concept,
            confidence=kw.get("confidence", mapping.Confidence.HIGH),
            evidence=kw.get("evidence", "the table's key and columns"),
            authoritative=kw.get("authoritative", False),
            alternatives=kw.get("alternatives", []),
        )

    def test_a_mapping_becomes_a_mapping_proposal(self) -> None:
        response = mapping.MappingProposalSet(
            mappings=[self._mapping("silver.crm_deal", "QuoteRequest")]
        )
        rows = mapping.to_proposals(
            response, proposer="agent", known_concepts=["QuoteRequest"]
        )
        assert [r.kind for r in rows] == [ProposalKind.MAPPING]
        assert rows[0].payload["table"] == "silver.crm_deal"

    def test_a_concept_outside_the_ontology_is_dropped(self) -> None:
        """The prompt enumerates every permitted name, so a name outside it is the closed-set
        rule failing -- and forwarding it would show a reviewer a concept nobody defined."""
        response = mapping.MappingProposalSet(
            mappings=[self._mapping("silver.crm_deal", "Invented")]
        )
        assert (
            mapping.to_proposals(response, proposer="agent", known_concepts=["Contact"])
            == []
        )

    def test_alternatives_are_derived_not_taken_from_the_model(self) -> None:
        """The alternatives to a table are the other tables mapped to the same concept --
        derivable from the response, so not something a model needs to get right."""
        response = mapping.MappingProposalSet(
            mappings=[
                self._mapping(
                    "silver.a", "WebSession", authoritative=True, alternatives=["wrong"]
                ),
                self._mapping("silver.b", "WebSession"),
            ]
        )
        rows = mapping.to_proposals(
            response, proposer="agent", known_concepts=["WebSession"]
        )
        source = next(r for r in rows if r.kind is ProposalKind.AUTHORITATIVE_SOURCE)
        assert source.payload["alternatives"] == ["b"]
        assert source.payload["table"] == "a"

    def test_an_uncontested_concept_gets_no_authoritative_proposal(self) -> None:
        """A concept one table serves has no contest to settle, and a proposal saying "this
        is the only table" is queue noise."""
        response = mapping.MappingProposalSet(
            mappings=[self._mapping("silver.only", "Contact", authoritative=True)]
        )
        rows = mapping.to_proposals(
            response, proposer="agent", known_concepts=["Contact"]
        )
        assert all(r.kind is not ProposalKind.AUTHORITATIVE_SOURCE for r in rows)

    def test_collision_resolutions_become_column_meanings(self) -> None:
        """G19's home is the column, not the concept's question patterns (P3 verification)."""
        response = mapping.MappingProposalSet(
            collision_resolutions=[
                mapping.CollisionResolution(
                    column_name="session_id",
                    table="silver.analytics_event",
                    concept="WebSession",
                    means="the analytics vendor's session id",
                    joinable=False,
                )
            ]
        )
        rows = mapping.to_proposals(
            response, proposer="agent", known_concepts=["WebSession"]
        )
        [row] = rows
        assert row.kind is ProposalKind.COLUMN_MEANING
        assert row.target == "silver.analytics_event.session_id"
        assert row.payload == {
            "table": "silver.analytics_event",
            "column": "session_id",
            "concept": "WebSession",
            "means": "the analytics vendor's session id",
            "joinable": False,
        }


class TestUnresolvedCollisions:
    def _resolution(self, table: str) -> mapping.CollisionResolution:
        return mapping.CollisionResolution(
            column_name="session_id", table=table, concept="WebSession", means="an id"
        )

    def test_one_side_resolved_is_not_resolved(self) -> None:
        """A name carried by two tables needs two resolutions: one leaves the name meaning
        two things with only one written down, which looks answered and is not."""
        response = mapping.MappingProposalSet(
            collision_resolutions=[self._resolution("silver.analytics_event")]
        )
        assert mapping.unresolved_collisions(response, ["session_id"]) == ["session_id"]

    def test_both_sides_resolved_clears_it(self) -> None:
        response = mapping.MappingProposalSet(
            collision_resolutions=[
                self._resolution("silver.analytics_event"),
                self._resolution("silver.session_replay_session"),
            ]
        )
        assert mapping.unresolved_collisions(response, ["session_id"]) == []

    def test_a_collision_never_mentioned_is_unresolved(self) -> None:
        assert mapping.unresolved_collisions(
            mapping.MappingProposalSet(), ["brand"]
        ) == ["brand"]


class TestNeitherAgentCanWrite:
    def test_no_proposer_module_imports_a_store(self) -> None:
        """The D10 guarantee is structural, not a promise in a prompt: these modules have no
        write path to the graph, so no prompt injection or model error can produce one."""
        import inspect

        for module in (enrich, mapping):
            source = inspect.getsource(module)
            assert "SemanticGraphStore" not in source, module.__name__
            assert "neo4j" not in source.lower(), module.__name__

    def test_every_produced_proposal_is_pending(self) -> None:
        response = mapping.MappingProposalSet(
            mappings=[
                mapping.MappingProposal(
                    table="silver.crm_deal",
                    concept="QuoteRequest",
                    confidence=mapping.Confidence.HIGH,
                    evidence="e",
                )
            ]
        )
        rows: List = mapping.to_proposals(
            response, proposer="agent", known_concepts=["QuoteRequest"]
        )
        assert all(r.state is ReviewState.PENDING for r in rows)
