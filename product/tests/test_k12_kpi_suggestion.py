"""K12 -- what the suggestion agent guarantees without a model in the loop.

The agent's *quality* is judged by eye in the cookbook and recorded as a baseline. What is
testable here is the machinery that makes its output safe: that it cannot express a
feasibility verdict, that the verdict it is given comes from the executor, and that what
reaches the discovery screen is the contract's own type rather than a copy of it.
"""

from __future__ import annotations

import json
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
        "business_value": "Tracks demand coming into sales before it converts to revenue.",
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

    def test_business_value_is_carried_from_the_draft_to_the_contract_type(
        self,
    ) -> None:
        """`business_value` answers a different question than `rationale` (why the metric is
        worth tracking, not what data it derives from) and is assembled the same plain way --
        no feasibility-style computation, just carried across, the same as `rationale` is."""
        report = k12.assemble(
            _set(
                _draft(business_value="Catches falling demand before it hits revenue.")
            ),
            available_tables=TABLES,
        )
        assert (
            report.suggestions[0].business_value
            == "Catches falling demand before it hits revenue."
        )

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

    def test_business_value_is_optional_on_the_contract_for_a_pre_existing_suggestion(
        self,
    ) -> None:
        """A suggestion run persisted before this field existed reads back with no
        `business_value` at all -- the field must not be required, or a stored row from
        before this change becomes an unreadable contract violation."""
        report = k12.assemble(_set(_draft()), available_tables=TABLES)
        dumped = report.suggestions[0].model_dump()
        del dumped["business_value"]
        KpiSuggestion(**dumped)  # must not raise

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


# --- proposals: the adopt action D10 requires ------------------------------------------


def _proposal_drafts(*suggestions):
    return k12.KpiSuggestionDraftSet(suggestions=list(suggestions), notes="")


def _proposal_draft(
    identifier="quoterequest__count",
    sql="SELECT count(*) FROM crm_deal",
    source=None,
    column_usage=None,
    table_usage=None,
):
    return k12.KpiSuggestionDraft(
        identifier=identifier,
        name="Quote requests",
        description="How many.",
        business_area="sales",
        grain="overall",
        concept="QuoteRequest",
        result_shape="scalar",
        confidence="high",
        rationale="crm_deal.deal_id counts them.",
        business_value="Tracks demand coming into sales before it converts to revenue.",
        derived_from_tables=["silver.crm_deal"],
        proposed_sql=sql,
        catalogue_source=source,
        column_usage=column_usage or [],
        table_usage=table_usage or [],
    )


class TestFormulaExplanation:
    """The discovery panel's FORMULA card -- a human-readable rendering of `proposed_sql`,
    threaded through the same draft -> `assemble()` -> contract pipeline as every other field."""

    def test_a_draft_with_no_formula_explanation_assembles_to_a_null_one(self) -> None:
        report = k12.assemble(_set(_draft()), available_tables=frozenset({"crm_deal"}))
        assert report.suggestions[0].formula_explanation is None

    def test_a_draft_s_formula_explanation_is_carried_through_assemble(self) -> None:
        draft = _draft(
            formula_explanation=k12.FormulaExplanationDraft(
                expression_before="value = ROUND( 100 x",
                numerator_label="count of buy inquiries with converted_to_quote = true",
                denominator_label="count of buy inquiries",
                expression_after=", 1 )",
                condition_label='where chat_type contains "buy"',
            )
        )
        report = k12.assemble(_set(draft), available_tables=frozenset({"crm_deal"}))
        explanation = report.suggestions[0].formula_explanation
        assert explanation is not None
        assert explanation.expression_before == "value = ROUND( 100 x"
        assert (
            explanation.numerator_label
            == "count of buy inquiries with converted_to_quote = true"
        )
        assert explanation.denominator_label == "count of buy inquiries"
        assert explanation.expression_after == ", 1 )"
        assert explanation.condition_label == 'where chat_type contains "buy"'

    def test_the_draft_schema_refuses_a_numerator_with_no_denominator(self) -> None:
        """The draft's own field is untyped-permissive (both are just `Optional[str]`), so
        this asymmetry is only caught once it reaches the contract's `FormulaExplanation` --
        pinned here so a future change that skips that construction doesn't silently let it
        through `assemble()`."""
        from egpt_retail.contracts.kpi import FormulaExplanation

        with pytest.raises(ValueError, match="must both be set or both be null"):
            FormulaExplanation(expression_before="value = X", numerator_label="a")


