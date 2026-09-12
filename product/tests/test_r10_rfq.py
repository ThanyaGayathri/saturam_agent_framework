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
    Driver,
    DriverKind,
    DriverReason,
    FrictionSample,
    FunnelExplanation,
    citations_within_input,
    closed_sets_match_prompt,
    friction_matches_input,
    movers_match_input,
    render_summary,
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
        friction_reason="",
        drivers=[],
        snapshot_version="r10-test.1",
    )
    defaults.update(overrides)
    return FunnelExplanation(**defaults)


def test_closed_sets_match_prompt():
    assert closed_sets_match_prompt() == {
        "direction": True,
        "kind": True,
        "reason": True,
    }


def test_citations_within_input_accepts_a_clean_explanation():
    assert citations_within_input(_explanation()) is True


def test_citations_within_input_rejects_a_brand_the_input_never_carried():
    bad = _explanation(
        drivers=[
            Driver(
                kind=DriverKind.BRAND,
                ref="brand_z",
                kpi_identifier="rfq__quote_to_deal_rate",
                reason=DriverReason.OTHER,
            )
        ]
    )
    assert citations_within_input(bad) is False


def test_citations_within_input_rejects_a_driver_naming_a_friction_event_never_carried():
    """A driver referencing neither a real brand nor a friction event actually present in the
    input -- the citation-grounding half of the fourth standing rule, checkable by exact
    equality now that a driver's subject is a reference, never free text."""
    bad = _explanation(
        friction_available=True,
        friction=[
            FrictionSample(
                event_name="form_start", rage_clicks=1, dead_clicks=0, script_errors=0
            )
        ],
        drivers=[
            Driver(
                kind=DriverKind.FRICTION_EVENT,
                ref="submit_page",
                reason=DriverReason.ELEVATED_FRICTION,
            )
        ],
    )
    assert citations_within_input(bad) is False


def test_citations_within_input_accepts_a_driver_naming_a_cited_brand():
    ok = _explanation(
        drivers=[
            Driver(
                kind=DriverKind.BRAND,
                ref="brand_a",
                kpi_identifier="rfq__quote_to_deal_rate",
                reason=DriverReason.LARGEST_MOVE,
            )
        ]
    )
    assert citations_within_input(ok) is True


def test_citations_within_input_rejects_a_driver_matching_only_as_a_near_miss():
    """A real brand's name being a *prefix* of the reference must not count as a match --
    `ref` is checked by exact equality, not substring containment, so `brand_ab` is not
    `brand_a` even though the old text-search implementation could have been fooled by it."""
    bad = _explanation(
        drivers=[
            Driver(
                kind=DriverKind.BRAND,
                ref="brand_ab",
                kpi_identifier="rfq__quote_to_deal_rate",
                reason=DriverReason.OTHER,
            )
        ]
    )
    assert citations_within_input(bad) is False


def test_citations_within_input_rejects_the_issue_209_repro_shape():
    """The exact gap issue #209 found: a sentence citing one real brand alongside a
    fabricated one used to pass, because the old free-text checker only required one
    grounded hit per sentence. There is no free-text field left to smuggle a second entity
    into -- even folding the old exploit sentence into `ref` itself is refused, since `ref`
    is checked for exact equality against `top_movers`/`friction`, not "mentions"."""
    bad = _explanation(
        drivers=[
            Driver(
                kind=DriverKind.BRAND,
                ref="brand_a's drop was driven by rival evilbrand entering the market",
                kpi_identifier="rfq__quote_to_deal_rate",
                reason=DriverReason.OTHER,
            )
        ]
    )
    assert citations_within_input(bad) is False


def test_citations_within_input_rejects_a_mix_of_grounded_and_fabricated_drivers():
    """The N-1-grounded-plus-one-fabricated shape issue #209 found, exercised with multiple
    `Driver` entries rather than folding the whole exploit into a single `ref` -- every
    driver must be grounded, not just most of them."""
    bad = _explanation(
        friction_available=True,
        friction=[
            FrictionSample(
                event_name="form_start", rage_clicks=1, dead_clicks=0, script_errors=0
            )
        ],
        drivers=[
            Driver(
                kind=DriverKind.BRAND,
                ref="brand_a",
                kpi_identifier="rfq__quote_to_deal_rate",
                reason=DriverReason.LARGEST_MOVE,
            ),
            Driver(
                kind=DriverKind.FRICTION_EVENT,
                ref="form_start",
                reason=DriverReason.ELEVATED_FRICTION,
            ),
            Driver(
                kind=DriverKind.BRAND,
                ref="evilbrand",
                kpi_identifier="rfq__quote_to_deal_rate",
                reason=DriverReason.OTHER,
            ),
        ],
    )
    assert citations_within_input(bad) is False


