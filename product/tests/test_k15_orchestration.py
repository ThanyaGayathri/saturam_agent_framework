"""K15 -- the workflow compiler, its shape table, and the step-prompt authoring skill.

DB-free. The fixtures are built so a wrong implementation gives a different answer rather than
the same one by another route.
"""

from __future__ import annotations

import asyncio

import pytest

from egpt_retail.contracts.use_case import SHAPE_PARAMETERS, UseCaseShape
from egpt_retail.executors.plan import UnknownPlanRef, plan_from_config, plan_refs
from egpt_retail.orchestration import (
    SHAPE_STAGES,
    ParameterOrigin,
    StageKind,
    UnknownShape,
    acompile_workflow,
    capability_report,
    compile_workflow,
    diff_against,
    normalise_config,
)
from egpt_retail.orchestration.compiler import _step_dict
from egpt_retail.skills import step_prompt as sp

#: A deployment with every executor the four shapes could ask for. Named here rather than read
#: from build_registry() so the tests state what they assume and do not change meaning the day
#: Lane R registers another executor.
FULL = frozenset(
    {
        "read_gold",
        "compare_to_baseline",
        "record_provenance",
        "deliver_in_app",
        "evaluate_alert_condition",
        "write_alert",
        "select_candidates",
        "apply_hard_limits",
        "await_approval",
        "write_to_target",
        "apply_per_run_cap",
        "record_rollback_point",
    }
)

SPEC = {
    "use_case_id": "weekly_pipeline",
    "kpi_identifiers": ["quoterequest__count"],
    "grain": "overall",
    "time_window": {"unit": "week", "count": 4},
}
DRAFT = {
    "schedule": "0 7 * * 1",
    "recipient": "sales@example.com",
    "operator": ">=",
    "threshold": 10,
    "severity": "high",
    "guardrail_amount": 5000,
    "candidates": "stalled deals",
    "approver": "sales_lead",
    "write_target": "crm.deal.stage",
    "timeout_behaviour": "hold",
    "per_run_cap": 20,
    "rollback": "snapshot",
}


def compiled(shape, *, executors=FULL, draft=DRAFT, template=None, spec=None):
    return compile_workflow(
        spec or SPEC,
        registered_executors=executors,
        shape=shape,
        draft=draft,
        template=template,
    )


# --- the shape table ------------------------------------------------------------------


def test_all_four_shapes_have_a_sequence_and_a_parameter_list():
    """Four shapes, not three. R9's AutonomyShape omits Alert and cannot express one."""
    assert set(SHAPE_STAGES) == set(UseCaseShape)
    assert set(SHAPE_PARAMETERS) == set(UseCaseShape)
    for shape in UseCaseShape:
        assert SHAPE_STAGES[shape], f"{shape} has no stages"
        assert SHAPE_PARAMETERS[shape], f"{shape} has no parameters"


def test_alert_carries_no_model_step():
    """An alert is arithmetic end to end: read, compare, write, notify.

    Worth asserting rather than assuming. A model step inside an Alert would be a model
    deciding whether a threshold fired, which is the one thing the build plan says a branch
    must never be.
    """
    kinds = [stage.kind for stage in SHAPE_STAGES[UseCaseShape.ALERT]]
    assert StageKind.MODEL not in kinds
    assert StageKind.BRANCH in kinds


def test_only_recommend_has_an_approval_gate():
    """Inform and Alert write nothing; Act writes without waiting. Recommend is the one
    shape whose definition is that a person decides first."""
    with_gates = {
        shape
        for shape, stages in SHAPE_STAGES.items()
        if any(stage.kind is StageKind.GATE for stage in stages)
    }
    assert with_gates == {UseCaseShape.RECOMMEND}


def test_the_write_shapes_carry_their_safety_stages():
    """Act's per-run cap and rollback point are in its sequence, not optional extras."""
    act = {stage.name for stage in SHAPE_STAGES[UseCaseShape.ACT]}
    assert {"Apply hard limits", "Apply per-run cap", "Record rollback point"} <= act
    recommend = {stage.name for stage in SHAPE_STAGES[UseCaseShape.RECOMMEND]}
    assert "Apply per-run cap" not in recommend  # a person decides instead


