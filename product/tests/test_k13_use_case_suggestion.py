"""K13 -- the use case suggestion draft schema, its shape gate and its assembly.

DB-free. Every test here can fail: the fixtures are built so that a wrong implementation
produces a different value, not the same one by another route.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from pathlib import Path

import pytest
from pydantic import ValidationError

from egpt_retail.api.models import Autonomy as ContractAutonomy
from egpt_retail.api.models import Confidence, UseCaseSuggestionList
from egpt_retail.contracts.vocabulary import load_vocabulary_set
from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.skills.use_case_suggestion import (
    PROMPT_PATH,
    SHAPE_REQUIREMENTS,
    Autonomy,
    DraftConfidence,
    UseCaseFeasibility,
    UseCaseShape,
    UseCaseSuggestionDraft,
    UseCaseSuggestionDraftSet,
    aassemble,
    arender_prompt,
    assemble,
    assess_shape_readiness,
    readiness_lines,
    render_prompt,
)

TABLES = frozenset({"crm_deal", "crm_contact", "web_session"})
KPIS = ("quoterequest__count", "quoterequest__ageing_days")
FULLY_CONFIGURED = frozenset(
    {
        "delivery_channel",
        "alert_threshold",
        "approver",
        "write_target",
        "guardrails",
        "per_run_cap",
        "rollback_point",
    }
)


def _definition(identifier: str, tables: set) -> KpiDefinition:
    table = sorted(tables)[0]
    joins = " ".join(f"JOIN {t} USING (id)" for t in sorted(tables)[1:])
    return KpiDefinition(
        identifier=identifier,
        name=identifier,
        description="",
        sql=f"SELECT count(*) FROM {table} {joins}".strip(),
        grain="overall",
        business_area="sales",
        source_tables=frozenset(tables),
    )


def draft(**overrides: object) -> UseCaseSuggestionDraft:
    fields: dict[str, object] = {
        "title": "Stalled quote requests",
        "description": "Notifies the owner when a request sits past the threshold.",
        "business_area": "sales",
        "shape": UseCaseShape.ALERT,
        "autonomy": Autonomy.COPILOT,
        "confidence": DraftConfidence.HIGH,
        "rationale": "quoterequest__ageing_days over crm_deal.",
        "derived_from_tables": ["silver.crm_deal"],
        "kpi_identifiers": ["quoterequest__ageing_days"],
    }
    fields.update(overrides)
    return UseCaseSuggestionDraft.model_validate(fields)


def response(*drafts, notes: str = "") -> UseCaseSuggestionDraftSet:
    return UseCaseSuggestionDraftSet(suggestions=list(drafts), notes=notes)


def accept(*drafts, configured=FULLY_CONFIGURED, **kwargs):
    return assemble(
        response(*drafts),
        configured=configured,
        known_kpis=KPIS,
        available_tables=TABLES,
        **kwargs,
    )


# --- the shape gate -------------------------------------------------------------------


def test_the_write_shapes_requirements_are_cumulative():
    """An Act needs everything a Recommend needs, and more.

    Not decoration: if Act could omit `guardrails` because its own list forgot it, an
    unattended writer would pass a gate a supervised one failed. Deliberately asserted only
    over the write shapes -- `alert_threshold` is Alert's alone, because a Recommend can be
    produced on a schedule with no condition to watch.
    """
    recommend = set(SHAPE_REQUIREMENTS[UseCaseShape.RECOMMEND])
    assert recommend < set(SHAPE_REQUIREMENTS[UseCaseShape.ACT])
    assert set(SHAPE_REQUIREMENTS[UseCaseShape.INFORM]) < recommend
    assert "alert_threshold" not in recommend
    for needs in SHAPE_REQUIREMENTS.values():
        assert "delivery_channel" in needs


def test_act_is_blocked_where_a_recommend_is_ready():
    """The case the gate exists for, and it must not be a tautology.

    A deployment with an approver and a write target can run a Recommend. The same
    deployment must not run an Act, because nobody is watching and there is nothing to roll
    back to.
    """
    supervised = frozenset(
        {"delivery_channel", "approver", "write_target", "guardrails"}
    )
    assert assess_shape_readiness(UseCaseShape.RECOMMEND, supervised).ready is True
    act = assess_shape_readiness(UseCaseShape.ACT, supervised)
    assert act.ready is False
    assert act.missing == ["per_run_cap", "rollback_point"]
    assert "per_run_cap" in act.rationale


def test_inform_is_ready_on_a_bare_deployment():
    """A deployment that can only show someone a number can still run an Inform."""
    bare = frozenset({"delivery_channel"})
    assert assess_shape_readiness(UseCaseShape.INFORM, bare).ready is True
    assert assess_shape_readiness(UseCaseShape.ALERT, bare).missing == [
        "alert_threshold"
    ]


def test_readiness_names_everything_missing_not_just_the_first():
    """A reviewer configures once from this list, or three times from a first-miss one."""
    verdict = assess_shape_readiness(UseCaseShape.ACT, frozenset())
    assert verdict.missing == list(SHAPE_REQUIREMENTS[UseCaseShape.ACT])


def test_readiness_is_a_set_difference_not_a_lookup():
    """A capability the deployment has that the shape does not need changes nothing."""
    noisy = FULLY_CONFIGURED | {"a_capability_no_shape_asks_for"}
    assert assess_shape_readiness(UseCaseShape.ACT, noisy).ready is True


# --- assembly -------------------------------------------------------------------------


def test_assembly_returns_the_generated_contract_model():
    """The point of the draft/contract split: drift is a construction error.

    ``UseCaseSuggestion`` here is the model generated from the OpenAPI schema. If the
    contract gains a required field, this stops constructing rather than serving a blank.
    """
    report = accept(draft())
    assert len(report.suggestions) == 1
    UseCaseSuggestionList(
        suggestions=report.suggestions, notes="", semantic_snapshot=None
    )
    assert report.suggestions[0].confidence is Confidence.high


def test_table_names_are_normalised_to_the_form_the_layer_returns():
    """K12's defect, and it cost a whole live run: the harvest says `silver.crm_deal`,
    the semantic layer returns `crm_deal`, and an un-normalised comparison drops everything.
    """
    report = accept(draft(derived_from_tables=["SILVER.Crm_Deal", "crm_deal"]))
    assert report.suggestions[0].derived_from_tables == ["crm_deal"]
    assert report.dropped == []


def test_a_suggestion_naming_an_unregistered_kpi_is_dropped():
    report = accept(draft(kpi_identifiers=["quoterequest__margin"]))
    assert report.suggestions == []
    assert "quoterequest__margin" in report.dropped[0]


def test_a_suggestion_over_a_table_the_layer_lacks_is_kept_and_marked_blocked():
    """The review's ruling: blocked suggestions stay visible. "Connect a promotions source to
    unlock 6 more use cases" is the roadmap, and a dropped suggestion is invisible."""
    report = accept(draft(derived_from_tables=["erp_invoice"]))
    assert len(report.suggestions) == 1
    assert report.feasibility[0].verdict is UseCaseFeasibility.BLOCKED
    assert report.feasibility[0].missing_tables == ["erp_invoice"]
    assert report.unlocks == {"erp_invoice": ["Stalled quote requests"]}
    assert report.dropped == []


