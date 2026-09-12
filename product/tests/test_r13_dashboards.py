"""Unit tests for R13's DB-free halves: the dashboard contracts' own validation, the JSONB
round-trip helpers in dashboards/store.py, and dashboards/data.py's gold-result-to-TileData
mapping (given a fake read_gold output, no warehouse or Postgres involved). The store's CRUD,
the readiness gate against a real KPI registry, and a live read_gold call are exercised only
by the cookbook (cookbook/94_retail/R13_dashboards.py) and by
api/routers/dashboards.py's DashboardValidationError paths that need Postgres/Neo4j.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from egpt_retail.contracts.dashboards import (
    ChartKind,
    Dashboard,
    DashboardFilter,
    Tile,
    TilePosition,
    Visibility,
)
from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.dashboards.data import (
    SERIES_LOOKBACK_DAYS,
    _row_for_grain,
    _status_for_missing,
    _tile_data,
    read_dashboard_data,
)
from egpt_retail.dashboards.store import (
    UNSET,
    _filter_from_json,
    _filter_to_json,
    _tile_from_json,
    _tile_to_json,
)


def _tile(**overrides) -> Tile:
    defaults = dict(
        id="tl_1",
        kpi_identifier="supplier__on_time_rate_30d",
        chart=ChartKind.NUMBER,
        position=TilePosition(x=0, y=0, w=3, h=2),
    )
    defaults.update(overrides)
    return Tile(**defaults)


# ---------------------------------------------------------------------------
# Domain contracts
# ---------------------------------------------------------------------------


def test_tile_position_rejects_negative_origin():
    with pytest.raises(ValueError):
        TilePosition(x=-1, y=0, w=3, h=2)


def test_tile_position_rejects_zero_size():
    with pytest.raises(ValueError):
        TilePosition(x=0, y=0, w=0, h=2)


def test_dashboard_visible_to_owner_always():
    dashboard = Dashboard(
        id="dsh_1",
        name="d",
        owner="alice",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        visibility=Visibility.PRIVATE,
    )
    assert dashboard.visible_to("alice")
    assert not dashboard.visible_to("bob")


def test_dashboard_visible_to_team_and_everyone():
    for visibility in (Visibility.TEAM, Visibility.EVERYONE):
        dashboard = Dashboard(
            id="dsh_1",
            name="d",
            owner="alice",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            visibility=visibility,
        )
        assert dashboard.visible_to("bob")


# ---------------------------------------------------------------------------
# JSONB round-trip helpers -- what the store persists and reads back
# ---------------------------------------------------------------------------


def test_tile_json_round_trips():
    tile = _tile(grain_value="sup_014", title="On-time rate")
    rebuilt = _tile_from_json(_tile_to_json(tile))
    assert rebuilt == tile


def test_filter_json_round_trips():
    item = DashboardFilter(field="business_area", value="supply_chain")
    assert _filter_from_json(_filter_to_json(item)) == item


def test_unset_sentinel_is_not_equal_to_any_real_value():
    assert UNSET is not None
    assert UNSET != []
    assert UNSET != ""


# ---------------------------------------------------------------------------
# dashboards/data.py -- the read_gold-result-to-TileData mapping
# ---------------------------------------------------------------------------


def test_status_for_missing_maps_reasons_honestly():
    assert _status_for_missing("never materialised").value == "never_computed"
    assert (
        _status_for_missing("no gold row at as_of 2026-09-06").value == "never_computed"
    )
    assert _status_for_missing("guard refused: not allowlisted").value == "failed"
    assert _status_for_missing("relation does not exist").value == "failed"


def test_row_for_grain_finds_the_matching_dimension_row():
    rows = [
        {"as_of": "2026-09-06", "value": "0.71", "supplier_id": "sup_014"},
        {"as_of": "2026-09-06", "value": "0.88", "supplier_id": "sup_003"},
    ]
    found = _row_for_grain(rows, "sup_003")
    assert found["value"] == "0.88"
    assert _row_for_grain(rows, "sup_999") is None


def test_tile_data_missing_kpi_renders_unavailable_not_broken():
    """The spec's own evidence: a missing KPI is a status, not an exception."""
    tile = _tile()
    data = _tile_data(tile, None, "never materialised")
    assert data.value is None
    assert data.status.value == "never_computed"
    assert data.rows == []


def test_tile_data_single_row_number_tile_takes_the_scalar():
    tile = _tile(chart=ChartKind.NUMBER)
    result = {
        "as_of": "2026-09-06",
        "computed_at": "2026-09-07T02:20:00Z",
        "run_id": "run_gold_1",
        "rows": [
            {
                "value": "0.91",
                "as_of": "2026-09-06",
                "computed_at": "x",
                "run_id": "run_gold_1",
            }
        ],
    }
    data = _tile_data(tile, result, "")
    assert data.value == 0.91
    assert data.status.value == "fresh"
    assert data.provenance_ref == "run_gold_1"
    assert data.rows == []