# --- compiling ------------------------------------------------------------------------


def test_the_shape_comes_from_the_spec_and_is_never_inferred():
    with pytest.raises(UnknownShape, match="names no shape"):
        compile_workflow({"use_case_id": "x"}, registered_executors=FULL)
    with pytest.raises(UnknownShape, match="not one of the four"):
        compile_workflow({"shape": "orchestrate"}, registered_executors=FULL)


def test_the_spec_can_carry_the_shape_itself():
    report = compile_workflow(
        {**SPEC, "shape": "alert"}, registered_executors=FULL, draft=DRAFT
    )
    assert report.shape is UseCaseShape.ALERT


def test_each_shape_compiles_to_its_own_sequence():
    """The sequences must actually differ, or the shape is decorative."""
    sequences = {
        shape: [s["name"] for s in normalise_config(compiled(shape).config)]
        for shape in UseCaseShape
    }
    assert len({tuple(v) for v in sequences.values()}) == 4
    assert sequences[UseCaseShape.INFORM][0] == "Read gold"
    assert "Write to target" in sequences[UseCaseShape.ACT]
    assert "Write to target" not in sequences[UseCaseShape.INFORM]


def test_a_fully_configured_deployment_publishes_every_shape():
    for shape in UseCaseShape:
        report = compiled(shape)
        assert report.publishable, f"{shape}: {report.refusal_reasons}"


def test_a_stage_with_no_executor_blocks_publication():
    without = FULL - {"compare_to_baseline"}
    report = compiled(UseCaseShape.INFORM, executors=without)
    assert report.publishable is False
    assert report.missing_capabilities == {"Compare to baseline": "compare_to_baseline"}
    assert "does not register" in report.notes


def test_a_missing_approval_gate_emits_no_steps_at_all():
    """The one shortfall where a partial workflow is dangerous rather than incomplete:
    every step after the gate still runs, with the approver compiled out."""
    without = FULL - {"await_approval"}
    report = compiled(UseCaseShape.RECOMMEND, executors=without)
    assert report.config["steps"] == []
    assert "unapproved" in report.notes


def test_a_missing_non_gate_stage_still_emits_what_it_could_build():
    """A reviewer needs to see the sequence to judge what is missing from it."""
    report = compiled(UseCaseShape.INFORM, executors=FULL - {"compare_to_baseline"})
    assert [s["name"] for s in normalise_config(report.config)] == [
        "Read gold",
        "Explain",
        "Record provenance",
        "Deliver",
    ]


def test_the_approval_gate_is_a_confirmation_not_a_comment():
    steps = normalise_config(compiled(UseCaseShape.RECOMMEND).config)
    gates = [s for s in steps if s["gate"]]
    assert [g["name"] for g in gates] == ["Approver decides"]


def test_compilation_is_reproducible():
    """Two compilations of one spec must be identical, or the diff this package is verified
    by would never be clean. Step ids are derived, not random."""
    assert compiled(UseCaseShape.ACT).config == compiled(UseCaseShape.ACT).config


def test_compute_metrics_is_recorded_as_satisfied_not_missing():
    """Gold already holds the materialised value, so the plan's separate compute stage has no
    executor and needs none. Recorded, because a table implemented verbatim should say where
    it deviates."""
    report = compiled(UseCaseShape.INFORM)
    assert report.satisfied_elsewhere == {"Compute metrics": "read_gold"}
    assert "Compute metrics" not in report.missing_capabilities


# --- parameters and templates ---------------------------------------------------------


def test_every_slot_the_shape_declares_is_bound_or_reported():
    for shape in UseCaseShape:
        report = compiled(shape)
        assert set(report.parameters) | set(report.unfilled_slots) == set(
            SHAPE_PARAMETERS[shape]
        )


def test_an_unfilled_slot_blocks_publication():
    """An Act with no per-run cap bound is an unattended writer with no bound on how much it
    writes."""
    report = compiled(UseCaseShape.ACT, draft={**DRAFT, "per_run_cap": None})
    assert report.publishable is False
    assert "per-run cap" in report.unfilled_slots


