"""Unit tests for `egpt_retail.skills.dashboard_suggestion` -- the draft schema, the prompt
renderer, and the executor that trusts nothing the model said about a KPI beyond its
identifier. No Postgres, no Neo4j, no live model call: every KPI here is a plain
`KpiDefinition` constructed in-process, exactly as `test_kpis_router.py`'s own `_kpi()` helper
builds fixtures for K12's tests.
"""

from __future__ import annotations

import pytest

from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.skills import dashboard_suggestion as dsg


def _kpi(**overrides) -> KpiDefinition:
    defaults = dict(
        identifier="session__active_analytics_user_count",
        name="Active analytics user count",
        description="Distinct analytics users active in the period.",
        sql="SELECT count(DISTINCT user_id) FROM sessions",
        grain="overall",
        business_area="marketing",
        source_tables=frozenset({"sessions"}),
        unit="count",
    )
    defaults.update(overrides)
    return KpiDefinition(**defaults)


def _draft(**overrides) -> dsg.DashboardSuggestionDraft:
    defaults = dict(
        name="Weekly engagement",
        tiles=[
            dsg.DashboardTileDraft(
                kpi_identifier="session__active_analytics_user_count",
                rationale="Directly measures engagement.",
            )
        ],
        business_area=None,
        notes="",
    )
    defaults.update(overrides)
    return dsg.DashboardSuggestionDraft(**defaults)


# ---------------------------------------------------------------------------
# render_prompt
# ---------------------------------------------------------------------------


def test_render_prompt_embeds_every_selectable_kpi():
    kpi = _kpi()
    prompt = dsg.render_prompt(kpis=[kpi])
    assert kpi.identifier in prompt
    assert kpi.name in prompt
    assert kpi.business_area in prompt


def test_render_prompt_says_so_when_no_kpi_is_ready():
    prompt = dsg.render_prompt(kpis=[])
    assert "no ready KPIs" in prompt


def test_render_prompt_refuses_a_template_missing_a_placeholder(tmp_path):
    broken = tmp_path / "broken.md"
    broken.write_text("no placeholders here")
    with pytest.raises(ValueError, match="kpis"):
        dsg.render_prompt(kpis=[_kpi()], prompt_path=broken)


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------


def test_assemble_accepts_a_tile_naming_a_ready_kpi():
    kpi = _kpi()
    report = dsg.assemble(_draft(), ready_kpis=[kpi])
    assert [t.kpi_identifier for t in report.suggestion.tiles] == [kpi.identifier]
    # The real registry name is copied across, never the model's -- the draft schema has no
    # `name` field on a tile at all, so this is really asserting the executor filled it in.
    assert report.suggestion.tiles[0].name == kpi.name
    assert report.dropped == []


def test_assemble_drops_a_tile_naming_a_kpi_outside_the_ready_set():
    draft = _draft(
        tiles=[
            dsg.DashboardTileDraft(
                kpi_identifier="not_a_real_kpi__identifier",
                rationale="hallucinated",
            )
        ]
    )
    report = dsg.assemble(draft, ready_kpis=[_kpi()])
    assert report.suggestion.tiles == []
    assert "not_a_real_kpi__identifier" in report.dropped[0]


def test_assemble_drops_a_tile_repeated_twice_in_one_response():
    kpi = _kpi()
    draft = _draft(
        tiles=[
            dsg.DashboardTileDraft(kpi_identifier=kpi.identifier, rationale="first"),
            dsg.DashboardTileDraft(kpi_identifier=kpi.identifier, rationale="again"),
        ]
    )
    report = dsg.assemble(draft, ready_kpis=[kpi])
    assert len(report.suggestion.tiles) == 1
    assert any("proposed twice" in reason for reason in report.dropped)


def test_assemble_accepts_a_valid_business_area_as_a_filter():
    kpi = _kpi(business_area="marketing")
    report = dsg.assemble(_draft(business_area="marketing"), ready_kpis=[kpi])
    assert len(report.suggestion.filters) == 1
    assert report.suggestion.filters[0].field.value == "business_area"
    assert report.suggestion.filters[0].value == "marketing"


def test_assemble_drops_an_unknown_business_area_and_notes_why():
    kpi = _kpi()
    report = dsg.assemble(_draft(business_area="not-a-real-area"), ready_kpis=[kpi])
    assert report.suggestion.filters == []
    assert "not-a-real-area" in report.suggestion.notes


def test_assemble_leaves_filters_empty_when_no_business_area_is_proposed():
    report = dsg.assemble(_draft(business_area=None), ready_kpis=[_kpi()])
    assert report.suggestion.filters == []


def test_assemble_falls_back_to_a_default_name_when_the_draft_names_nothing():
    report = dsg.assemble(_draft(name="   "), ready_kpis=[_kpi()])
    assert report.suggestion.name == "Untitled dashboard"


@pytest.mark.anyio
async def test_aassemble_agrees_with_its_sync_twin():
    kpi = _kpi()
    draft = _draft()
    sync_report = dsg.assemble(draft, ready_kpis=[kpi])
    async_report = await dsg.aassemble(draft, ready_kpis=[kpi])
    assert sync_report.suggestion == async_report.suggestion
    assert sync_report.dropped == async_report.dropped