def test_citations_within_input_accepts_no_drivers_with_no_data():
    """With no movers and no friction, there is nothing to reference -- an empty `drivers`
    list is honest, not a violation."""
    empty = _explanation(
        top_movers=[], friction_available=False, friction=[], drivers=[]
    )
    assert citations_within_input(empty) is True


# ---------------------------------------------------------------------------
# render_summary -- the deterministic renderer that replaced model-authored prose
# ---------------------------------------------------------------------------


def test_render_summary_reports_honest_no_data():
    """With no movers and no friction, the rendered summary names the KPI it's reporting on
    (funnel_explanation.md's own documented example) -- built from `kpi_identifiers`, never
    from anything the model wrote."""
    empty = _explanation(
        top_movers=[], friction_available=False, friction=[], drivers=[]
    )
    assert render_summary(empty) == (
        "No gold rows were available for rfq__quote_to_deal_rate this run."
    )


def test_render_summary_builds_a_sentence_from_a_grounded_brand_driver():
    ok = _explanation(
        drivers=[
            Driver(
                kind=DriverKind.BRAND,
                ref="brand_a",
                kpi_identifier="rfq__quote_to_deal_rate",
                reason=DriverReason.LARGEST_MOVE,
            )
        ]
    )
    assert (
        render_summary(ok)
        == "brand_a's rfq__quote_to_deal_rate fell to 0.18 from 0.23."
    )


def test_render_summary_builds_a_sentence_from_a_grounded_friction_driver():
    ok = _explanation(
        friction_available=True,
        friction=[
            FrictionSample(
                event_name="form_start", rage_clicks=12, dead_clicks=3, script_errors=1
            )
        ],
        drivers=[
            Driver(
                kind=DriverKind.FRICTION_EVENT,
                ref="form_start",
                reason=DriverReason.ELEVATED_FRICTION,
            )
        ],
    )
    assert render_summary(ok) == (
        "form_start carried elevated friction this period "
        "(12 rage clicks, 3 dead clicks, 1 script error)."
    )


def test_render_summary_falls_back_to_the_first_mover_with_no_drivers():
    """A model that named no drivers still gets an honest, grounded headline -- the largest
    mover, since `top_movers` is already ranked by size of change."""
    ok = _explanation(drivers=[])
    assert (
        render_summary(ok)
        == "brand_a's rfq__quote_to_deal_rate fell to 0.18 from 0.23."
    )


def test_render_summary_falls_back_to_the_first_friction_row_with_no_resolved_drivers():
    """Real friction data with no `top_movers` and no driver that resolves must still render
    something, not silently return an empty string."""
    ok = _explanation(
        top_movers=[],
        friction_available=True,
        friction=[
            FrictionSample(
                event_name="form_start", rage_clicks=12, dead_clicks=3, script_errors=1
            )
        ],
        drivers=[],
    )
    assert render_summary(ok) == (
        "form_start carried elevated friction this period "
        "(12 rage clicks, 3 dead clicks, 1 script error)."
    )


def test_render_summary_singularizes_a_friction_count_of_one():
    ok = _explanation(
        friction_available=True,
        friction=[
            FrictionSample(
                event_name="form_start", rage_clicks=1, dead_clicks=1, script_errors=1
            )
        ],
        drivers=[
            Driver(
                kind=DriverKind.FRICTION_EVENT,
                ref="form_start",
                reason=DriverReason.ELEVATED_FRICTION,
            )
        ],
    )
    assert render_summary(ok) == (
        "form_start carried elevated friction this period "
        "(1 rage click, 1 dead click, 1 script error)."
    )


def test_render_summary_deduplicates_repeated_driver_references():
    ok = _explanation(
        drivers=[
            Driver(
                kind=DriverKind.BRAND,
                ref="brand_a",
                kpi_identifier="rfq__quote_to_deal_rate",
                reason=DriverReason.LARGEST_MOVE,
            ),
            Driver(
                kind=DriverKind.BRAND,
                ref="brand_a",
                kpi_identifier="rfq__quote_to_deal_rate",
                reason=DriverReason.LARGEST_MOVE,
            ),
        ]
    )
    assert (
        render_summary(ok)
        == "brand_a's rfq__quote_to_deal_rate fell to 0.18 from 0.23."
    )