def test_feasibility_aggregates_over_the_registered_kpis():
    """Ready / mostly ready n-of-m / blocked, from K11's per-KPI set difference."""
    registry = {
        "quoterequest__ageing_days": _definition(
            "quoterequest__ageing_days", {"crm_deal", "crm_deal_stage_history"}
        ),
        "quoterequest__count": _definition("quoterequest__count", {"crm_deal"}),
    }
    report = assemble(
        response(
            draft(
                kpi_identifiers=["quoterequest__ageing_days", "quoterequest__count"],
                derived_from_tables=["crm_deal"],
            )
        ),
        configured=FULLY_CONFIGURED,
        known_kpis=list(registry),
        available_tables=frozenset({"crm_deal"}),
        registry=registry,
    )
    verdict = report.feasibility[0]
    assert verdict.verdict is UseCaseFeasibility.MOSTLY_READY
    assert verdict.ready_kpis == ["quoterequest__count"]
    assert verdict.blocked_kpis == ["quoterequest__ageing_days"]
    assert verdict.missing_tables == ["crm_deal_stage_history"]
    assert verdict.label == "mostly ready -- 1 of 2 metrics"
    assert report.unlocks == {"crm_deal_stage_history": ["Stalled quote requests"]}


def test_use_case_proposals_carry_what_the_contract_cannot():
    """The producer K7's USE_CASE kind was missing. The payload carries shape, readiness and
    feasibility, so a reviewer sees on the proposal what the discovery screen would show."""
    from egpt_retail.semantic.proposals import ProposalKind, validate_payload

    report = accept(draft(), configured=frozenset({"delivery_channel"}))
    proposals = report.as_proposals()
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.kind is ProposalKind.USE_CASE
    assert proposal.target == "stalled_quote_requests"
    assert proposal.proposer == "use_case_suggestion@1.0.0"
    assert proposal.payload["shape"] == "alert"
    assert proposal.payload["shape_ready"] is False
    assert proposal.payload["feasibility"] == "ready"
    validate_payload(ProposalKind.USE_CASE, proposal.payload)


