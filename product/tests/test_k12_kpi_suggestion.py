"""K12 -- what the suggestion agent guarantees without a model in the loop.

The agent's *quality* is judged by eye in the cookbook and recorded as a baseline. What is
testable here is the machinery that makes its output safe: that it cannot express a
feasibility verdict, that the verdict it is given comes from the executor, and that what
reaches the discovery screen is the contract's own type rather than a copy of it.
"""

from __future__ import annotations

from typing import Any, List

import pytest
from egpt_retail.api.models import (
    Confidence,
    FeasibilityVerdict,
    KpiSuggestion,
    ResultShape,
)
from egpt_retail.contracts.kpi import assess_feasibility
from egpt_retail.contracts.vocabulary import load_vocabulary_set
from egpt_retail.skills import kpi_suggestion as k12


def _feasibility(suggestion: KpiSuggestion) -> Any:
    """The verdict, asserting it is there.

    `feasibility` is Optional on the contract because the schema allows a suggestion without
    one; `assemble` always stamps it, and that is exactly what these tests are checking, so
    an absent one should fail loudly here rather than be silently skipped by a null check.
    """
    assert suggestion.feasibility is not None, suggestion.identifier
    return suggestion.feasibility


TABLES = frozenset({"crm_deal", "crm_contact", "analytics_event"})


def _draft(**overrides: Any) -> k12.KpiSuggestionDraft:
    fields: dict = {
        "identifier": "quote_request_count",
        "name": "Quote requests",
        "description": "How many came in.",
        "business_area": "sales",
        "grain": "overall",
        "concept": "QuoteRequest",
        "result_shape": k12.DraftResultShape.SCALAR,
        "confidence": k12.DraftConfidence.HIGH,
        "rationale": "crm_deal.deal_id counts them.",
        "derived_from_tables": ["crm_deal"],
        "proposed_sql": "SELECT count(*) FROM crm_deal",
    }
    fields.update(overrides)
    return k12.KpiSuggestionDraft(**fields)


def _set(*drafts: k12.KpiSuggestionDraft, notes: str = "") -> k12.KpiSuggestionDraftSet:
    return k12.KpiSuggestionDraftSet(suggestions=list(drafts), notes=notes)


class TestTheAgentCannotExpressAVerdict:
    """The strongest form of "feasibility is never a model judgment": no field for it."""

    def test_the_draft_schema_has_no_feasibility_field(self) -> None:
        assert "feasibility" not in k12.KpiSuggestionDraft.model_fields
        assert "verdict" not in k12.KpiSuggestionDraft.model_fields

    def test_the_draft_schema_does_not_let_it_claim_authorship(self) -> None:
        """`proposed_by` is stamped by the assembler; a model asserting it proves nothing."""
        assert "proposed_by" not in k12.KpiSuggestionDraft.model_fields

    def test_an_extra_field_is_refused_rather_than_carried(self) -> None:
        with pytest.raises(Exception):
            k12.KpiSuggestionDraft(**{**_draft().model_dump(), "feasibility": "ready"})  # type: ignore[arg-type]