def test_render_summary_disambiguates_a_brand_on_two_brand_grain_kpis():
    """The same brand can legitimately be a top mover on two different brand-grain KPIs in
    the same run -- a `Driver` must disambiguate by `kpi_identifier`, not just `brand`, or one
    KPI's movement silently overwrites the other's in the rendered summary."""
    ok = _explanation(
        top_movers=[
            BrandMovement(
                kpi_identifier="rfq__quote_to_deal_rate",
                brand="brand_a",
                current_value="0.18",
                prior_value="0.23",
                delta="-0.05",
                direction=Direction.DOWN,
            ),
            BrandMovement(
                kpi_identifier="rfq__quote_requests_by_brand",
                brand="brand_a",
                current_value="41",
                prior_value="30",
                delta="11",
                direction=Direction.UP,
            ),
        ],
        drivers=[
            Driver(
                kind=DriverKind.BRAND,
                ref="brand_a",
                kpi_identifier="rfq__quote_requests_by_brand",
                reason=DriverReason.LARGEST_MOVE,
            )
        ],
    )
    assert (
        render_summary(ok)
        == "brand_a's rfq__quote_requests_by_brand rose to 41 from 30."
    )


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


def test_funnel_explanation_rejects_friction_available_disagreeing_with_friction():
    with pytest.raises(ValueError):
        _explanation(friction_available=True, friction=[])
    with pytest.raises(ValueError):
        _explanation(
            friction_available=False,
            friction=[
                FrictionSample(
                    event_name="form_start",
                    rage_clicks=1,
                    dead_clicks=0,
                    script_errors=0,
                )
            ],
        )


def test_driver_rejects_a_brand_kind_with_no_kpi_identifier():
    with pytest.raises(ValueError):
        Driver(kind=DriverKind.BRAND, ref="brand_a", reason=DriverReason.LARGEST_MOVE)


def test_driver_accepts_a_friction_event_kind_with_no_kpi_identifier():
    """`kpi_identifier` is meaningless for a friction driver -- only `kind=brand` requires
    it."""
    driver = Driver(
        kind=DriverKind.FRICTION_EVENT,
        ref="form_start",
        reason=DriverReason.ELEVATED_FRICTION,
    )
    assert driver.kpi_identifier == ""


def test_friction_matches_input_accepts_a_verbatim_copy():
    friction_input = {
        "available": True,
        "reason": "",
        "rows": [
            {
                "event_name": "form_start",
                "rage_clicks": 12,
                "dead_clicks": 3,
                "script_errors": 1,
            }
        ],
    }
    ok = _explanation(
        friction_available=True,
        friction=[
            FrictionSample(
                event_name="form_start", rage_clicks=12, dead_clicks=3, script_errors=1
            )
        ],
    )
    assert friction_matches_input(ok, friction_input) is True


def test_friction_matches_input_rejects_a_fabricated_row():
    friction_input = {"available": False, "reason": "no join yet", "rows": []}
    bad = _explanation(
        friction_available=True,
        friction=[
            FrictionSample(
                event_name="form_start", rage_clicks=12, dead_clicks=3, script_errors=1
            )
        ],
    )
    assert friction_matches_input(bad, friction_input) is False


def test_friction_matches_input_rejects_a_suppressed_availability():
    friction_input = {
        "available": True,
        "reason": "",
        "rows": [
            {
                "event_name": "form_start",
                "rage_clicks": 12,
                "dead_clicks": 3,
                "script_errors": 1,
            }
        ],
    }
    bad = _explanation(friction_reason="nothing to attribute")
    assert friction_matches_input(bad, friction_input) is False


def test_friction_matches_input_rejects_an_ungrounded_reason():
    friction_input = {"available": False, "reason": "the real reason", "rows": []}
    bad = _explanation(friction_reason="an invented reason")
    assert friction_matches_input(bad, friction_input) is False