def test_a_template_fills_slots_the_author_left_empty():
    template = {
        "id": "tmpl_a",
        "parameters": {"per-run cap": 20, "rollback": "snapshot"},
    }
    report = compiled(
        UseCaseShape.ACT,
        draft={**DRAFT, "per_run_cap": None, "rollback": None},
        template=template,
    )
    assert report.template_id == "tmpl_a"
    assert report.parameters["per-run cap"] == 20
    assert report.parameter_origins["per-run cap"] is ParameterOrigin.TEMPLATE


def test_an_authored_value_beats_the_template_default():
    """Otherwise editing a prefilled field would do nothing."""
    template = {"id": "tmpl_a", "parameters": {"per-run cap": 20}}
    report = compiled(
        UseCaseShape.ACT, draft={**DRAFT, "per_run_cap": 5}, template=template
    )
    assert report.parameters["per-run cap"] == 5
    assert report.parameter_origins["per-run cap"] is ParameterOrigin.AUTHORED


def test_accepting_the_shipped_value_is_recorded_as_template_not_authored():
    """The distinction the activation screen needs: whether the approver of an Act chose its
    per-run cap or accepted the number a template shipped with."""
    template = {"id": "tmpl_a", "parameters": {"per-run cap": 20}}
    report = compiled(
        UseCaseShape.ACT, draft={**DRAFT, "per_run_cap": 20}, template=template
    )
    assert report.parameter_origins["per-run cap"] is ParameterOrigin.TEMPLATE


def test_spec_values_are_marked_as_coming_from_the_spec():
    report = compiled(UseCaseShape.INFORM)
    assert report.parameter_origins["metrics"] is ParameterOrigin.SPEC
    assert report.parameters["metrics"] == ["quoterequest__count"]


def test_a_template_cannot_add_or_reorder_steps():
    """A template that could change the sequence would be a fifth shape wearing another name,
    and every guarantee resting on the four sequences would stop holding."""
    template = {
        "id": "tmpl_a",
        "parameters": {"schedule": "@daily"},
        "steps": [{"name": "Exfiltrate", "executor_ref": "read_gold"}],
    }
    with_template = compiled(UseCaseShape.INFORM, template=template)
    without = compiled(UseCaseShape.INFORM)
    assert normalise_config(with_template.config) == normalise_config(without.config)


# --- the definition is real, and contains only what exists ----------------------------


def test_the_emitted_step_matches_the_frameworks_own_serialisation():
    """``_step_dict`` hand-builds what ``Step.to_dict()`` produces, to avoid constructing an
    Agent per model step. This is what stops the two drifting apart silently."""
    from egpt.agent import Agent
    from egpt.workflow import Step, Workflow

    from egpt_retail.executors.gold import read_gold

    workflow = Workflow(
        id="w",
        name="W",
        steps=[
            Step(name="Read gold", executor=read_gold),
            Step(
                name="Explain",
                agent=Agent(id="a__explanation", name="x"),
                description="d",
            ),
        ],
    )
    theirs = [s.to_dict() for s in workflow.steps]
    mine = [
        _step_dict(name="Read gold", step_id="ignored", executor_ref="read_gold"),
        _step_dict(
            name="Explain",
            step_id="ignored",
            agent_id="a__explanation",
            description="d",
        ),
    ]
    for their_step, my_step in zip(theirs, mine):
        their_step.pop("step_id")
        my_step.pop("step_id")
        assert their_step == my_step


def test_every_executor_step_survives_r9s_plan_walker():
    """R9's ``plan_from_config`` raises on any executor the registry does not resolve. It is
    written by another lane against a contract I do not control, so it is a real independent
    check that the compiler composes only what exists.

    Checked on Alert, the one shape whose sequence is executors end to end. The shapes with a
    model step cannot reach the walker yet -- see the two tests below, which is a finding
    rather than a gap in this one.
    """
    report = compiled(UseCaseShape.ALERT)
    assert report.model_steps == []
    plan = plan_from_config(
        report.config,
        use_case_id=report.use_case_id,
        version=1,
        workflow_component_id=None,
        executor_names=FULL,
    )
    assert len(plan["steps"]) == len(normalise_config(report.config))