class TestColumnAndTableUsage:
    """ "The columns it uses" and "the data it reads"'s fact/lookup role -- the same
    draft -> `assemble()` -> contract pipeline `TestFormulaExplanation` covers."""

    def test_a_draft_with_no_usage_assembles_to_empty_lists(self) -> None:
        report = k12.assemble(_set(_draft()), available_tables=frozenset({"crm_deal"}))
        suggestion = report.suggestions[0]
        assert suggestion.column_usage == []
        assert suggestion.table_usage == []

    def test_a_draft_s_usage_is_carried_through_assemble(self) -> None:
        draft = _draft(
            column_usage=[
                k12.ColumnUsageDraft(
                    table="crm_deal",
                    column="deal_id",
                    used_as=k12.ColumnUsageRoleDraft.MEASURE,
                ),
                k12.ColumnUsageDraft(
                    table="crm_deal",
                    column="quote_request_source_url",
                    used_as=k12.ColumnUsageRoleDraft.FILTER,
                    why="Only column that marks a deal as a quote request.",
                ),
            ],
            table_usage=[
                k12.TableUsageDraft(table="crm_deal", role=k12.TableRoleDraft.FACT),
            ],
        )
        report = k12.assemble(_set(draft), available_tables=frozenset({"crm_deal"}))
        suggestion = report.suggestions[0]
        assert [cu.column for cu in suggestion.column_usage] == [
            "deal_id",
            "quote_request_source_url",
        ]
        assert suggestion.column_usage[0].used_as.value == "measure"
        assert suggestion.column_usage[1].used_as.value == "filter"
        assert (
            suggestion.column_usage[1].why
            == "Only column that marks a deal as a quote request."
        )
        assert suggestion.table_usage[0].table == "crm_deal"
        assert suggestion.table_usage[0].role.value == "fact"
        assert suggestion.table_usage[0].join_note == ""

    def test_qualified_table_names_are_bared_the_same_as_derived_from_tables(
        self,
    ) -> None:
        """The agent sees tables qualified (`silver.crm_deal`) in the harvest text, and
        `derived_from_tables` is already normalised to bare names before it reaches the
        contract -- `column_usage`/`table_usage` must match, or the drift guard below would
        refuse a draft whose own tables agree with each other in every way except spelling."""
        draft = _draft(
            derived_from_tables=["silver.crm_deal"],
            column_usage=[
                k12.ColumnUsageDraft(
                    table="silver.crm_deal",
                    column="deal_id",
                    used_as=k12.ColumnUsageRoleDraft.MEASURE,
                ),
            ],
            table_usage=[
                k12.TableUsageDraft(
                    table="silver.crm_deal", role=k12.TableRoleDraft.FACT
                ),
            ],
        )
        report = k12.assemble(_set(draft), available_tables=frozenset({"crm_deal"}))
        assert report.dropped == []
        suggestion = report.suggestions[0]
        assert suggestion.column_usage[0].table == "crm_deal"
        assert suggestion.table_usage[0].table == "crm_deal"

    def test_a_table_not_in_derived_from_tables_is_dropped_as_drift(self) -> None:
        """The same class of drift `KpiDefinition` already refuses between `sql` and
        `source_tables` -- caught here, at assembly, rather than surfacing only at adoption."""
        draft = _draft(
            table_usage=[
                k12.TableUsageDraft(
                    table="crm_contact", role=k12.TableRoleDraft.LOOKUP
                ),
            ],
        )
        report = k12.assemble(_set(draft), available_tables=frozenset({"crm_deal"}))
        assert report.suggestions == []
        assert report.dropped and "quote_request_count" in report.dropped[0]

    def test_a_proposal_carrying_usage_is_json_serializable(self) -> None:
        """Regression: `as_proposals()` is written straight to jsonb by the proposal store
        (`egpt_retail.semantic.proposals`), so its payload must survive a real `json.dumps` --
        not just pydantic's own `model_dump()`, which leaves enum fields (`used_as`, `role`)
        as `ColumnUsageRole`/`TableRole` instances unless `mode="json"` is passed."""
        draft = _proposal_draft(
            column_usage=[
                k12.ColumnUsageDraft(
                    table="silver.crm_deal",
                    column="deal_id",
                    used_as=k12.ColumnUsageRoleDraft.MEASURE,
                ),
            ],
            table_usage=[
                k12.TableUsageDraft(
                    table="silver.crm_deal", role=k12.TableRoleDraft.FACT
                ),
            ],
        )
        report = k12.assemble(
            _proposal_drafts(draft), available_tables=frozenset({"crm_deal"})
        )
        payload = report.as_proposals()[0].payload
        json.dumps(payload)  # must not raise TypeError
        assert payload["column_usage"][0]["used_as"] == "measure"
        assert payload["table_usage"][0]["role"] == "fact"


