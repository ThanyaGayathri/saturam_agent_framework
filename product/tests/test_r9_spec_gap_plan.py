"""R9 without a model or a service -- the spec's closed sets, the gap check's classification
over the contract fixture, the plan walker over every step shape, and the refusals.

The extraction itself needs a model and is exercised by cookbook/94_retail/R09_spec_gap_plan.py;
everything deterministic around it is pinned here.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest
from egpt.agent import Agent
from egpt.workflow import Workflow
from egpt.workflow.condition import Condition
from egpt.workflow.router import Router
from egpt.workflow.step import Step
from egpt.workflow.types import StepInput

from egpt_retail.api.models._generated import GapCheck, Plan, Spec
from egpt_retail.app.components import build_registry
from egpt_retail.contracts.fixture_builder import CONTRACT_FIXTURE_PATH
from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.contracts.semantic_stub import StubSemanticRead
from egpt_retail.contracts.vocabulary import load_vocabulary_set
from egpt_retail.executors.delivery import deliver_in_app
from egpt_retail.executors.gaps import (
    SHAPE_PARAMETERS,
    AutonomyShape,
    assess_gaps,
    check_spec_gaps,
    shape_for,
)
from egpt_retail.executors.gold import read_gold
from egpt_retail.executors.metadata import EXECUTOR_METADATA
from egpt_retail.executors.plan import (
    UnknownPlanRef,
    derive_plan,
    plan_from_config,
    plan_refs,
)
from egpt_retail.executors.preflight import check_preflight
from egpt_retail.executors.provenance import record_provenance
from egpt_retail.executors.registry import check_registry
from egpt_retail.routing import ROUTING_TABLE
from egpt_retail.skills.spec_extractor import (
    SKILL_IDENTIFIER,
    Delivery,
    SpecRole,
    TimeUnit,
    UseCaseSpec,
    build_instructions,
    closed_sets_from,
    closed_sets_match_prompt,
    render_closed_sets,
    spec_prompt,
    validate_spec,
)

_REGISTRY_DOC = (
    Path(__file__).resolve().parents[1]
    / "egpt_retail"
    / "contracts"
    / "EXECUTOR_REGISTRY.md"
)
NOW = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def reader() -> StubSemanticRead:
    return StubSemanticRead.from_fixture(CONTRACT_FIXTURE_PATH)


def _kpi(
    identifier: str, sql: str, tables: set, grain: str = "supplier"
) -> KpiDefinition:
    return KpiDefinition(
        identifier=identifier,
        name=identifier,
        description="fixture",
        sql=sql,
        grain=grain,
        business_area="supply_chain",
        source_tables=frozenset(tables),
    )


@pytest.fixture(scope="module")
def kpis() -> dict:
    return {
        k.identifier: k
        for k in (
            _kpi(
                "supplier__on_time_rate_30d",
                "SELECT count(*) FROM supplier_deliveries",
                {"supplier_deliveries"},
            ),
            _kpi(
                "product__avg_rating",
                "SELECT avg(r) FROM product_reviews JOIN web_sessions ON true",
                {"product_reviews", "web_sessions"},
                grain="product",
            ),
            _kpi(
                "order__ghost",
                "SELECT count(*) FROM shipments JOIN carriers ON true",
                {"shipments", "carriers"},
                grain="order",
            ),
            _kpi(
                "review__per_delivery",
                "SELECT count(*) FROM product_reviews JOIN supplier_deliveries ON true",
                {"product_reviews", "supplier_deliveries"},
                grain="product",
            ),
        )
    }


# ---------------------------------------------------------------------------
# The spec and its closed sets
# ---------------------------------------------------------------------------


def test_the_enums_are_enumerated_in_full_in_the_prompt():
    assert closed_sets_match_prompt() == {"role": True, "unit": True, "delivery": True}


def test_the_enums_are_the_contracts():
    assert {m.value for m in SpecRole} == {"subject", "filter", "measure", "group_by"}
    assert {m.value for m in TimeUnit} == {"day", "week", "month", "quarter", "year"}
    assert {m.value for m in Delivery} == {"in_app", "email", "slack"}


def test_no_enum_field_carries_a_description():
    """The template's section 6 constraint: a `$ref` with sibling keywords is a 400 from Azure."""
    schema = UseCaseSpec.model_json_schema()
    for name in ("delivery",):
        assert "description" not in schema["properties"][name]
    assert "description" not in schema["$defs"]["SpecEntityDraft"]["properties"]["role"]
    assert "description" not in schema["$defs"]["TimeWindowDraft"]["properties"]["unit"]


