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


def test_refresh_schedule_carries_every_cadence_the_interface_offers():
    """Contract 1.1.4 (#226): `daily` and `weekly` join `nightly`/`hourly`/`manual`, and the
    domain enum and the wire enum must agree value for value -- the router converts between
    them by `.value`, so a value present on one side only would 500 on the way out."""
    from egpt_retail.api import models as wire
    from egpt_retail.contracts.dashboards import RefreshSchedule

    expected = {"nightly", "daily", "weekly", "hourly", "manual"}
    assert {s.value for s in RefreshSchedule} == expected
    assert {s.value for s in wire.RefreshSchedule} == expected
    for domain_value in RefreshSchedule:
        assert wire.RefreshSchedule(domain_value.value).value == domain_value.value


def test_dashboard_out_round_trips_the_new_cadences():
    from egpt_retail.api.routers.dashboards import _dashboard_out
    from egpt_retail.contracts.dashboards import RefreshSchedule

    for cadence in (RefreshSchedule.DAILY, RefreshSchedule.WEEKLY):
        dashboard = Dashboard(
            id="dsh_1",
            name="d",
            owner="alice",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            refresh_schedule=cadence,
        )
        assert _dashboard_out(dashboard).refresh_schedule.value == cadence.value


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