def test_tile_data_grain_scoped_tile_picks_its_own_row():
    tile = _tile(chart=ChartKind.LINE, grain_value="sup_003")
    result = {
        "as_of": "2026-09-06",
        "computed_at": "2026-09-07T02:20:00Z",
        "run_id": "run_gold_1",
        "rows": [
            {
                "value": "0.71",
                "supplier_id": "sup_014",
                "as_of": "x",
                "computed_at": "x",
                "run_id": "x",
            },
            {
                "value": "0.88",
                "supplier_id": "sup_003",
                "as_of": "x",
                "computed_at": "x",
                "run_id": "x",
            },
        ],
    }
    data = _tile_data(tile, result, "")
    assert data.value == 0.88


def test_tile_data_table_tile_with_multiple_rows_and_no_grain_lists_them():
    tile = _tile(chart=ChartKind.TABLE)
    result = {
        "as_of": "2026-09-06",
        "computed_at": "2026-09-07T02:20:00Z",
        "run_id": "run_gold_1",
        "rows": [
            {
                "value": "0.71",
                "supplier_id": "sup_014",
                "as_of": "x",
                "computed_at": "x",
                "run_id": "x",
            },
            {
                "value": "0.88",
                "supplier_id": "sup_003",
                "as_of": "x",
                "computed_at": "x",
                "run_id": "x",
            },
        ],
    }
    data = _tile_data(tile, result, "")
    assert data.value is None
    assert len(data.rows) == 2


def test_tile_data_grain_mismatch_reports_never_computed_not_fresh():
    """Round-1 review finding: a `grain_value` that matches no dimension row (a typo, or a
    supplier that dropped out of the latest materialisation) must not render as `fresh` with
    nothing to show -- that is indistinguishable from a genuinely current, correctly empty
    tile. A number/line/bar tile that resolved no value is a real gap."""
    tile = _tile(chart=ChartKind.NUMBER, grain_value="sup_999")
    result = {
        "as_of": "2026-09-06",
        "computed_at": "2026-09-07T02:20:00Z",
        "run_id": "run_gold_1",
        "rows": [
            {
                "value": "0.71",
                "supplier_id": "sup_014",
                "as_of": "x",
                "computed_at": "x",
                "run_id": "x",
            }
        ],
    }
    data = _tile_data(tile, result, "")
    assert data.status.value == "never_computed"
    assert data.value is None
    assert data.rows == []


def test_tile_data_ambiguous_multi_row_non_table_reports_never_computed():
    """No grain to disambiguate and more than one row: a number/line/bar tile still cannot
    resolve a single value, and must say so rather than reporting `fresh`."""
    tile = _tile(chart=ChartKind.LINE)  # no grain_value
    result = {
        "as_of": "2026-09-06",
        "computed_at": "2026-09-07T02:20:00Z",
        "run_id": "run_gold_1",
        "rows": [
            {
                "value": "0.71",
                "supplier_id": "sup_014",
                "as_of": "x",
                "computed_at": "x",
                "run_id": "x",
            },
            {
                "value": "0.88",
                "supplier_id": "sup_003",
                "as_of": "x",
                "computed_at": "x",
                "run_id": "x",
            },
        ],
    }
    data = _tile_data(tile, result, "")
    assert data.status.value == "never_computed"
    assert data.value is None


def test_tile_data_table_tile_with_grain_mismatch_reports_never_computed_too():
    """Round-2 review finding: the round-1 fix's guard only fired for non-TABLE charts, so a
    TABLE tile with a `grain_value` that matches no row fell through to showing the entire
    *unfiltered* listing -- indistinguishable from a TABLE tile deliberately configured with
    no grain to show everything. A TABLE tile with a grain_value that resolves nothing is a
    real gap, the same as any other chart kind."""
    tile = _tile(chart=ChartKind.TABLE, grain_value="sup_999")
    result = {
        "as_of": "2026-09-06",
        "computed_at": "2026-09-07T02:20:00Z",
        "run_id": "run_gold_1",
        "rows": [
            {
                "value": "0.71",
                "supplier_id": "sup_014",
                "as_of": "x",
                "computed_at": "x",
                "run_id": "x",
            },
            {
                "value": "0.88",
                "supplier_id": "sup_003",
                "as_of": "x",
                "computed_at": "x",
                "run_id": "x",
            },
        ],
    }
    data = _tile_data(tile, result, "")
    assert data.status.value == "never_computed"
    assert data.value is None
    assert data.rows == []


# ---------------------------------------------------------------------------
# series and delta -- from read_gold's `history` (issue #227 / E1), still DB-free
# ---------------------------------------------------------------------------


