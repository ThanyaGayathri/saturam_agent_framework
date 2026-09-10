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
from egpt_retail.dashboards.data import _row_for_grain, _status_for_missing, _tile_data
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