class TestTheVerdictComesFromTheExecutor:
    def test_it_matches_an_independent_assessment(self) -> None:
        """The package's evidence line, as a test: no suggestion carries a feasibility the
        executor disagrees with."""
        drafts = _set(
            _draft(),
            _draft(
                identifier="cross_source_rate",
                derived_from_tables=["crm_deal", "absent_table"],
                proposed_sql=None,
            ),
        )
        report = k12.assemble(drafts, available_tables=TABLES)
        assert len(report.suggestions) == 2
        for suggestion in report.suggestions:
            independent = assess_feasibility(
                k12._looks_like_a_definition(
                    next(
                        d
                        for d in drafts.suggestions
                        if d.identifier == suggestion.identifier
                    )
                ),
                TABLES,
            )
            assert _feasibility(suggestion).verdict.value == independent.verdict.value
            assert sorted(_feasibility(suggestion).missing_tables) == sorted(
                independent.missing_tables
            )

    def test_a_blocked_suggestion_names_every_missing_table(self) -> None:
        report = k12.assemble(
            _set(
                _draft(
                    identifier="two_absent",
                    derived_from_tables=["absent_one", "absent_two"],
                    proposed_sql=None,
                )
            ),
            available_tables=TABLES,
        )
        [suggestion] = report.suggestions
        assert _feasibility(suggestion).verdict is FeasibilityVerdict.blocked
        assert _feasibility(suggestion).missing_tables == ["absent_one", "absent_two"]

    def test_partial_is_distinguished_from_blocked(self) -> None:
        report = k12.assemble(
            _set(
                _draft(
                    identifier="one_absent",
                    derived_from_tables=["crm_deal", "absent"],
                    proposed_sql=None,
                )
            ),
            available_tables=TABLES,
        )
        assert _feasibility(report.suggestions[0]).verdict is FeasibilityVerdict.partial

    def test_the_snapshot_version_travels_with_the_verdict(self) -> None:
        report = k12.assemble(
            _set(_draft()), available_tables=TABLES, snapshot_version="snap.4"
        )
        assert _feasibility(report.suggestions[0]).semantic_snapshot == "snap.4"


class TestWhatReachesTheScreenIsTheContractType:
    def test_assemble_returns_the_generated_model(self) -> None:
        """Not a hand-written copy. The plan requires these to render without
        transformation, so a field the agent invents and the screen does not know would be
        an empty cell; building the generated type makes that a construction error here."""
        report = k12.assemble(_set(_draft()), available_tables=TABLES)
        assert isinstance(report.suggestions[0], KpiSuggestion)

    def test_every_required_contract_field_is_populated(self) -> None:
        report = k12.assemble(_set(_draft()), available_tables=TABLES)
        dumped = report.suggestions[0].model_dump()
        required = [
            name
            for name, field in KpiSuggestion.model_fields.items()
            if field.is_required()
        ]
        assert required, (
            "the contract model declares no required fields; check the generator"
        )
        for name in required:
            assert dumped[name] not in (None, "", []), name

    def test_the_skill_reference_is_stamped(self) -> None:
        report = k12.assemble(_set(_draft()), available_tables=TABLES)
        assert report.suggestions[0].proposed_by == k12.SKILL

    def test_the_draft_enums_mirror_the_contract_enums(self) -> None:
        """Two enums for one closed set: the generated one is regenerated from the spec, and
        the draft one is what the model reads through the prompt. They must not drift."""
        assert {m.value for m in k12.DraftConfidence} == {m.value for m in Confidence}
        assert {m.value for m in k12.DraftResultShape} == {m.value for m in ResultShape}


class TestDraftsThatAreDropped:
    def _dropped(self, draft: k12.KpiSuggestionDraft, **kw: Any) -> List[str]:
        return k12.assemble(_set(draft), available_tables=TABLES, **kw).dropped

    def test_a_duplicate_of_the_registry_is_dropped(self) -> None:
        assert self._dropped(_draft(), known_identifiers=["quote_request_count"])

    def test_the_same_identifier_twice_in_one_response_is_dropped_once(self) -> None:
        report = k12.assemble(_set(_draft(), _draft()), available_tables=TABLES)
        assert len(report.suggestions) == 1
        assert len(report.dropped) == 1

    @pytest.mark.parametrize("bad", ["Quote Count", "x", "1_leading_digit", "has-dash"])
    def test_an_unusable_identifier_is_dropped(self, bad: str) -> None:
        """It becomes a Python identifier and a view name; both refuse the rest."""
        assert self._dropped(_draft(identifier=bad))

    def test_a_business_area_outside_the_vocabulary_is_dropped(self) -> None:
        assert self._dropped(_draft(business_area="marketng"))

    def test_a_grain_outside_the_vocabulary_is_dropped(self) -> None:
        assert self._dropped(_draft(grain="per_wednesday"))

    def test_a_formula_reading_an_unlisted_table_is_dropped(self) -> None:
        """The drift that makes a verdict lie: feasibility subtracts the declared tables, so
        a formula reading one it did not declare reports ready and fails on first run."""
        assert self._dropped(
            _draft(
                derived_from_tables=["crm_deal"],
                proposed_sql="SELECT count(*) FROM crm_deal JOIN crm_contact ON true",
            )
        )

    def test_one_bad_draft_does_not_lose_the_others(self) -> None:
        report = k12.assemble(
            _set(_draft(), _draft(identifier="bad name")), available_tables=TABLES
        )
        assert len(report.suggestions) == 1
        assert len(report.dropped) == 1

    def test_a_refusal_carries_its_notes_through(self) -> None:
        report = k12.assemble(
            _set(notes="no table maps to a concept"), available_tables=TABLES
        )
        assert report.suggestions == []
        assert report.notes == "no table maps to a concept"