def test_a_suggestion_derived_from_no_table_is_dropped():
    report = accept(draft(derived_from_tables=[]))
    assert report.suggestions == []
    assert "derived from no table" in report.dropped[0]


def test_a_business_area_outside_the_vocabulary_is_dropped():
    report = accept(draft(business_area="growth_hacking"))
    assert report.suggestions == []
    assert "growth_hacking" in report.dropped[0]


def test_one_bad_suggestion_does_not_cost_the_good_ones():
    """Dropping rather than raising. A reviewer loses the one, not the run."""
    report = accept(
        draft(),
        draft(title="Invented", kpi_identifiers=["nope__nope"]),
        draft(title="Quote volume", shape=UseCaseShape.INFORM, kpi_identifiers=[]),
    )
    assert [s.title for s in report.suggestions] == [
        "Stalled quote requests",
        "Quote volume",
    ]
    assert len(report.dropped) == 1


def test_the_same_title_twice_is_kept_once():
    report = accept(draft(), draft(description="Said differently."))
    assert len(report.suggestions) == 1
    assert "proposed twice" in report.dropped[0]


def test_readiness_travels_beside_each_kept_suggestion_in_order():
    report = accept(
        draft(title="Inform one", shape=UseCaseShape.INFORM),
        draft(title="Act one", shape=UseCaseShape.ACT),
        configured=frozenset({"delivery_channel"}),
    )
    assert [r.shape for r in report.readiness] == [
        UseCaseShape.INFORM,
        UseCaseShape.ACT,
    ]
    assert [r.ready for r in report.readiness] == [True, False]
    assert len(report.readiness) == len(report.suggestions)


def test_an_unready_act_is_kept_not_dropped():
    """It answers "what would configuring a write target buy me", which is the question
    the discovery screen exists for. Dropping it hides the answer."""
    report = accept(
        draft(title="Auto-reprice", shape=UseCaseShape.ACT), configured=frozenset()
    )
    assert [s.title for s in report.suggestions] == ["Auto-reprice"]
    assert report.readiness[0].ready is False


def test_the_dropped_shape_is_declared_in_notes_rather_than_vanishing():
    """R-12: the contract has no field for shape. Silence there is a lie by omission."""
    wire = accept(draft()).as_contract_list(snapshot_version="snap-7")
    assert "R-12" in wire.notes
    assert wire.semantic_snapshot == "snap-7"


def test_notes_carry_no_caveat_when_there_is_nothing_to_caveat():
    """An empty list has no shape to drop, so the caveat would be noise."""
    wire = assemble(
        response(notes="No KPIs registered."),
        configured=FULLY_CONFIGURED,
        known_kpis=KPIS,
        available_tables=TABLES,
    ).as_contract_list()
    assert wire.notes == "No KPIs registered."


def test_empty_available_tables_does_not_silently_accept_everything():
    """A caller who has not loaded the semantic layer gets no verification, so the fixture
    must prove the KPI check still runs independently."""
    report = assemble(
        response(draft(kpi_identifiers=["nope__nope"])),
        configured=FULLY_CONFIGURED,
        known_kpis=KPIS,
        available_tables=frozenset(),
    )
    assert report.suggestions == []