def test_a_suggestion_with_a_formula_becomes_a_kpi_proposal_the_registry_can_adopt():
    """Build plan section 06: candidates "are proposals, so they carry an adopt action and
    enter the registry only when a person accepts them". This is the producer that was
    missing. The payload round-trips through K7's validator into a KpiDefinition, which is
    exactly what adoption saves."""
    from egpt_retail.semantic.proposals import ProposalKind, validate_payload

    report = k12.assemble(
        _proposal_drafts(_proposal_draft(source="Quote request volume")),
        available_tables=frozenset({"crm_deal"}),
        snapshot_version="snap-1",
    )
    proposals = report.as_proposals()
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.kind is ProposalKind.KPI
    assert proposal.target == "quoterequest__count"
    assert proposal.proposer == "kpi_suggestion@1.0.0"
    definition = validate_payload(ProposalKind.KPI, proposal.payload)
    assert definition.identifier == "quoterequest__count"
    assert definition.source_tables == frozenset({"crm_deal"})
    assert proposal.payload["catalogue_source"] == "Quote request volume"
    assert proposal.payload["feasibility"] == "ready"


def test_a_suggestion_without_a_formula_is_kept_but_not_proposed():
    """The placeholder formula assembly uses for the feasibility check must never reach the
    registry as a formula."""
    report = k12.assemble(
        _proposal_drafts(_proposal_draft(sql=None)),
        available_tables=frozenset({"crm_deal"}),
    )
    assert [s.identifier for s in report.suggestions] == ["quoterequest__count"]
    assert report.without_formula == ["quoterequest__count"]
    assert report.as_proposals() == []


def test_catalogue_provenance_is_carried_beside_each_suggestion():
    """The review wants "from the curated registry" and "inferred from your schema" badged
    differently. The contract has no field for it (R-16); the report carries it."""
    report = k12.assemble(
        _proposal_drafts(
            _proposal_draft(identifier="a__one", source="Quote request volume"),
            _proposal_draft(identifier="a__two", source=None),
            _proposal_draft(identifier="a__three", source="   "),
        ),
        available_tables=frozenset({"crm_deal"}),
    )
    assert report.catalogue_source == {
        "a__one": "Quote request volume",
        "a__two": None,
        "a__three": None,
    }


# --- round 1 of PR 208: the suggestion-time verdict is the join-aware one ---------------


def test_a_suggestion_over_an_unjoinable_pair_is_blocked_at_suggestion_time():
    """Without the oracle this reported READY at suggestion time and BLOCKED after adoption
    -- the badge that quietly starts lying, one stage earlier than K12.5 closed it."""
    draft = _proposal_draft(
        identifier="websession__replay_coverage",
        sql="SELECT count(*) FROM analytics_event e JOIN session_replay_session s ON s.session_id = e.session_id",
    )
    draft = draft.model_copy(
        update={"derived_from_tables": ["analytics_event", "session_replay_session"]}
    )
    tables = frozenset({"analytics_event", "session_replay_session"})

    def ruled_out(left: str, right: str) -> bool:
        return {left, right} == {"analytics_event", "session_replay_session"}

    plain = k12.assemble(_proposal_drafts(draft), available_tables=tables)
    aware = k12.assemble(
        _proposal_drafts(draft), available_tables=tables, known_unjoinable=ruled_out
    )
    assert plain.suggestions[0].feasibility.verdict.value == "ready"
    assert aware.suggestions[0].feasibility.verdict.value == "blocked"
    assert aware.feasibility_reason == {"websession__replay_coverage": "not_joinable"}
    assert "must not be joined" in aware.suggestions[0].feasibility.rationale
    payload = aware.as_proposals()[0].payload
    assert payload["feasibility"] == "blocked"
    assert payload["feasibility_reason"] == "not_joinable"


def test_the_oracle_changes_nothing_for_a_pair_review_has_not_ruled_on():
    report = k12.assemble(
        _proposal_drafts(_proposal_draft()),
        available_tables=frozenset({"crm_deal"}),
        known_unjoinable=lambda left, right: False,
    )
    assert report.suggestions[0].feasibility.verdict.value == "ready"
    assert report.feasibility_reason == {"quoterequest__count": "none"}


def test_the_async_assembly_takes_exactly_the_sync_arguments():
    """The async twin had drifted behind the sync one twice. Pin the signatures together."""
    import inspect

    assert (
        inspect.signature(k12.aassemble).parameters.keys()
        == inspect.signature(k12.assemble).parameters.keys()
    )