def test_friction_matches_input_rejects_a_fabricated_reason_alongside_real_rows():
    """`friction_reason` must be checked even when `friction_available` is true and the real
    rows are copied verbatim -- otherwise a model can smuggle fabricated prose into the one
    field this same check exists to close off, right alongside an otherwise-honest response."""
    friction_input = {
        "available": True,
        "reason": "",
        "rows": [
            {
                "event_name": "form_start",
                "rage_clicks": 12,
                "dead_clicks": 3,
                "script_errors": 1,
            }
        ],
    }
    bad = _explanation(
        friction_available=True,
        friction=[
            FrictionSample(
                event_name="form_start", rage_clicks=12, dead_clicks=3, script_errors=1
            )
        ],
        friction_reason="brand_a's drop was driven by rival evilbrand entering the market",
    )
    assert friction_matches_input(bad, friction_input) is False


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


def _grounding_input(explanation, movers_input=None, friction_input=None):
    return StepInput(
        previous_step_content=explanation.model_dump(),
        previous_step_outputs={
            "sample-rows": StepOutput(
                content={
                    "movers": movers_input or _GROUNDED_MOVERS_INPUT,
                    "friction": friction_input
                    or {"available": False, "reason": "", "rows": []},
                }
            )
        },
    )


def test_check_explanation_grounding_passes_a_grounded_explanation():
    output = asyncio.run(check_explanation_grounding(_grounding_input(_explanation())))
    assert output.success is True
    assert not output.stop


def test_check_explanation_grounding_attaches_the_real_render_summary_output():
    """The success-path content must carry `render_summary`'s actual rendered string, not
    just any `"summary"` key -- this is what `record_provenance`/`deliver_in_app` forward to
    the delivery record, so a stale or placeholder value here would silently break the claim
    that the delivery screen shows the deterministic headline."""
    explanation = _explanation()
    output = asyncio.run(check_explanation_grounding(_grounding_input(explanation)))
    assert output.content["summary"] == render_summary(explanation)


def test_check_explanation_grounding_drops_notes_from_what_it_forwards():
    """`notes` is the one field this schema leaves genuinely free text with no grounding
    check at all -- it must not reach the delivery record now that `check_explanation_
    grounding`'s output is actually wired through `record_provenance`/`deliver_in_app`."""
    explanation = _explanation(
        notes="brand_a's drop was driven by rival evilbrand entering the market"
    )
    output = asyncio.run(check_explanation_grounding(_grounding_input(explanation)))
    assert output.success is True
    assert "notes" not in output.content
    assert "summary" in output.content


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
    bad = _explanation(
        drivers=[
            Driver(
                kind=DriverKind.BRAND,
                ref="brand_z",
                kpi_identifier="rfq__quote_to_deal_rate",
                reason=DriverReason.OTHER,
            )
        ]
    )
    output = asyncio.run(check_explanation_grounding(_grounding_input(bad)))
    assert output.success is False
    assert output.stop is True


def test_check_explanation_grounding_stops_the_chain_on_a_fabricated_friction_row():
    bad = _explanation(
        friction_available=True,
        friction=[
            FrictionSample(
                event_name="checkout_error",
                rage_clicks=99,
                dead_clicks=0,
                script_errors=0,
            )
        ],
    )
    output = asyncio.run(check_explanation_grounding(_grounding_input(bad)))
    assert output.success is False
    assert output.stop is True


def test_check_explanation_grounding_stops_the_chain_on_a_suppressed_friction_available():
    """A model that sets `friction_available=False` while the real input's friction was
    available -- hiding a real signal from the reader while substituting a `friction_reason`
    narrative -- must be refused, not silently pass through."""
    bad = _explanation(friction_reason="nothing to attribute this run")
    real_friction = {
        "available": True,
        "reason": "",
        "rows": [
            {
                "event_name": "checkout_error",
                "rage_clicks": 4,
                "dead_clicks": 1,
                "script_errors": 2,
            }
        ],
    }
    output = asyncio.run(
        check_explanation_grounding(_grounding_input(bad, friction_input=real_friction))
    )
    assert output.success is False
    assert output.stop is True


def test_check_explanation_grounding_stops_the_chain_on_an_ungrounded_friction_reason():
    """`friction_reason` is free text -- it must match the real input's `friction.reason`
    verbatim when friction is genuinely unavailable, not carry a model-invented narrative."""
    bad = _explanation(friction_reason="a narrative the model invented")
    real_friction = {
        "available": False,
        "reason": "no row on either side carries a populated identity_key yet",
        "rows": [],
    }
    output = asyncio.run(
        check_explanation_grounding(_grounding_input(bad, friction_input=real_friction))
    )
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