# --- the draft schema -----------------------------------------------------------------


def test_the_draft_has_no_readiness_field():
    """The K11/K12 rule: an agent cannot be wrong about something it cannot say."""
    fields = set(UseCaseSuggestionDraft.model_fields)
    assert not fields & {"ready", "readiness", "feasibility", "missing", "blocked"}


def test_an_unexpected_key_is_refused_not_ignored():
    with pytest.raises(ValidationError):
        UseCaseSuggestionDraft(**draft().model_dump(mode="json"), readiness="ready")


def test_an_invented_shape_is_refused():
    with pytest.raises(ValidationError):
        draft(shape="orchestrate")


def test_draft_enums_equal_the_contract_and_the_vocabulary():
    """Defined in three places on purpose, so this is the check they have not drifted."""
    vocabularies = load_vocabulary_set()
    assert {m.value for m in Autonomy} == {m.value for m in ContractAutonomy}
    assert {m.value for m in Autonomy} == vocabularies["autonomy"].values
    assert {m.value for m in DraftConfidence} == {m.value for m in Confidence}
    assert {m.value for m in UseCaseShape} == vocabularies["use_case_shape"].values
    assert set(SHAPE_REQUIREMENTS) == set(UseCaseShape)


def test_no_enum_typed_field_carries_a_description():
    """Azure structured outputs reject a `$ref` with sibling keywords with a 400, and the
    failure lands at call time in a live run rather than here.

    Found by enumerating the model rather than by listing the three fields by name, so a
    fourth enum field added later is covered without anyone remembering to add it.
    """
    enum_fields = [
        name
        for name, field in UseCaseSuggestionDraft.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, Enum)
    ]
    assert set(enum_fields) == {"shape", "autonomy", "confidence"}
    for name in enum_fields:
        assert UseCaseSuggestionDraft.model_fields[name].description is None
    properties = UseCaseSuggestionDraftSet.model_json_schema()["$defs"][
        "UseCaseSuggestionDraft"
    ]["properties"]
    for name in enum_fields:
        assert set(properties[name]) <= {"$ref", "allOf", "anyOf"}


def test_the_schema_forbids_additional_properties():
    schema = UseCaseSuggestionDraftSet.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["UseCaseSuggestionDraft"]["additionalProperties"] is False


# --- the prompt -----------------------------------------------------------------------


def test_render_fills_every_placeholder():
    rendered = render_prompt(kpis=())
    assert "{{" not in rendered
    assert "`sales`" in rendered
    assert "inform" in rendered and "act" in rendered


def test_render_refuses_a_prompt_that_lost_a_placeholder(tmp_path: Path):
    """A closed set silently absent from a prompt is how an agent starts inventing values."""
    stripped = tmp_path / "p.md"
    stripped.write_text(PROMPT_PATH.read_text().replace("{{business_area}}", "sales"))
    with pytest.raises(ValueError, match="business_area"):
        render_prompt(prompt_path=stripped)


def test_registered_kpis_reach_the_prompt():
    class Fake:
        identifier = "quoterequest__count"
        name = "Quote requests"
        source_tables = ("crm_deal",)

    rendered = render_prompt(kpis=[Fake()])
    assert "quoterequest__count" in rendered
    assert "crm_deal" in rendered
    assert "(none registered" not in rendered


def test_the_prompt_declares_the_deterministic_check_it_is_paired_with():
    header = PROMPT_PATH.read_text().split("```")[1]
    assert "assess_shape_readiness" in header
    assert "writes:          nothing" in header


# --- async variants -------------------------------------------------------------------


def test_async_variants_match_the_sync_ones():
    got = asyncio.run(
        aassemble(
            response(draft()),
            configured=FULLY_CONFIGURED,
            known_kpis=KPIS,
            available_tables=TABLES,
        )
    )
    assert got.model_dump(mode="json") == accept(draft()).model_dump(mode="json")
    assert asyncio.run(arender_prompt(kpis=())) == render_prompt(kpis=())


def test_readiness_lines_name_the_shape_and_the_tables():
    lines = readiness_lines(accept(draft(), configured=frozenset()))
    assert lines[0].startswith("alert")
    assert "crm_deal" in lines[0]
    assert "needs:" in lines[0]