def test_closed_sets_are_rendered_from_where_they_live(reader, kpis):
    sets = closed_sets_from(reader, kpis.values(), load_vocabulary_set())
    assert "supply_chain" in sets.business_areas and "supplier" in sets.grains
    assert (
        "Supplier" in sets.concepts
        and "supplier__on_time_rate_30d" in sets.kpi_identifiers
    )
    rendered = render_closed_sets(sets)
    for value in (
        "`supply_chain`",
        "`supplier`",
        "`Supplier`",
        "`supplier__on_time_rate_30d`",
    ):
        assert value in rendered
    instructions = build_instructions(sets)
    assert (
        instructions.startswith("# Skill — spec extractor") and rendered in instructions
    )


def test_validate_spec_names_every_coined_value(reader, kpis):
    sets = closed_sets_from(reader, kpis.values(), load_vocabulary_set())
    spec = UseCaseSpec(
        use_case_id="uc_0002",
        question="q",
        business_area="logistics",
        grain="warehouse",
        entities=[{"concept": "Carrier", "role": "subject"}],
        kpi_identifiers=["supplier__avg_basket_size"],
        semantic_snapshot="r3-contract.1",
    )
    problems = validate_spec(spec, sets)
    assert len(problems) == 4
    assert any("logistics" in p for p in problems) and any(
        "Carrier" in p for p in problems
    )
    clean = UseCaseSpec(
        use_case_id="uc_0002",
        question="q",
        business_area="supply_chain",
        grain="supplier",
        entities=[{"concept": "Supplier", "role": "subject"}],
        kpi_identifiers=["supplier__on_time_rate_30d"],
        semantic_snapshot="r3-contract.1",
    )
    assert validate_spec(clean, sets) == ()


def test_the_spec_validates_against_the_contract_with_questions():
    spec = UseCaseSpec(
        use_case_id="uc_0002",
        question="q",
        business_area="supply_chain",
        grain="supplier",
        questions=["which KPI?"],
        semantic_snapshot="r3-contract.1",
    )
    payload = spec.to_contract()
    assert payload["extracted_by"] == {
        "identifier": SKILL_IDENTIFIER,
        "version": "1.0.0",
    }
    validated = Spec.model_validate(payload)
    assert validated.questions == ["which KPI?"]


def test_spec_prompt_declares_every_input():
    prompt = spec_prompt(
        "uc_0002", "Which suppliers slip?", "r3-contract.1", ["and why?"]
    )
    assert (
        "use_case_id: uc_0002" in prompt
        and 'snapshot: {"version": "r3-contract.1"}' in prompt
    )
    assert "supporting_questions:" in prompt and "- and why?" in prompt


# ---------------------------------------------------------------------------
# The gap check
# ---------------------------------------------------------------------------


def _gaps(result, kind):
    return [g for g in result["gaps"] if g["kind"] == kind]