def _gold_row(value, as_of, **grain):
    return {
        "value": value,
        "as_of": as_of,
        "computed_at": "x",
        "source_snapshot": "snap",
        "run_id": f"run_{as_of}",
        **grain,
    }


def _overall_result_with_history():
    """Three nightly rows at grain `overall`; the current read is the latest of them."""
    history = [
        _gold_row("0.95", "2026-09-04"),
        _gold_row("0.93", "2026-09-05"),
        _gold_row("0.91", "2026-09-06"),
    ]
    return {
        "as_of": "2026-09-06",
        "computed_at": "2026-09-07T02:20:00Z",
        "run_id": "run_2026-09-06",
        "rows": [history[-1]],
        "history": history,
    }


def test_line_tile_gets_one_series_point_per_business_date_and_an_exact_delta():
    data = _tile_data(_tile(chart=ChartKind.LINE), _overall_result_with_history(), "")
    assert data.status.value == "fresh"
    assert data.value == 0.91
    assert [(p.at.isoformat(), p.value) for p in data.series] == [
        ("2026-09-04T23:59:59+00:00", 0.95),
        ("2026-09-05T23:59:59+00:00", 0.93),
        ("2026-09-06T23:59:59+00:00", 0.91),
    ]
    # 0.91 - 0.93 in binary float is -0.020000000000000018; gold chose `numeric` so a
    # customer never reads that, and the delta must honour the same choice.
    assert data.delta == -0.02


def test_bar_tile_plots_a_series_too():
    data = _tile_data(_tile(chart=ChartKind.BAR), _overall_result_with_history(), "")
    assert len(data.series) == 3
    assert data.delta == -0.02


def test_number_tile_gets_a_delta_but_never_a_series():
    data = _tile_data(_tile(chart=ChartKind.NUMBER), _overall_result_with_history(), "")
    assert data.value == 0.91
    assert data.delta == -0.02
    assert data.series == []


def test_table_tile_listing_everything_has_neither_series_nor_delta():
    result = {
        "as_of": "2026-09-06",
        "computed_at": "2026-09-07T02:20:00Z",
        "run_id": "r",
        "rows": [
            _gold_row("0.71", "2026-09-06", supplier_id="sup_014"),
            _gold_row("0.88", "2026-09-06", supplier_id="sup_003"),
        ],
        "history": [
            _gold_row("0.70", "2026-09-05", supplier_id="sup_014"),
            _gold_row("0.71", "2026-09-06", supplier_id="sup_014"),
            _gold_row("0.88", "2026-09-06", supplier_id="sup_003"),
        ],
    }
    data = _tile_data(_tile(chart=ChartKind.TABLE), result, "")
    assert data.value is None
    assert len(data.rows) == 2
    assert data.series == []
    assert data.delta is None


def test_grain_scoped_series_follows_the_tile_row_and_skips_a_date_without_it():
    """A supplier that had no row on one night is left out of that night's point, not plotted
    as `null` -- `null` would claim the formula returned NULL, a different fact."""
    result = {
        "as_of": "2026-09-06",
        "computed_at": "2026-09-07T02:20:00Z",
        "run_id": "r",
        "rows": [
            _gold_row("0.71", "2026-09-06", supplier_id="sup_014"),
            _gold_row("0.88", "2026-09-06", supplier_id="sup_003"),
        ],
        "history": [
            _gold_row("0.80", "2026-09-04", supplier_id="sup_003"),
            _gold_row("0.69", "2026-09-04", supplier_id="sup_014"),
            _gold_row("0.70", "2026-09-05", supplier_id="sup_014"),
            _gold_row("0.71", "2026-09-06", supplier_id="sup_014"),
            _gold_row("0.88", "2026-09-06", supplier_id="sup_003"),
        ],
    }
    data = _tile_data(_tile(chart=ChartKind.LINE, grain_value="sup_003"), result, "")
    assert data.value == 0.88
    assert [(p.at.date().isoformat(), p.value) for p in data.series] == [
        ("2026-09-04", 0.80),
        ("2026-09-06", 0.88),
    ]
    # The previous *resolved* date for sup_003 is 09-04, not 09-05 (no sup_003 row then).
    assert data.delta == 0.08


def test_a_single_materialised_date_has_no_delta_and_a_one_point_series():
    result = _overall_result_with_history()
    result["history"] = [result["history"][-1]]
    data = _tile_data(_tile(chart=ChartKind.LINE), result, "")
    assert len(data.series) == 1
    assert data.delta is None


def test_no_history_key_at_all_still_renders_the_current_value():
    """`read_gold` without `history_days` (every caller other than dashboards) hands out no
    `history`; the tile must render exactly as before, series empty and delta null."""
    result = _overall_result_with_history()
    del result["history"]
    data = _tile_data(_tile(chart=ChartKind.LINE), result, "")
    assert data.value == 0.91
    assert data.series == []
    assert data.delta is None