class TestPromptCarriesItsClosedSets:
    def test_no_placeholder_survives(self) -> None:
        assert "{{" not in k12.render_prompt(catalogue=[{"name": "Gross Margin %"}])

    def test_both_vocabularies_are_rendered_in_full(self) -> None:
        vocabularies = load_vocabulary_set()
        prompt = k12.render_prompt(vocabularies=vocabularies)
        for name in ("business_area", "grain"):
            for term in vocabularies[name].values:
                assert f"`{term}`" in prompt, (name, term)

    def test_the_registry_reaches_the_prompt(self) -> None:
        assert "`deal__won_value`" in k12.render_prompt(existing=["deal__won_value"])

    def test_the_catalogue_reaches_the_prompt(self) -> None:
        prompt = k12.render_prompt(
            catalogue=[
                {"name": "Inventory Turns", "formula_text": "COGS / Avg Inventory"}
            ]
        )
        assert "Inventory Turns: COGS / Avg Inventory" in prompt

    def test_an_absent_catalogue_says_so_rather_than_rendering_nothing(self) -> None:
        """A silently empty catalogue is an agent inventing metrics with no signal that it
        was given none."""
        assert "no catalogue supplied" in k12.render_prompt(catalogue=[])

    def test_a_prompt_missing_a_placeholder_is_refused(self, tmp_path: Any) -> None:
        broken = tmp_path / "broken.md"
        broken.write_text("nothing here")
        with pytest.raises(ValueError, match="business_area"):
            k12.render_prompt(prompt_path=broken)


class TestQualifiedTableNames:
    """The harvest shows `silver.crm_deal`; the SQL says `crm_deal`; the semantic layer
    returns bare names. The first live run dropped all ten suggestions on that mismatch."""

    def test_a_qualified_table_is_accepted_and_normalised(self) -> None:
        report = k12.assemble(
            _set(
                _draft(
                    derived_from_tables=["silver.crm_deal"],
                    proposed_sql="SELECT count(*) FROM crm_deal",
                )
            ),
            available_tables=TABLES,
        )
        assert report.dropped == []
        assert report.suggestions[0].derived_from_tables == ["crm_deal"]
        assert _feasibility(report.suggestions[0]).verdict is FeasibilityVerdict.ready

    def test_mixed_spellings_of_one_table_collapse(self) -> None:
        report = k12.assemble(
            _set(
                _draft(
                    derived_from_tables=["silver.crm_deal", "CRM_DEAL"],
                    proposed_sql="SELECT count(*) FROM crm_deal",
                )
            ),
            available_tables=TABLES,
        )
        assert report.suggestions[0].derived_from_tables == ["crm_deal"]

    def test_a_qualified_missing_table_is_still_blocked(self) -> None:
        report = k12.assemble(
            _set(
                _draft(
                    identifier="absent_metric",
                    derived_from_tables=["silver.absent"],
                    proposed_sql=None,
                )
            ),
            available_tables=TABLES,
        )
        [suggestion] = report.suggestions
        assert _feasibility(suggestion).verdict is FeasibilityVerdict.blocked
        assert _feasibility(suggestion).missing_tables == ["absent"]