def test_a_clean_inform_spec_is_feasible(reader, kpis):
    spec = {
        "use_case_id": "uc_0002",
        "business_area": "supply_chain",
        "grain": "supplier",
        "entities": [{"concept": "Supplier", "role": "subject"}],
        "kpi_identifiers": ["supplier__on_time_rate_30d"],
        "time_window": {"length": 30, "unit": "day"},
    }
    draft = {"step8": {"schedule": "daily", "recipient": "ops@example.com"}}
    result = assess_gaps(spec, reader=reader, kpis=kpis, draft=draft, level=1, now=NOW)
    GapCheck.model_validate(result)
    assert result["feasible"] is True and result["gaps"] == []
    assert (
        result["semantic_snapshot"] == "r3-contract.1"
        and result["checked_at"] == "2026-09-09T09:00:00Z"
    )


def test_every_data_gap_kind_is_reachable_and_named_for_the_customer(reader, kpis):
    spec = {
        "use_case_id": "uc_0003",
        "business_area": "logistics",
        "grain": "product",
        "entities": [
            {"concept": "Carrier", "role": "subject"},
            {"concept": "Product", "role": "group_by"},
        ],
        "kpi_identifiers": [
            "product__avg_rating",
            "order__ghost",
            "review__per_delivery",
            "nobody__knows",
        ],
        "time_window": {"length": 1, "unit": "month"},
    }
    result = assess_gaps(
        spec,
        reader=reader,
        kpis=kpis,
        draft={"step8": {"schedule": "daily", "recipient": "x"}},
        level=1,
        now=NOW,
    )
    GapCheck.model_validate(result)
    assert result["feasible"] is False
    partial = _gaps(result, "partial_kpi")
    assert [g["subject"] for g in partial] == [
        "product__avg_rating"
    ] and "web_sessions" in partial[0]["detail"]
    blocked = _gaps(result, "blocked_kpi")
    assert {g["subject"] for g in blocked} == {"order__ghost", "nobody__knows"}
    assert {g["subject"] for g in _gaps(result, "missing_table")} == {
        "web_sessions",
        "shipments",
        "carriers",
    }
    assert [g["subject"] for g in _gaps(result, "unknown_concept")] == ["Carrier"]
    assert [g["subject"] for g in _gaps(result, "unsupported_area")] == ["logistics"]
    joins = _gaps(result, "no_join_path")
    assert joins == [] or all("->" in g["subject"] for g in joins)
    assert _gaps(result, "missing_parameter") == []


def test_a_coined_grain_is_a_gap_only_when_the_vocabulary_is_given(reader, kpis):
    """`grain` is otherwise the one closed-set spec field with no ground-truth re-check --
    Round-2 review, PR #203. `grains=None` (the default) must not reject every spec's grain
    just because a caller has not wired the vocabulary through yet."""
    sets = closed_sets_from(reader, kpis.values(), load_vocabulary_set())
    assert "warehouse" not in sets.grains and "supplier" in sets.grains
    spec = {
        "use_case_id": "uc_0004",
        "business_area": "supply_chain",
        "grain": "warehouse",
        "entities": [],
        "kpi_identifiers": ["supplier__on_time_rate_30d"],
        "time_window": {"length": 1, "unit": "day"},
    }
    draft = {"step8": {"schedule": "daily", "recipient": "x"}}

    unchecked = assess_gaps(
        spec, reader=reader, kpis=kpis, draft=draft, level=1, now=NOW
    )
    assert _gaps(unchecked, "unknown_grain") == []

    checked = assess_gaps(
        spec,
        reader=reader,
        kpis=kpis,
        draft=draft,
        level=1,
        now=NOW,
        grains=sets.grains,
    )
    GapCheck.model_validate(checked)
    assert [g["subject"] for g in _gaps(checked, "unknown_grain")] == ["warehouse"]

    clean = assess_gaps(
        dict(spec, grain="supplier"),
        reader=reader,
        kpis=kpis,
        draft=draft,
        level=1,
        now=NOW,
        grains=sets.grains,
    )
    assert _gaps(clean, "unknown_grain") == []


