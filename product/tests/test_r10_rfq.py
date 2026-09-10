"""Unit tests for R10 -- the RFQ chain's own executor, agent schema and workflow shape.

Everything that touches Postgres or Neo4j (the friction join, the guard, the real gold
comparison) is exercised by ``cookbook/94_retail/R10_rfq_workflow.py`` against the real
stack; what is pinned here is every rule that can be wrong without one -- the same split
``test_r5_gold.py`` and ``test_r7_preflight.py`` already draw.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest
from egpt.workflow.types import OnError, StepInput, StepOutput

from egpt_retail.app.components import (
    FUNNEL_EXPLANATION_ID,
    RFQ_WORKFLOW_ID,
    build_registry,
    build_workflows,
)
from egpt_retail.app.db import build_control_plane_db
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import KpiRegistry
from egpt_retail.contracts.vocabulary import load_vocabulary_set
from egpt_retail.executors.registry import check_registry
from egpt_retail.executors.rfq import (
    DIRECTION_DOWN,
    DIRECTION_FLAT,
    DIRECTION_NEW,
    DIRECTION_UNKNOWN,
    DIRECTION_UP,
    FRICTION_QUERY,
    check_explanation_grounding,
    friction_unavailable,
    prior_period_statement,
    rank_movers,
    sample_rows,
)
from egpt_retail.kpi.rfq import RFQ_KPI_DEFINITIONS
from egpt_retail.routing.table import ROUTING_TABLE, Tier, tier_for
from egpt_retail.skills.funnel_explanation import (
    BrandMovement,
    Direction,
    FrictionSample,
    FunnelExplanation,
    citations_within_input,
    closed_sets_match_prompt,
    movers_match_input,
)

_REGISTRY_DOC = (
    Path(__file__).resolve().parents[1]
    / "egpt_retail"
    / "contracts"
    / "EXECUTOR_REGISTRY.md"
)


# ---------------------------------------------------------------------------
# rank_movers -- pure
# ---------------------------------------------------------------------------


def test_rank_movers_ranks_by_absolute_delta_largest_first():
    current = [{"brand": "a", "value": "10"}, {"brand": "b", "value": "5"}]
    prior = [{"brand": "a", "value": "12"}, {"brand": "b", "value": "1"}]

    ranked = rank_movers(current, prior)

    assert [r["brand"] for r in ranked] == ["b", "a"]
    assert ranked[0]["direction"] == DIRECTION_UP
    assert ranked[1]["direction"] == DIRECTION_DOWN


def test_rank_movers_reports_new_for_a_brand_absent_from_the_prior_period():
    """A brand with no history is `new`, not dropped -- the build plan's evidence line is
    "none appears that is absent from the provenance record"; silently excluding a brand
    with no prior row would be its own kind of invented gap."""
    ranked = rank_movers([{"brand": "c", "value": "7"}], [])

    assert ranked == [
        {
            "brand": "c",
            "current_value": "7",
            "prior_value": None,
            "delta": None,
            "direction": DIRECTION_NEW,
        }
    ]


def test_rank_movers_reports_unknown_when_either_value_is_none():
    ranked = rank_movers(
        [{"brand": "a", "value": None}], [{"brand": "a", "value": "5"}]
    )
    assert ranked[0]["direction"] == DIRECTION_UNKNOWN
    assert ranked[0]["delta"] is None


def test_rank_movers_reports_flat_for_an_equal_known_value_not_unknown():
    """A zero delta between two *known* values is a real comparison -- `unknown` is reserved
    for a value that could not be compared at all, per its own docstring."""
    ranked = rank_movers([{"brand": "a", "value": "5"}], [{"brand": "a", "value": "5"}])
    assert ranked[0]["direction"] == DIRECTION_FLAT
    assert ranked[0]["delta"] == "0"


def test_rank_movers_skips_a_row_with_no_brand():
    assert rank_movers([{"brand": None, "value": "1"}], []) == []


def test_rank_movers_computes_delta_with_decimal_not_float():
    """`0.18 - 0.23` on Python `float` is `-0.050000000000000044`, not `-0.05` -- the exact
    binary-rounding error `read_gold` keeps values as `str` from a Postgres `numeric` column
    specifically to avoid. `rank_movers` must round-trip through `Decimal`, not `float`."""
    ranked = rank_movers(
        [{"brand": "a", "value": "0.18"}], [{"brand": "a", "value": "0.23"}]
    )
    assert ranked[0]["delta"] == "-0.05"


def test_friction_unavailable_shape():
    result = friction_unavailable("no shared identity key yet")
    assert result == {
        "available": False,
        "reason": "no shared identity key yet",
        "rows": [],
    }


def test_prior_period_statement_reads_the_kpis_own_gold_view():
    statement = prior_period_statement("rfq__quote_requests_by_brand")
    assert "gold.kpi_rfq__quote_requests_by_brand" in statement
    assert "as_of < :as_of" in statement
    assert "DISTINCT ON (brand)" in statement


def test_friction_query_joins_on_the_one_shared_column():
    """`silver.analytics_event.session_id` is documented as not joinable to
    `silver.session_replay_session.session_id` -- `identity_key` is the only column both
    tables carry, and it is the only one this statement may join on."""
    assert "silver.analytics_event" in FRICTION_QUERY
    assert "silver.session_replay_session" in FRICTION_QUERY
    assert "ON s.identity_key = e.identity_key" in FRICTION_QUERY
    assert "session_id" not in FRICTION_QUERY.split("ON")[1]


# ---------------------------------------------------------------------------
# The executor: registry conformance
# ---------------------------------------------------------------------------


def test_sample_rows_satisfies_the_registry_convention_and_is_async():
    assert check_registry([sample_rows]) == ()
    assert inspect.iscoroutinefunction(sample_rows)
    assert list(inspect.signature(sample_rows).parameters) == ["step_input"]


def test_the_registry_document_lists_sample_rows():
    assert f"| `{sample_rows.__name__}` |" in _REGISTRY_DOC.read_text(encoding="utf-8")


def test_build_registry_includes_sample_rows():
    names = {f.__name__ for f in build_registry().functions}
    assert "sample_rows" in names


def test_check_explanation_grounding_satisfies_the_registry_convention_and_is_async():
    assert check_registry([check_explanation_grounding]) == ()
    assert inspect.iscoroutinefunction(check_explanation_grounding)
    assert list(inspect.signature(check_explanation_grounding).parameters) == [
        "step_input"
    ]


def test_the_registry_document_lists_check_explanation_grounding():
    assert f"| `{check_explanation_grounding.__name__}` |" in _REGISTRY_DOC.read_text(
        encoding="utf-8"
    )


def test_build_registry_includes_check_explanation_grounding():
    names = {f.__name__ for f in build_registry().functions}
    assert "check_explanation_grounding" in names


# ---------------------------------------------------------------------------
# The RFQ KPI definitions
# ---------------------------------------------------------------------------


def test_rfq_kpi_definitions_validate_against_the_shipped_vocabularies():
    registry = KpiRegistry.build(RFQ_KPI_DEFINITIONS, load_vocabulary_set())
    identifiers = {kpi.identifier for kpi in registry}
    assert identifiers == {"rfq__quote_requests_by_brand", "rfq__quote_to_deal_rate"}
    assert all(kpi.grain == "brand" for kpi in registry)
    assert all(kpi.business_area == "sales" for kpi in registry)
    # Neither declares a `{scope}` predicate: a shipped template KPI is not scoped by a
    # concept's accepted sql_filter the way a customer-authored formula would be.
    assert all(kpi.parameters == () for kpi in registry)


# ---------------------------------------------------------------------------
# funnel_explanation's schema and its deterministic checkers
# ---------------------------------------------------------------------------


def _explanation(**overrides):
    defaults = dict(
        kpi_identifiers=["rfq__quote_to_deal_rate"],
        top_movers=[
            BrandMovement(
                kpi_identifier="rfq__quote_to_deal_rate",
                brand="brand_a",
                current_value="0.18",
                prior_value="0.23",
                delta="-0.05",
                direction=Direction.DOWN,
            )
        ],
        friction_available=False,
        friction=[],
        brands_cited=["brand_a"],
        summary="brand_a fell.",
        likely_drivers=[],
        snapshot_version="r10-test.1",
    )
    defaults.update(overrides)
    return FunnelExplanation(**defaults)


def test_closed_sets_match_prompt():
    assert closed_sets_match_prompt() == {"direction": True}


def test_citations_within_input_accepts_a_clean_explanation():
    assert citations_within_input(_explanation()) is True


def test_citations_within_input_rejects_a_brand_the_input_never_carried():
    bad = _explanation(brands_cited=["brand_a", "brand_z"])
    assert citations_within_input(bad) is False


def test_citations_within_input_rejects_a_driver_naming_a_friction_event_never_carried():
    """A driver sentence naming neither a cited brand nor a friction event actually present
    in the input -- the citation-grounding half of the fourth standing rule, checkable
    because `friction.rows` names its events explicitly rather than being free text."""
    bad = _explanation(
        friction_available=True,
        friction=[
            FrictionSample(
                event_name="form_start", rage_clicks=1, dead_clicks=0, script_errors=0
            )
        ],
        likely_drivers=["The submit page saw a spike in dead clicks."],
    )
    assert citations_within_input(bad) is False


def test_citations_within_input_accepts_a_driver_naming_a_cited_brand():
    ok = _explanation(likely_drivers=["brand_a's drop is the whole story this period."])
    assert citations_within_input(ok) is True


def test_citations_within_input_rejects_a_driver_matching_only_as_a_substring():
    """A real brand that is a *prefix* of a fabricated one must not count as a citation --
    `"brand_a" in "...brand_ab..."` is true as plain substring containment but `brand_ab` is
    not a term this input ever carried."""
    bad = _explanation(
        likely_drivers=["Something about brand_ab happened this period."]
    )
    assert citations_within_input(bad) is False


def test_citations_within_input_checks_summary_too():
    """`summary` is prose a person reads first and the module's own rule requires every brand
    or event name it names to be grounded, same as `likely_drivers`."""
    bad = _explanation(summary="evilbrand had a rough quarter.")
    assert citations_within_input(bad) is False


def test_citations_within_input_accepts_an_honest_no_data_summary():
    """With no movers and no friction, there is nothing to cite from and nothing to check a
    summary against -- a "nothing to report" summary must not be rejected just because it
    names no grounded term, since there is no term to ground it against either."""
    empty = _explanation(
        top_movers=[],
        brands_cited=[],
        friction_available=False,
        friction=[],
        summary="No data was available to compare this period.",
        likely_drivers=[],
    )
    assert citations_within_input(empty) is True


def test_movers_match_input_requires_verbatim_copy():
    movers_input = {
        "rfq__quote_to_deal_rate": [
            {
                "brand": "brand_a",
                "current_value": "0.18",
                "prior_value": "0.23",
                "delta": "-0.05",
                "direction": "down",
            }
        ]
    }
    assert movers_match_input(_explanation(), movers_input) is True

    reordered_input = {
        "rfq__quote_to_deal_rate": [
            {
                "brand": "brand_a",
                "current_value": "0.99",  # not what the explanation carries
                "prior_value": "0.23",
                "delta": "-0.05",
                "direction": "down",
            }
        ]
    }
    assert movers_match_input(_explanation(), reordered_input) is False


def test_friction_sample_round_trips():
    sample = FrictionSample(
        event_name="form_start", rage_clicks=12, dead_clicks=3, script_errors=1
    )
    explanation = _explanation(friction_available=True, friction=[sample])
    assert explanation.friction[0].event_name == "form_start"


# ---------------------------------------------------------------------------
# check_explanation_grounding -- the runtime guard between the model step and delivery
# ---------------------------------------------------------------------------

_GROUNDED_MOVERS_INPUT = {
    "rfq__quote_to_deal_rate": [
        {
            "brand": "brand_a",
            "current_value": "0.18",
            "prior_value": "0.23",
            "delta": "-0.05",
            "direction": "down",
        }
    ]
}


def _grounding_input(explanation, movers_input=None):
    return StepInput(
        previous_step_content=explanation.model_dump(),
        previous_step_outputs={
            "sample-rows": StepOutput(
                content={"movers": movers_input or _GROUNDED_MOVERS_INPUT}
            )
        },
    )


def test_check_explanation_grounding_passes_a_grounded_explanation():
    output = asyncio.run(check_explanation_grounding(_grounding_input(_explanation())))
    assert output.success is True
    assert not output.stop


def test_check_explanation_grounding_stops_the_chain_on_an_edited_mover():
    bad_movers = {
        "rfq__quote_to_deal_rate": [
            {
                "brand": "brand_a",
                "current_value": "0.99",  # not what the explanation carries
                "prior_value": "0.23",
                "delta": "-0.05",
                "direction": "down",
            }
        ]
    }
    output = asyncio.run(
        check_explanation_grounding(_grounding_input(_explanation(), bad_movers))
    )
    assert output.success is False
    assert output.stop is True


def test_check_explanation_grounding_stops_the_chain_on_an_uncited_brand():
    bad = _explanation(brands_cited=["brand_a", "brand_z"])
    output = asyncio.run(check_explanation_grounding(_grounding_input(bad)))
    assert output.success is False
    assert output.stop is True


def test_check_explanation_grounding_stops_the_chain_on_malformed_content():
    step_input = StepInput(
        previous_step_content={"not": "a funnel explanation"},
        previous_step_outputs={
            "sample-rows": StepOutput(content={"movers": _GROUNDED_MOVERS_INPUT})
        },
    )
    output = asyncio.run(check_explanation_grounding(step_input))
    assert output.success is False
    assert output.stop is True


# ---------------------------------------------------------------------------
# Routing: funnel_explanation is reserved and routed to the small tier (R8, G6)
# ---------------------------------------------------------------------------


def test_funnel_explanation_is_routed_to_the_small_tier():
    assert FUNNEL_EXPLANATION_ID in ROUTING_TABLE
    assert tier_for(FUNNEL_EXPLANATION_ID) is Tier.SMALL


# ---------------------------------------------------------------------------
# The workflow: rfq-funnel is the API contract's own six, in order
# ---------------------------------------------------------------------------


def test_rfq_funnel_workflow_is_the_contracts_own_steps_in_order():
    """Seven steps: the API contract's own six (`paths/use-cases.yaml`,
    `runUseCasePreflight`) plus `check-explanation-grounding`, added between the model step
    and delivery after review found the schema's own checkers were reachable from nowhere at
    run time. `paths/use-cases.yaml`'s example plan records the drift."""
    settings = RetailSettings(
        retail_db_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/none"
    )
    workflows = {
        w.id: w for w in build_workflows(settings, build_control_plane_db(settings))
    }
    rfq = workflows[RFQ_WORKFLOW_ID]

    assert [s.name for s in rfq.steps] == [
        "check-preflight",
        "read-gold",
        "sample-rows",
        "funnel-explanation",
        "check-explanation-grounding",
        "record-provenance",
        "deliver-in-app",
    ]
    assert all(s.on_error is OnError.fail for s in rfq.steps)

    agent_step = next(s for s in rfq.steps if s.name == "funnel-explanation")
    assert getattr(agent_step, "agent", None) is not None
    assert agent_step.agent.id == FUNNEL_EXPLANATION_ID
    assert agent_step.agent.output_schema is FunnelExplanation
    assert agent_step.agent.model is None  # routing decides; a pinned model is refused


def test_rfq_funnel_workflow_reuses_the_same_agent_object_passed_in():
    """`build_agent_os` passes its already-built `agents` list through so the OS-registered
    agent and the workflow step's agent are one object, which is what lets the memory
    lifespan's provider assignment reach the one this workflow actually runs."""
    settings = RetailSettings(
        retail_db_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/none"
    )
    db = build_control_plane_db(settings)
    from egpt_retail.app.components import build_agents

    agents = build_agents(settings, db)
    funnel_explanation = next(a for a in agents if a.id == FUNNEL_EXPLANATION_ID)

    workflows = {w.id: w for w in build_workflows(settings, db, agents)}
    step = next(
        s for s in workflows[RFQ_WORKFLOW_ID].steps if s.name == "funnel-explanation"
    )
    assert step.agent is funnel_explanation


def test_build_workflows_names_the_missing_agent_instead_of_a_bare_stopiteration():
    settings = RetailSettings(
        retail_db_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/none"
    )
    db = build_control_plane_db(settings)

    with pytest.raises(ValueError, match=FUNNEL_EXPLANATION_ID):
        build_workflows(settings, db, agents=[])
