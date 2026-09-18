"""Unit tests for `egpt_retail.skills.dashboard_suggestion` (2.0.0) -- the draft schema, the
prompt renderer over a `SuggestionContext`, and the executor that trusts nothing the model
said about a KPI beyond its identifier and nothing about a query until a `check` has. No
Postgres, no Neo4j, no warehouse, no live model call: every KPI is a plain `KpiDefinition`,
every table a plain `Table`, and the guard is a fake `check` that returns whatever verdict the
test needs -- the real one is exercised by `test_dashboard_queries.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import pytest

from egpt_retail.api import models as wire
from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.contracts.semantic import Column, JoinEdge, Table, TableRef
from egpt_retail.skills import dashboard_suggestion as dsg


def _kpi(**overrides) -> KpiDefinition:
    defaults = dict(
        identifier="stocklevel__stockout_rate",
        name="Stock-out rate",
        description="Share of SKU-store-hours with zero on hand.",
        sql="SELECT avg(stockout_flag) FROM stock_level",
        grain="overall",
        business_area="supply_chain",
        source_tables=frozenset({"stock_level"}),
        unit="%",
    )
    defaults.update(overrides)
    return KpiDefinition(**defaults)


def _table(name="stock_level", **overrides) -> Table:
    defaults = dict(
        ref=TableRef(name=name, schema="silver", row_count=2_100_000),
        description="Hourly on-hand snapshot per SKU per store.",
        columns=(
            Column(name="sku", data_type="text", description="Product code."),
            Column(
                name="stockout_flag",
                data_type="int",
                description="",
                meaning="1 when on_hand_qty = 0 during trading hours.",
            ),
            Column(name="region", data_type="text"),
            Column(name="manager_email", data_type="text", is_personal_data=True),
        ),
    )
    defaults.update(overrides)
    return Table(**defaults)


def _context(**overrides) -> dsg.SuggestionContext:
    defaults = dict(
        kpis=(_kpi(),),
        tables=(_table(),),
        joins=(
            JoinEdge(
                left_table="stock_level",
                right_table="product",
                on="stock_level.sku = product.sku",
            ),
        ),
        chart_budget=4,
    )
    defaults.update(overrides)
    return dsg.SuggestionContext(**defaults)


def _kpi_chart(**overrides) -> dsg.DashboardChartDraft:
    defaults = dict(
        title="Stock-out rate by week",
        chart="line",
        source="kpi",
        kpi_identifier="stocklevel__stockout_rate",
        description="Share of hours with an empty shelf.",
        rationale="Directly measures what the purpose asks about.",
    )
    defaults.update(overrides)
    return dsg.DashboardChartDraft(**defaults)


def _query_chart(**overrides) -> dsg.DashboardChartDraft:
    defaults = dict(
        title="Stock-out hours by region",
        chart="bar",
        source="query",
        query=dsg.ChartQueryDraft(
            sql="SELECT region, sum(stockout_flag) AS hours FROM silver.stock_level GROUP BY 1 LIMIT 10",
            x=dsg.AxisDraft(column="region", kind="category"),
            y=[dsg.AxisDraft(column="hours", unit="count")],
        ),
        description="Hours with an empty shelf, per region.",
        rationale="Breaks the stock-out picture down by region.",
    )
    defaults.update(overrides)
    return dsg.DashboardChartDraft(**defaults)


def _draft(**overrides) -> dsg.DashboardSuggestionDraft:
    defaults = dict(
        name="Stock-outs",
        charts=[_kpi_chart()],
        business_area=None,
        notes="",
    )
    defaults.update(overrides)
    return dsg.DashboardSuggestionDraft(**defaults)


@dataclass(frozen=True)
class _Verdict:
    status: str
    detail: str
    sql: str
    tables: Tuple[str, ...] = ("stock_level",)


def _checking(status="ok", detail="", sql=None, tables=("stock_level",)):
    calls = []

    def _check(statement, context_tables):
        calls.append((statement, context_tables))
        return _Verdict(status, detail, sql or statement, tables)

    _check.calls = calls  # type: ignore[attr-defined]
    return _check


def _previewing(columns=("region", "hours"), error=None):
    def _preview(sql):
        return wire.QueryPreview(
            guard=wire.QueryGuardVerdict(status=wire.QueryGuardStatus.ok, detail=""),
            sql=sql,
            columns=list(columns),
            rows=[{c: 1 for c in columns}] if columns and not error else [],
            duration_ms=3,
            error=error,
        )

    return _preview


# ---------------------------------------------------------------------------
# render_prompt
# ---------------------------------------------------------------------------


def test_render_prompt_embeds_the_selected_kpis_with_their_formula():
    kpi = _kpi()
    prompt = dsg.render_prompt(context=_context())
    assert kpi.identifier in prompt
    assert kpi.name in prompt
    assert kpi.sql in prompt
    assert kpi.description in prompt


def test_render_prompt_embeds_every_table_column_preferring_the_reviewed_meaning():
    prompt = dsg.render_prompt(context=_context())
    assert "silver.stock_level" in prompt
    assert "Hourly on-hand snapshot" in prompt
    assert "1 when on_hand_qty = 0 during trading hours." in prompt
    assert "region · text · (no description)" in prompt


def test_render_prompt_never_lists_a_personal_data_column():
    prompt = dsg.render_prompt(context=_context())
    assert "manager_email" not in prompt


def test_render_prompt_embeds_joins_and_the_chart_budget():
    prompt = dsg.render_prompt(context=_context(chart_budget=3))
    assert "stock_level → product ON stock_level.sku = product.sku" in prompt
    assert "at most 3 charts" in prompt


def test_render_prompt_defaults_the_budget_and_clamps_it():
    assert dsg.SuggestionContext().budget == dsg.DEFAULT_CHART_BUDGET
    assert dsg.SuggestionContext(chart_budget=99).budget == dsg.MAX_CHART_BUDGET
    assert dsg.SuggestionContext(chart_budget=0).budget == 1


def test_render_prompt_says_so_when_nothing_is_selected():
    prompt = dsg.render_prompt(context=dsg.SuggestionContext())
    assert "no metrics selected" in prompt
    assert "no tables" in prompt


def test_render_prompt_refuses_a_template_missing_a_placeholder(tmp_path):
    broken = tmp_path / "broken.md"
    broken.write_text("no placeholders here")
    with pytest.raises(ValueError, match="kpis"):
        dsg.render_prompt(context=_context(), prompt_path=broken)


# ---------------------------------------------------------------------------
# SuggestionContext.summary
# ---------------------------------------------------------------------------


def test_context_summary_counts_described_columns_and_names_the_withheld_ones():
    summary = _context().summary()
    assert summary.kpi_identifiers == ["stocklevel__stockout_rate"]
    assert summary.table_names == ["stock_level"]
    # sku (description), stockout_flag (meaning) described; region not; manager_email withheld.
    assert summary.total_columns == 3
    assert summary.described_columns == 2
    assert summary.withheld_columns == ["stock_level.manager_email"]


# ---------------------------------------------------------------------------
# assemble -- kpi charts
# ---------------------------------------------------------------------------


def test_assemble_accepts_a_kpi_chart_naming_a_ready_kpi():
    kpi = _kpi()
    report = dsg.assemble(_draft(), ready_kpis=[kpi], context=_context())
    chart = report.suggestion.charts[0]
    assert chart.source is wire.TileSource.kpi
    assert chart.kpi_identifier == kpi.identifier
    assert chart.query is None
    assert chart.guard.status is wire.QueryGuardStatus.ok
    assert chart.description == "Share of hours with an empty shelf."
    assert report.dropped == []
    assert report.suggestion.context.withheld_columns == ["stock_level.manager_email"]


def test_assemble_drops_a_kpi_chart_naming_a_kpi_outside_the_ready_set():
    draft = _draft(charts=[_kpi_chart(kpi_identifier="not_a_real_kpi__identifier")])
    report = dsg.assemble(draft, ready_kpis=[_kpi()], context=_context())
    assert report.suggestion.charts == []
    assert "not_a_real_kpi__identifier" in report.dropped[0]


def test_assemble_keeps_the_same_kpi_on_two_different_chart_kinds_but_not_twice_as_one():
    kpi = _kpi()
    draft = _draft(
        charts=[
            _kpi_chart(chart="number", title="Now"),
            _kpi_chart(chart="line", title="Trend"),
            _kpi_chart(chart="line", title="Trend again"),
        ]
    )
    report = dsg.assemble(draft, ready_kpis=[kpi], context=_context())
    assert [c.title for c in report.suggestion.charts] == ["Now", "Trend"]
    assert any("proposed twice" in reason for reason in report.dropped)


# ---------------------------------------------------------------------------
# assemble -- query charts
# ---------------------------------------------------------------------------


def test_assemble_runs_a_query_chart_through_check_with_the_context_tables():
    check = _checking()
    draft = _draft(charts=[_query_chart()])
    report = dsg.assemble(
        draft,
        ready_kpis=[_kpi()],
        context=_context(),
        check=check,
        preview=_previewing(),
    )
    assert check.calls[0][1] == frozenset({"stock_level"})
    chart = report.suggestion.charts[0]
    assert chart.source is wire.TileSource.query
    assert chart.kpi_identifier is None
    assert chart.guard.status is wire.QueryGuardStatus.ok
    assert chart.query.source_tables == ["stock_level"]
    assert chart.query.x.column == "region"
    assert chart.query.y[0].column == "hours"
    assert chart.preview.columns == ["region", "hours"]


def test_assemble_stores_the_guards_rewrite_not_the_models_text():
    check = _checking(
        status="rewritten", detail="Added LIMIT 1000.", sql="SELECT 1 LIMIT 1000"
    )
    report = dsg.assemble(
        _draft(charts=[_query_chart()]),
        ready_kpis=[_kpi()],
        context=_context(),
        check=check,
        preview=_previewing(columns=("?column?",)),
    )
    chart = report.suggestion.charts[0]
    assert chart.guard.status is wire.QueryGuardStatus.rewritten
    assert chart.guard.detail == "Added LIMIT 1000."
    assert chart.query.sql == "SELECT 1 LIMIT 1000"


def test_assemble_keeps_a_refused_query_chart_as_a_stub_rather_than_dropping_it():
    check = _checking(
        status="refused",
        detail="Not permitted to read shipments.",
        tables=("shipments",),
    )
    previews = []

    def _preview(sql):
        previews.append(sql)
        raise AssertionError("a refused statement must never be previewed")

    report = dsg.assemble(
        _draft(charts=[_query_chart()]),
        ready_kpis=[_kpi()],
        context=_context(),
        check=check,
        preview=_preview,
    )
    chart = report.suggestion.charts[0]
    assert chart.guard.status is wire.QueryGuardStatus.refused
    assert "shipments" in chart.guard.detail
    assert chart.preview is None
    assert chart.query.sql.startswith("SELECT region")
    assert previews == []
    assert report.dropped == []


def test_assemble_marks_a_query_the_warehouse_itself_refused():
    report = dsg.assemble(
        _draft(charts=[_query_chart()]),
        ready_kpis=[_kpi()],
        context=_context(),
        check=_checking(),
        preview=_previewing(columns=(), error='column "hours" does not exist'),
    )
    chart = report.suggestion.charts[0]
    assert chart.guard.status is wire.QueryGuardStatus.refused
    assert "does not exist" in chart.guard.detail


def test_assemble_drops_a_query_chart_with_no_query():
    report = dsg.assemble(
        _draft(charts=[_query_chart(query=None)]),
        ready_kpis=[_kpi()],
        context=_context(),
        check=_checking(),
    )
    assert report.suggestion.charts == []
    assert "no query" in report.dropped[0]


def test_assemble_reconciles_axes_against_the_previewed_columns():
    """An axis naming a column the result lacks would render nothing: it is dropped and
    noted, and when no y axis survives the last real column stands in."""
    chart = _query_chart(
        query=dsg.ChartQueryDraft(
            sql="SELECT region, sum(stockout_flag) AS stockout_hours FROM silver.stock_level GROUP BY 1 LIMIT 10",
            x=dsg.AxisDraft(column="region"),
            y=[dsg.AxisDraft(column="hours")],
            series_by="store",
        )
    )
    report = dsg.assemble(
        _draft(charts=[chart]),
        ready_kpis=[_kpi()],
        context=_context(),
        check=_checking(),
        preview=_previewing(columns=("region", "stockout_hours")),
    )
    query = report.suggestion.charts[0].query
    assert query.x.column == "region"
    assert [axis.column for axis in query.y] == ["stockout_hours"]
    assert query.series_by is None
    assert "y axis 'hours' is not a result column" in report.suggestion.notes
    assert "series_by 'store'" in report.suggestion.notes


def test_assemble_without_a_check_takes_the_statement_as_is():
    """The unit-test / fixture-only path: no guard, tables read by the same scan
    `KpiDefinition` uses."""
    report = dsg.assemble(
        _draft(charts=[_query_chart()]), ready_kpis=[_kpi()], context=_context()
    )
    chart = report.suggestion.charts[0]
    assert chart.guard.status is wire.QueryGuardStatus.ok
    assert chart.query.source_tables == ["stock_level"]
    assert chart.preview is None


# ---------------------------------------------------------------------------
# assemble -- name, filter, notes
# ---------------------------------------------------------------------------


def test_assemble_always_leaves_filters_empty():
    """A filter is now a chosen `(table, column)` (R13.1), which this agent has no basis to
    pick -- `filters` is always `[]` regardless of what `business_area` the model proposed, a
    person adds filter columns afterward in the dashboard builder."""
    report = dsg.assemble(
        _draft(business_area="supply_chain"), ready_kpis=[_kpi()], context=_context()
    )
    assert report.suggestion.filters == []


def test_assemble_leaves_filters_empty_when_no_business_area_is_proposed():
    report = dsg.assemble(_draft(business_area=None), ready_kpis=[_kpi()])
    assert report.suggestion.filters == []


def test_assemble_notes_an_unrecognized_business_area_guess():
    """R13.1 retired the business-area filter, but the diagnostic survives: a person reading
    the suggestion's notes should still see when the model's own guess doesn't match the
    deployment's vocabulary (round-2 review, nit) -- even though nothing consumes the guess
    itself as a filter any more."""
    report = dsg.assemble(
        _draft(business_area="not-a-real-area"), ready_kpis=[_kpi()], context=_context()
    )
    assert report.suggestion.filters == []
    assert "not-a-real-area" in report.suggestion.notes


def test_assemble_does_not_note_a_recognized_business_area_guess():
    report = dsg.assemble(
        _draft(business_area="supply_chain"), ready_kpis=[_kpi()], context=_context()
    )
    assert report.suggestion.filters == []
    assert report.suggestion.notes == ""


def test_assemble_falls_back_to_a_default_name_when_the_draft_names_nothing():
    report = dsg.assemble(_draft(name="   "), ready_kpis=[_kpi()])
    assert report.suggestion.name == "Untitled dashboard"


def test_assemble_returns_the_generated_wire_type():
    report = dsg.assemble(_draft(), ready_kpis=[_kpi()])
    assert isinstance(report.suggestion, wire.DashboardSuggestion)
    # Round-trips as JSON the way the run row stores it.
    wire.DashboardSuggestion.model_validate(report.suggestion.model_dump(mode="json"))


@pytest.mark.anyio
async def test_aassemble_agrees_with_its_sync_twin():
    kpi = _kpi()
    draft = _draft(charts=[_kpi_chart(), _query_chart()])
    sync_report = dsg.assemble(
        draft, ready_kpis=[kpi], context=_context(), check=_checking()
    )
    async_report = await dsg.aassemble(
        draft, ready_kpis=[kpi], context=_context(), check=_checking()
    )
    assert sync_report.suggestion == async_report.suggestion
    assert sync_report.dropped == async_report.dropped


def test_summarise_drafts_counts_each_source():
    assert dsg.summarise_drafts([_kpi_chart(), _query_chart(), _query_chart()]) == {
        "kpi": 1,
        "query": 2,
    }


# ---------------------------------------------------------------------------
# Round-1 review regressions
# ---------------------------------------------------------------------------


def test_assemble_drops_a_kpi_chart_naming_a_ready_kpi_outside_the_selected_context():
    """Finding #6 (major): `by_identifier` used to be built from the whole `ready_kpis`
    argument, not `context.kpis` -- the narrower, caller-selected subset the prompt actually
    rendered. A chart naming any ready KPI anywhere in the deployment, never shown to the
    model and never selected by the caller, must not pass validation."""
    selected = _kpi()
    outside_context = _kpi(
        identifier="unrelated__other_metric", name="Unrelated metric"
    )
    draft = _draft(
        charts=[
            _kpi_chart(kpi_identifier="unrelated__other_metric", title="Sneaked in")
        ]
    )
    # Both KPIs are genuinely `ready` (in `ready_kpis`); only `selected` was ever shown to the
    # model (`context.kpis`).
    report = dsg.assemble(
        draft,
        ready_kpis=[selected, outside_context],
        context=_context(kpis=(selected,)),
    )
    assert report.suggestion.charts == []
    assert "Sneaked in" in report.dropped[0]
    assert "not among the metrics selected for this run" in report.dropped[0]


def test_assemble_truncates_charts_beyond_the_context_budget():
    """Finding #7 (major): `chart_budget` was prompt-text advisory only -- nothing capped how
    many drafted charts were actually checked/previewed. The excess must be dropped before
    `check`/`preview` ever run on it (never checked, never previewed), and noted."""
    check_calls = []

    def _counting_check(sql, tables):
        check_calls.append(sql)
        return _Verdict("ok", "", sql)

    charts = [
        _query_chart(
            title=f"chart {i}",
            query=dsg.ChartQueryDraft(
                sql=f"SELECT {i} AS n FROM silver.stock_level LIMIT 1",
            ),
        )
        for i in range(5)
    ]
    report = dsg.assemble(
        _draft(charts=charts),
        ready_kpis=[_kpi()],
        context=_context(chart_budget=2),
        check=_counting_check,
    )
    assert len(report.suggestion.charts) == 2
    assert len(check_calls) == 2, "an over-budget chart must never reach check()"
    assert "proposed 5 charts, above the budget of 2" in report.suggestion.notes
    assert "3 were dropped" in report.suggestion.notes