def test_no_join_path_is_reported_between_present_source_tables(reader, kpis):
    """product_reviews and supplier_deliveries are both in the fixture and the contract's own
    example says the graph holds no join between them."""
    spec = {
        "use_case_id": "uc",
        "business_area": "product",
        "grain": "product",
        "entities": [],
        "kpi_identifiers": ["review__per_delivery"],
        "time_window": {"length": 1, "unit": "day"},
    }
    result = assess_gaps(
        spec,
        reader=reader,
        kpis=kpis,
        draft={"step8": {"schedule": "daily", "recipient": "x"}},
        level=1,
        now=NOW,
    )
    subjects = [g["subject"] for g in _gaps(result, "no_join_path")]
    assert subjects == ["product_reviews -> supplier_deliveries"]


def test_the_autonomy_shapes_parameters_are_the_build_plans():
    assert SHAPE_PARAMETERS[AutonomyShape.INFORM] == (
        "metrics",
        "dimensions",
        "comparison",
        "schedule",
        "recipient",
    )
    assert shape_for(None, None) is AutonomyShape.INFORM
    assert shape_for("autopilot", 2) is AutonomyShape.INFORM
    assert shape_for("copilot", 3) is AutonomyShape.RECOMMEND
    assert shape_for("autopilot", 4) is AutonomyShape.ACT


def test_missing_parameters_are_gaps_for_the_chosen_shape(reader, kpis):
    spec = {
        "use_case_id": "uc",
        "business_area": "supply_chain",
        "grain": "supplier",
        "entities": [],
        "kpi_identifiers": ["supplier__on_time_rate_30d"],
    }
    inform = assess_gaps(spec, reader=reader, kpis=kpis, draft={}, level=1, now=NOW)
    assert [g["subject"] for g in _gaps(inform, "missing_parameter")] == [
        "comparison",
        "schedule",
        "recipient",
    ]
    assert "spec.time_window" in _gaps(inform, "missing_parameter")[0]["detail"]
    act = assess_gaps(
        spec,
        reader=reader,
        kpis=kpis,
        draft={"step8": {"schedule": "daily", "guardrail_amount": 100}},
        autonomy="autopilot",
        level=4,
        now=NOW,
    )
    subjects = [g["subject"] for g in _gaps(act, "missing_parameter")]
    assert subjects == ["candidates", "write target", "per-run cap", "rollback"]
    assert all("P4" in g["detail"] for g in _gaps(act, "missing_parameter"))
    GapCheck.model_validate(act)


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def _inform_config(db=None):
    """An Inform-shape skeleton stored the way the framework stores it: Workflow.to_dict()."""
    steps = [
        Step(name="Preflight", executor=check_preflight),
        Step(name="Read the KPIs", executor=read_gold),
        Step(
            name="Explain",
            agent=Agent(id="funnel_explanation", name="funnel_explanation"),
            description="One model step.",
        ),
        Step(name="Record provenance", executor=record_provenance),
        Step(name="Deliver", executor=deliver_in_app),
    ]
    workflow = Workflow(id="wf-inform", name="Inform skeleton", steps=steps, db=db)
    return {"steps": [s.to_dict() for s in workflow.steps]}


def _names():
    return {f.__name__ for f in build_registry().functions}


def test_the_plan_is_every_step_of_the_workflow_in_order_and_validates():
    plan = plan_from_config(
        _inform_config(),
        use_case_id="uc_0001",
        version=3,
        workflow_component_id="cmp_wf_0001",
        executor_names=_names(),
        compiled_at=NOW,
    )
    Plan.model_validate(plan)
    assert (
        [s["ref"] for s in plan["steps"]]
        == plan_refs(_inform_config())
        == [
            "check_preflight",
            "read_gold",
            "funnel_explanation",
            "record_provenance",
            "deliver_in_app",
        ]
    )
    assert [s["index"] for s in plan["steps"]] == [1, 2, 3, 4, 5]
    preflight, explain = plan["steps"][0], plan["steps"][2]
    assert (
        preflight["kind"] == "executor"
        and preflight["permits_or_blocks"] is True
        and preflight["model_tier"] is None
    )
    assert (
        explain["kind"] == "agent"
        and explain["model_tier"] == "small"
        and explain["permits_or_blocks"] is False
    )
    assert preflight["inputs"] == ["spec"] and preflight["outputs"] == [
        "preflight_result"
    ]
    assert plan["compiled_at"] == "2026-09-09T09:00:00Z"