def test_a_generated_model_step_cannot_be_planned_until_r8_has_a_row():
    """The finding K15 surfaced, recorded as a test so it cannot be forgotten.

    R8's ``ROUTING_TABLE`` is a static literal keyed by agent id, and K15 mints an agent id
    per use case, so no hand-written table can contain one. R9's walker refuses the step --
    correctly, an unrouted step has no model -- which means a generated Inform, Recommend or
    Act is unplannable until activation can register a routing row. That path is Lane R's and
    does not exist (K-9). This test passes today by asserting the refusal, and will fail the
    day the gap closes, which is exactly when it should be revisited.
    """
    report = compiled(UseCaseShape.INFORM)
    assert report.model_steps, "the Inform shape should carry an explanation step"
    with pytest.raises(UnknownPlanRef, match="routing table"):
        plan_from_config(
            report.config,
            use_case_id=report.use_case_id,
            version=1,
            workflow_component_id=None,
            executor_names=FULL,
            agent_ids=[m["agent_id"] for m in report.model_steps],
        )


def test_the_compiler_names_the_routing_row_each_model_step_needs():
    """So closing K-9 is a lookup rather than a judgement: the tier is already decided."""
    inform = compiled(UseCaseShape.INFORM)
    assert inform.required_routing_rows == {"weekly_pipeline__explanation": "small"}
    act = compiled(UseCaseShape.ACT)
    assert act.required_routing_rows == {"weekly_pipeline__proposal": "frontier"}
    assert compiled(UseCaseShape.ALERT).required_routing_rows == {}


def test_the_walker_rejects_a_definition_naming_an_unregistered_executor():
    """Proves the test above is not vacuous: the walker really does raise."""
    report = compiled(UseCaseShape.INFORM)
    with pytest.raises(UnknownPlanRef):
        plan_from_config(
            report.config,
            use_case_id="x",
            version=1,
            workflow_component_id=None,
            executor_names=FULL - {"read_gold"},
            agent_ids=[m["agent_id"] for m in report.model_steps],
        )


def test_every_ref_in_the_config_is_an_executor_or_a_declared_model_step():
    report = compiled(UseCaseShape.RECOMMEND)
    declared = {m["agent_id"] for m in report.model_steps}
    for ref in plan_refs(report.config):
        assert ref in FULL or ref in declared


def test_model_steps_are_declared_for_the_prompt_skill_to_author():
    report = compiled(UseCaseShape.ACT)
    assert [m["role"] for m in report.model_steps] == ["proposal"]
    assert report.model_steps[0]["agent_id"] == "weekly_pipeline__proposal"


# --- the capability report ------------------------------------------------------------


def test_capability_report_names_what_each_shape_still_needs():
    bare = frozenset({"read_gold", "record_provenance", "deliver_in_app"})
    report = capability_report(bare)
    assert set(report) == {s.value for s in UseCaseShape}
    assert "Compare to baseline" in report["inform"]["missing"]
    assert "Write to target" in report["act"]["missing"]
    assert report["inform"]["satisfied_elsewhere"] == {"Compute metrics": "read_gold"}


def test_capability_report_is_empty_of_gaps_on_a_full_deployment():
    report = capability_report(FULL)
    assert all(not shape["missing"] for shape in report.values())


# --- diffing --------------------------------------------------------------------------


def test_diff_reports_a_difference_and_is_empty_when_there_is_none():
    report = compiled(UseCaseShape.INFORM)
    assert diff_against(report, report.config) == []
    shortened = {"steps": report.config["steps"][:2]}
    lines = diff_against(report, shortened)
    assert lines and "only generated" in lines[0]


# --- async ----------------------------------------------------------------------------


def test_async_compile_matches_the_sync_one():
    got = asyncio.run(
        acompile_workflow(
            SPEC, registered_executors=FULL, shape=UseCaseShape.ACT, draft=DRAFT
        )
    )
    assert got.config == compiled(UseCaseShape.ACT).config


# --- the step-prompt skill ------------------------------------------------------------