def test_a_null_formula_result_in_the_history_is_a_null_point_not_a_gap():
    result = _overall_result_with_history()
    result["history"][1]["value"] = None
    data = _tile_data(_tile(chart=ChartKind.LINE), result, "")
    assert [p.value for p in data.series] == [0.95, None, 0.91]
    # The previous date's value is NULL: nothing to subtract, so no delta is invented.
    assert data.delta is None


@pytest.mark.anyio
async def test_read_dashboard_data_requests_history_only_when_a_tile_can_use_it(
    monkeypatch,
):
    seen = []

    async def fake_read_gold(step_input):
        from egpt.workflow.types import StepOutput

        seen.append(dict(step_input.additional_data))
        return StepOutput(content={"kpis": {}, "missing": []}, success=True)

    monkeypatch.setattr("egpt_retail.dashboards.data.read_gold", fake_read_gold)
    now = datetime.now(timezone.utc)

    tables_only = Dashboard(
        id="d1",
        name="d",
        owner="local",
        created_at=now,
        updated_at=now,
        tiles=(_tile(chart=ChartKind.TABLE),),
    )
    await read_dashboard_data(tables_only)
    assert "history_days" not in seen[-1]

    with_a_trend = Dashboard(
        id="d2",
        name="d",
        owner="local",
        created_at=now,
        updated_at=now,
        tiles=(_tile(chart=ChartKind.TABLE), _tile(id="tl_2", chart=ChartKind.LINE)),
    )
    await read_dashboard_data(with_a_trend)
    assert seen[-1]["history_days"] == SERIES_LOOKBACK_DAYS


# ---------------------------------------------------------------------------
# tile_readiness_refusal -- R13's headline 422 guarantee, pure and DB-free
# (round-1 review: this path previously had zero coverage that CI actually runs)
# ---------------------------------------------------------------------------


def _kpi(identifier: str, tables: frozenset) -> KpiDefinition:
    return KpiDefinition(
        identifier=identifier,
        name=identifier,
        description="",
        sql=f"SELECT 1 FROM {next(iter(sorted(tables)))}" if tables else "SELECT 1",
        grain="overall",
        business_area="supply_chain",
        source_tables=tables,
    )


def test_tile_readiness_refusal_for_an_unregistered_kpi():
    from egpt_retail.api.routers.dashboards import tile_readiness_refusal

    message = tile_readiness_refusal(["missing_kpi"], [], frozenset())
    assert message is not None
    assert "not a registered KPI" in message


def test_tile_readiness_refusal_for_a_blocked_kpi():
    from egpt_retail.api.routers.dashboards import tile_readiness_refusal

    kpi = _kpi("k1", frozenset({"orders"}))
    message = tile_readiness_refusal(["k1"], [kpi], frozenset())
    assert message is not None
    assert "blocked" in message
    assert "orders" in message


def test_tile_readiness_refusal_for_a_partial_kpi():
    from egpt_retail.api.routers.dashboards import tile_readiness_refusal

    kpi = KpiDefinition(
        identifier="k1",
        name="k1",
        description="",
        sql="SELECT 1 FROM orders JOIN customers ON orders.customer_id = customers.id",
        grain="overall",
        business_area="supply_chain",
        source_tables=frozenset({"orders", "customers"}),
    )
    message = tile_readiness_refusal(["k1"], [kpi], frozenset({"orders"}))
    assert message is not None
    assert "partial" in message
    assert "customers" in message


def test_tile_readiness_refusal_is_none_when_every_tile_is_ready():
    from egpt_retail.api.routers.dashboards import tile_readiness_refusal

    kpi = _kpi("k1", frozenset({"orders"}))
    assert tile_readiness_refusal(["k1"], [kpi], frozenset({"orders"})) is None


# ---------------------------------------------------------------------------
# _owner -- round-1 review: a valid, scoped token with no `sub` claim must not
# collapse onto the shared "local" owner
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeRequest:
    def __init__(self, **state_kwargs):
        self.state = _FakeState(**state_kwargs)


def test_dashboards_owner_is_local_when_authorization_is_off():
    from egpt_retail.api.routers.dashboards import _owner

    assert _owner(_FakeRequest(authorization_enabled=False)) == "local"


def test_dashboards_owner_returns_the_authenticated_subject():
    from egpt_retail.api.routers.dashboards import _owner

    assert _owner(_FakeRequest(authorization_enabled=True, user_id="u1")) == "u1"


def test_dashboards_owner_refuses_a_scoped_token_with_no_subject_claim():
    from fastapi import HTTPException

    from egpt_retail.api.routers.dashboards import _owner

    with pytest.raises(HTTPException) as excinfo:
        _owner(_FakeRequest(authorization_enabled=True, user_id=None))
    assert excinfo.value.status_code == 401