def test_the_walker_reaches_else_steps_and_router_choices():
    config = _inform_config()
    branch = Condition(
        name="fresh?",
        evaluator=lambda _: True,
        steps=[Step(name="Read", executor=read_gold)],
        else_steps=[
            Step(
                name="Materialise first",
                executor=__import__(
                    "egpt_retail.executors.gold", fromlist=["materialise_gold"]
                ).materialise_gold,
            )
        ],
    )
    router = Router(
        name="channel",
        selector=lambda _: [],
        choices=[Step(name="Deliver", executor=deliver_in_app)],
    )
    config["steps"] = [branch.to_dict(), router.to_dict(), *config["steps"][2:]]
    refs = plan_refs(config)
    assert refs == [
        "read_gold",
        "materialise_gold",
        "deliver_in_app",
        "funnel_explanation",
        "record_provenance",
        "deliver_in_app",
    ]
    plan = plan_from_config(
        config,
        use_case_id="uc",
        version=1,
        workflow_component_id=None,
        executor_names=_names(),
        compiled_at=NOW,
    )
    assert [s["ref"] for s in plan["steps"]] == refs
    Plan.model_validate(plan)


def test_dropping_a_branch_is_visible_in_the_plan():
    """Mutation, as a test: a walker that skipped else_steps would still produce a valid Plan."""
    config = _inform_config()
    branch = Condition(
        name="fresh?",
        evaluator=lambda _: True,
        steps=[Step(name="Read", executor=read_gold)],
        else_steps=[Step(name="Preflight again", executor=check_preflight)],
    )
    config["steps"] = [branch.to_dict()]
    assert plan_refs(config) == ["read_gold", "check_preflight"]
    del config["steps"][0]["else_steps"]
    assert plan_refs(config) == ["read_gold"]


def test_an_unregistered_executor_is_refused_by_name():
    config = _inform_config()
    config["steps"][1]["executor_ref"] = "sample_rows"  # R10's, not registered yet
    with pytest.raises(UnknownPlanRef, match="sample_rows"):
        plan_from_config(
            config,
            use_case_id="uc",
            version=1,
            workflow_component_id=None,
            executor_names=_names(),
        )


def test_an_unrouted_agent_is_refused_by_name():
    config = _inform_config()
    config["steps"][2]["agent_id"] = "mystery_agent"
    with pytest.raises(UnknownPlanRef, match="mystery_agent"):
        plan_from_config(
            config,
            use_case_id="uc",
            version=1,
            workflow_component_id=None,
            executor_names=_names(),
            agent_ids={"mystery_agent"},
        )


def test_a_step_with_neither_ref_is_refused():
    config = {"steps": [{"type": "Step", "name": "inline"}]}
    with pytest.raises(UnknownPlanRef, match="neither"):
        plan_from_config(
            config,
            use_case_id="uc",
            version=1,
            workflow_component_id=None,
            executor_names=_names(),
        )