def draft_prompt(**overrides):
    fields = {
        "identity": "You explain how the numbers moved.",
        "inputs": [sp.InputRow(name="rows", source="read_gold", required=True)],
        "phases": ["Read every row.", "Emit the schema."],
        "specific_rules": ["Never state a percentage change against a zero prior."],
        "output_notes": "One paragraph per KPI.",
        "worked_example": "Given two rows, say which way it moved.",
        "near_miss": "Judging whether the move is significant.",
        "near_miss_reason": "Significance is a threshold, and thresholds belong to Alert.",
        "refusals": ["No rows: return empty and say gold held nothing."],
    }
    fields.update(overrides)
    return sp.StepPromptDraft(**fields)


def authored(**overrides):
    return sp.assemble(
        draft_prompt(**overrides),
        agent_id="wk__explanation",
        role="explanation",
        output_schema="x.Y",
        use_case_id="wk",
        kpi_identifiers=["quoterequest__count"],
    )


def test_the_four_standing_rules_appear_verbatim():
    """K14 requires them copied word for word so seven prompts can be reviewed against each
    other. Code writes them, so an authoring agent cannot paraphrase one."""
    assert sp.STANDING_RULES in authored().markdown


def test_the_draft_has_no_field_for_the_parts_code_owns():
    fields = set(sp.StepPromptDraft.model_fields)
    assert not fields & {
        "header",
        "model_tier",
        "standing_rules",
        "identifier",
        "writes",
    }


def test_an_unexpected_key_is_refused_not_ignored():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        sp.StepPromptDraft.model_validate(
            {**draft_prompt().model_dump(), "model_tier": "frontier"}
        )


def test_the_tier_follows_from_the_role():
    """G6 fixes explanation on the small tier and suggestion on the frontier tier. An agent
    that could choose its own tier would choose frontier."""
    assert authored().model_tier == "small"
    proposal = sp.assemble(
        draft_prompt(),
        agent_id="wk__proposal",
        role="proposal",
        output_schema="x.Y",
        use_case_id="wk",
    )
    assert proposal.model_tier == "frontier"


def test_an_unruled_role_refuses_rather_than_guessing_a_tier():
    got = sp.assemble(
        draft_prompt(),
        agent_id="a",
        role="triage",
        output_schema="x.Y",
        use_case_id="wk",
    )
    assert got.publishable is False
    assert any("model_tier" in problem for problem in got.problems)


def test_all_eight_sections_are_present_and_in_order():
    assert sp.missing_sections(authored().markdown) == []


def test_missing_sections_actually_detects_an_absent_one():
    """Proves the test above is not vacuous."""
    broken = authored().markdown.replace("## 7. Worked example", "## Worked example")
    assert any(
        "7. Worked example" in problem for problem in sp.missing_sections(broken)
    )


def test_an_empty_section_is_caught_as_well_as_an_absent_one():
    assert sp.missing_sections("## 1. Header\n\n## 2. Identity\n") != []


def test_a_draft_with_no_refusal_case_is_not_publishable():
    got = authored(refusals=[])
    assert got.publishable is False
    assert any("refusal" in problem for problem in got.problems)


def test_a_draft_naming_no_input_is_not_publishable():
    got = authored(inputs=[])
    assert got.publishable is False
    assert any("traceable" in problem for problem in got.problems)


def test_render_fills_every_placeholder():
    rendered = sp.render_prompt(
        role="explanation", use_case_id="wk", question="q", kpi_identifiers=["a__b"]
    )
    assert "{{" not in rendered
    assert "a__b" in rendered


def test_render_refuses_a_prompt_that_lost_a_placeholder(tmp_path):
    stripped = tmp_path / "p.md"
    stripped.write_text(sp.PROMPT_PATH.read_text().replace("{{role}}", "explanation"))
    with pytest.raises(ValueError, match="role"):
        sp.render_prompt(
            role="explanation", use_case_id="wk", question="q", prompt_path=stripped
        )


def test_async_step_prompt_variants_match():
    got = asyncio.run(
        sp.aassemble(
            draft_prompt(),
            agent_id="wk__explanation",
            role="explanation",
            output_schema="x.Y",
            use_case_id="wk",
            kpi_identifiers=["quoterequest__count"],
        )
    )
    assert got.markdown == authored().markdown
    assert asyncio.run(
        sp.arender_prompt(role="explanation", use_case_id="wk", question="q")
    ) == sp.render_prompt(role="explanation", use_case_id="wk", question="q")