def test_derive_plan_reuses_a_threaded_db_and_agent_ids(monkeypatch):
    """Round-2 review, PR #203: `derive_plan` rebuilt the whole agent set (and opened a
    fresh control-plane db) on every call just to read two static ids. When a caller
    threads them through `additional_data` -- the startup-built db/agents, once a caller
    exists to do it -- neither must be rebuilt."""
    import egpt.registry.hydrate as hydrate_module

    import egpt_retail.app.components as components_module
    import egpt_retail.app.db as db_module

    config = _inform_config()

    def _fake_fetch_config_row(db, component_id, component_type, **kwargs):
        assert db == "sentinel-db"  # the threaded db, not a freshly built one
        return {"config": config, "version": 1}

    def _must_not_run(*args, **kwargs):
        raise AssertionError(
            "build_agents/build_control_plane_db must not run when threaded through"
        )

    monkeypatch.setattr(hydrate_module, "fetch_config_row", _fake_fetch_config_row)
    monkeypatch.setattr(components_module, "build_agents", _must_not_run)
    monkeypatch.setattr(db_module, "build_control_plane_db", _must_not_run)

    output = asyncio.run(
        derive_plan(
            StepInput(
                additional_data={
                    "workflow_component_id": "cmp_x",
                    "use_case_id": "uc_x",
                    "control_plane_db": "sentinel-db",
                    "agent_ids": {"funnel_explanation"},
                }
            )
        )
    )
    assert output.success is True
    assert [s["ref"] for s in output.content["steps"]] == plan_refs(config)


def test_derive_plan_falls_back_to_building_when_nothing_is_threaded(monkeypatch):
    """The other half of the fix above: a caller that has not wired the startup-built
    db/agents through yet (there is no such caller until P5) still gets a working view --
    the fallback builds them itself."""
    import egpt.registry.hydrate as hydrate_module

    import egpt_retail.app.components as components_module
    import egpt_retail.app.db as db_module

    config = _inform_config()
    built_agents = [Agent(id="funnel_explanation", name="funnel_explanation")]
    calls = {"build_agents": 0, "build_control_plane_db": 0}

    def _fake_fetch_config_row(db, component_id, component_type, **kwargs):
        assert db == "built-db"
        return {"config": config, "version": 1}

    def _fake_build_control_plane_db(settings):
        calls["build_control_plane_db"] += 1
        return "built-db"

    def _fake_build_agents(settings, db):
        calls["build_agents"] += 1
        assert db == "built-db"
        return built_agents

    monkeypatch.setattr(hydrate_module, "fetch_config_row", _fake_fetch_config_row)
    monkeypatch.setattr(
        db_module, "build_control_plane_db", _fake_build_control_plane_db
    )
    monkeypatch.setattr(components_module, "build_agents", _fake_build_agents)

    output = asyncio.run(
        derive_plan(
            StepInput(
                additional_data={
                    "workflow_component_id": "cmp_x",
                    "use_case_id": "uc_x",
                }
            )
        )
    )
    assert output.success is True
    assert [s["ref"] for s in output.content["steps"]] == plan_refs(config)
    assert calls == {"build_agents": 1, "build_control_plane_db": 1}


# ---------------------------------------------------------------------------
# Registry conformance and the metadata table
# ---------------------------------------------------------------------------


def test_the_two_executors_satisfy_the_convention_and_are_listed():
    assert check_registry([check_spec_gaps, derive_plan]) == ()
    for executor in (check_spec_gaps, derive_plan):
        assert inspect.iscoroutinefunction(executor)
        assert f"| `{executor.__name__}` |" in _REGISTRY_DOC.read_text(encoding="utf-8")
    assert {"check_spec_gaps", "derive_plan"} <= _names()


def test_metadata_names_exactly_the_registered_executors():
    assert set(EXECUTOR_METADATA) == _names()
    blocking = {
        name for name, meta in EXECUTOR_METADATA.items() if meta.permits_or_blocks
    }
    assert blocking == {"decide_load_verdict", "check_preflight", "materialise_gold"}


def test_every_reserved_agent_in_the_routing_table_can_be_planned():
    for agent_id in ROUTING_TABLE:
        config = {"steps": [{"type": "Step", "name": agent_id, "agent_id": agent_id}]}
        plan = plan_from_config(
            config,
            use_case_id="uc",
            version=1,
            workflow_component_id=None,
            executor_names=_names(),
        )
        assert plan["steps"][0]["model_tier"] in ("small", "frontier")
