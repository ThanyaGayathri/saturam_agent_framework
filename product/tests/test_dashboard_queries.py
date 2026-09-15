"""Unit tests for the `query` tile half of dashboards (contract 1.2.0), DB-free: the domain
contract's own validation, the JSONB round trip, the guard-and-rewrite rules in
`dashboards/queries.py` over a fake allow-list, the cached-result-to-`TileData` mapping, and
the per-tile routes over a fake store. The real guard is the framework's `SQLGuard`; only
its allow-list is faked here, the way `test_r6_sql_guard*.py` fakes it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from egpt.guardrails.sql_guard import SQLGuard
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from egpt_retail.api import models as wire
from egpt_retail.api.routers import dashboards as dashboards_module
from egpt_retail.contracts.dashboards import (
    AxisKind,
    ChartAxis,
    ChartKind,
    Dashboard,
    Tile,
    TilePosition,
    TileQuery,
    TileSource,
)
from egpt_retail.dashboards.data import query_tile_data
from egpt_retail.dashboards.queries import check_query
from egpt_retail.dashboards.query_results import QueryResultRecord
from egpt_retail.dashboards.store import _tile_from_json, _tile_to_json


class _Allowlist:
    def __init__(self, *tables):
        self._tables = frozenset(tables)

    def allowed_tables(self):
        return self._tables

    async def aallowed_tables(self):
        return self._tables


def _guard(*tables, row_cap=1000):
    return SQLGuard(
        allowlist=_Allowlist(
            *(tables or ("orders", "products", "stock_level", "customers"))
        ),
        row_cap=row_cap,
        schema={"silver", "gold"},
    )


_PERSONAL = {
    "orders": frozenset({"customer_email"}),
    "customers": frozenset({"email", "phone"}),
}


def _personal(tables):
    return {t: _PERSONAL.get(t, frozenset()) for t in tables}


def _query(**overrides) -> TileQuery:
    defaults = dict(
        sql="SELECT wk, stockouts FROM silver.stock_level LIMIT 100",
        source_tables=frozenset({"stock_level"}),
        x=ChartAxis(column="wk", kind=AxisKind.TIME),
        y=(ChartAxis(column="stockouts", unit="count"),),
    )
    defaults.update(overrides)
    return TileQuery(**defaults)


def _query_tile(**overrides) -> Tile:
    defaults = dict(
        id="tl_q1",
        kpi_identifier=None,
        chart=ChartKind.LINE,
        position=TilePosition(x=0, y=0, w=6, h=2),
        title="Stock-outs by week",
        source=TileSource.QUERY,
        query=_query(),
        description="Inventory rows with zero on hand, per week.",
    )
    defaults.update(overrides)
    return Tile(**defaults)


# ---------------------------------------------------------------------------
# Domain contract
# ---------------------------------------------------------------------------


def test_tile_query_requires_every_read_table_to_be_declared():
    with pytest.raises(ValueError, match="source_tables does not list"):
        TileQuery(
            sql="SELECT 1 FROM silver.orders o JOIN silver.products p ON p.sku = o.sku",
            source_tables=frozenset({"orders"}),
        )


def test_tile_query_lower_cases_source_tables_and_refuses_empty_sql():
    query = TileQuery(sql="SELECT 1 FROM Orders", source_tables=frozenset({"ORDERS"}))
    assert query.source_tables == frozenset({"orders"})
    with pytest.raises(ValueError, match="sql is empty"):
        TileQuery(sql="   ", source_tables=frozenset({"orders"}))


def test_a_query_tile_needs_a_query_and_no_kpi_and_a_kpi_tile_the_reverse():
    with pytest.raises(ValueError, match="must carry a query"):
        _query_tile(query=None)
    with pytest.raises(ValueError, match="does not name a kpi_identifier"):
        _query_tile(kpi_identifier="some__kpi")
    with pytest.raises(ValueError, match="must name a kpi_identifier"):
        Tile(
            id="tl_k",
            kpi_identifier=None,
            chart=ChartKind.NUMBER,
            position=TilePosition(x=0, y=0, w=3, h=2),
        )
    with pytest.raises(ValueError, match="carries no query"):
        Tile(
            id="tl_k",
            kpi_identifier="some__kpi",
            chart=ChartKind.NUMBER,
            position=TilePosition(x=0, y=0, w=3, h=2),
            query=_query(),
        )


def test_a_kpi_tile_still_reads_back_from_a_row_written_before_the_contract_change():
    """A JSONB tile with no `source`/`query`/`description` is a 1.1.x kpi tile."""
    tile = _tile_from_json(
        {
            "id": "tl_1",
            "kpi_identifier": "supplier__on_time_rate_30d",
            "chart": "number",
            "position": {"x": 0, "y": 0, "w": 3, "h": 2},
            "title": "On-time",
            "grain_value": None,
        }
    )
    assert tile.source is TileSource.KPI
    assert tile.query is None
    assert tile.description == ""


def test_query_tile_json_round_trips():
    tile = _query_tile(rationale="why")
    assert _tile_from_json(_tile_to_json(tile)) == tile
    payload = _tile_to_json(tile)
    assert payload["source"] == "query"
    assert payload["kpi_identifier"] is None
    assert payload["query"]["source_tables"] == ["stock_level"]
    assert payload["query"]["x"] == {
        "column": "wk",
        "kind": "time",
        "label": "",
        "unit": "",
    }


# ---------------------------------------------------------------------------
# check_query -- the guard, then scope, personal data, row cap
# ---------------------------------------------------------------------------


def test_check_query_passes_a_clean_statement_unchanged():
    sql = (
        "SELECT category, sum(total) AS revenue FROM silver.orders GROUP BY 1 LIMIT 10"
    )
    verdict = check_query(sql, guard=_guard(), personal_data=_personal)
    assert verdict.status == "ok"
    assert verdict.sql == sql
    assert verdict.tables == ("orders",)


def test_check_query_adds_a_missing_limit_at_the_row_cap():
    verdict = check_query(
        "SELECT category, sum(total) AS revenue FROM silver.orders GROUP BY 1",
        guard=_guard(row_cap=500),
        personal_data=_personal,
    )
    assert verdict.status == "rewritten"
    assert "Added LIMIT 500" in verdict.detail
    assert verdict.sql.endswith("LIMIT 500")


def test_check_query_lowers_a_limit_above_the_row_cap():
    verdict = check_query(
        "SELECT category FROM silver.orders LIMIT 5000",
        guard=_guard(row_cap=1000),
        personal_data=_personal,
    )
    assert verdict.status == "rewritten"
    assert "Lowered LIMIT 5000 to 1000" in verdict.detail
    assert verdict.sql.endswith("LIMIT 1000")


def test_check_query_drops_a_personal_data_column_from_the_select_list():
    verdict = check_query(
        "SELECT o.customer_email, sum(total) AS revenue FROM silver.orders o GROUP BY 1 LIMIT 10",
        guard=_guard(),
        personal_data=_personal,
    )
    assert verdict.status == "rewritten"
    assert "orders.customer_email" in verdict.detail
    assert "customer_email" not in verdict.sql
    assert "SUM(total)" in verdict.sql


def test_check_query_refuses_when_only_personal_data_would_be_left():
    verdict = check_query(
        "SELECT customer_email FROM silver.orders LIMIT 10",
        guard=_guard(),
        personal_data=_personal,
    )
    assert verdict.status == "refused"
    assert "nothing would be left" in verdict.detail


def test_check_query_refuses_personal_data_outside_the_select_list():
    verdict = check_query(
        "SELECT sum(total) FROM silver.orders WHERE customer_email LIKE '%@x.com' LIMIT 1",
        guard=_guard(),
        personal_data=_personal,
    )
    assert verdict.status == "refused"
    assert "outside the SELECT list" in verdict.detail


def test_check_query_refuses_personal_data_hidden_in_a_cte():
    verdict = check_query(
        "WITH c AS (SELECT email AS e FROM silver.customers) SELECT e FROM c LIMIT 10",
        guard=_guard(),
        personal_data=_personal,
    )
    assert verdict.status == "refused"
    assert "customers.email" in verdict.detail


def test_check_query_refuses_select_star_over_a_table_with_personal_data_only():
    refused = check_query(
        "SELECT * FROM silver.orders LIMIT 10", guard=_guard(), personal_data=_personal
    )
    assert refused.status == "refused"
    assert "SELECT *" in refused.detail
    allowed = check_query(
        "SELECT * FROM silver.products LIMIT 10",
        guard=_guard(),
        personal_data=_personal,
    )
    assert allowed.status == "ok"


def test_check_query_refuses_a_table_outside_the_context_even_when_the_layer_allows_it():
    verdict = check_query(
        "SELECT category FROM silver.orders o JOIN silver.products p ON p.sku = o.sku LIMIT 5",
        guard=_guard(),
        context_tables=frozenset({"orders"}),
        personal_data=_personal,
    )
    assert verdict.status == "refused"
    assert "products" in verdict.detail
    assert "not among the tables selected" in verdict.detail


def test_check_query_passes_the_guards_own_refusals_through():
    outside = check_query("SELECT count(*) FROM silver.shipments", guard=_guard())
    assert outside.status == "refused"
    assert "shipments" in outside.detail
    write = check_query("DELETE FROM silver.orders", guard=_guard())
    assert write.status == "refused"
    assert "DELETE" in write.detail


# ---------------------------------------------------------------------------
# query_tile_data -- cached rows through the tile's axes
# ---------------------------------------------------------------------------


def _record(rows, columns=("wk", "stockouts"), error=None) -> QueryResultRecord:
    return QueryResultRecord(
        dashboard_id="dsh_1",
        tile_id="tl_q1",
        computed_at=datetime(2026, 9, 7, 2, 31, tzinfo=timezone.utc),
        columns=tuple(columns),
        rows=tuple(rows),
        duration_ms=12,
        error=error,
    )


def test_query_tile_data_with_no_cached_row_is_never_computed_not_broken():
    data = query_tile_data(_query_tile(), None)
    assert data.status is wire.KpiHealthStatus.never_computed
    assert data.kpi_identifier is None
    assert data.value is None and data.rows == [] and data.series == []


def test_query_tile_data_with_a_cached_error_reports_failed():
    data = query_tile_data(_query_tile(), _record([], error="guard refused: ..."))
    assert data.status is wire.KpiHealthStatus.failed
    assert data.computed_at is not None


def test_query_tile_data_plots_a_time_series_and_a_delta_from_the_last_two_points():
    rows = [
        {"wk": "2026-06-29T00:00:00", "stockouts": 34},
        {"wk": "2026-06-22T00:00:00", "stockouts": 31},
        {"wk": "2026-07-06", "stockouts": 40},
    ]
    data = query_tile_data(_query_tile(), _record(rows))
    assert data.status is wire.KpiHealthStatus.fresh
    assert [p.value for p in data.series] == [31.0, 34.0, 40.0]
    assert data.series[0].at.tzinfo is not None
    assert data.value == 40.0
    assert data.delta == 6.0
    assert data.unit == "count"
    assert data.columns == ["wk", "stockouts"]
    assert data.rows == rows


def test_query_tile_data_number_chart_takes_the_first_rows_y_column():
    tile = _query_tile(
        chart=ChartKind.NUMBER,
        query=_query(
            sql="SELECT count(*) AS late FROM silver.stock_level LIMIT 1",
            x=None,
            y=(ChartAxis(column="late", unit="count"),),
        ),
    )
    data = query_tile_data(tile, _record([{"late": 37}], columns=("late",)))
    assert data.value == 37.0
    assert data.series == []
    assert data.delta is None


def test_query_tile_data_falls_back_to_the_last_column_when_no_y_axis_matches():
    tile = _query_tile(
        chart=ChartKind.BAR,
        query=_query(x=ChartAxis(column="category"), y=(ChartAxis(column="missing"),)),
    )
    rows = [{"category": "Dairy", "margin_lost": 42300.0}]
    data = query_tile_data(tile, _record(rows, columns=("category", "margin_lost")))
    assert data.value == 42300.0
    assert data.series == []  # a category x axis is not a time series


# ---------------------------------------------------------------------------
# Per-tile routes over a fake store (no Postgres, no guard -- description-only edits and
# removals never reach either)
# ---------------------------------------------------------------------------


class _FakeDashboardStore:
    def __init__(self, dashboard):
        self.dashboards = {dashboard.id: dashboard}

    def get(self, dashboard_id):
        return self.dashboards.get(dashboard_id)

    def upsert_tile(self, dashboard_id, owner, tile):
        current = self.dashboards[dashboard_id]
        tiles = list(current.tiles)
        for i, existing in enumerate(tiles):
            if existing.id == tile.id:
                tiles[i] = tile
                break
        else:
            tiles.append(tile)
        updated = Dashboard(
            **{**current.__dict__, "tiles": tuple(tiles)},
        )
        self.dashboards[dashboard_id] = updated
        return updated

    def remove_tile(self, dashboard_id, owner, tile_id):
        current = self.dashboards[dashboard_id]
        if current.tile(tile_id) is None:
            return None
        updated = Dashboard(
            **{
                **current.__dict__,
                "tiles": tuple(t for t in current.tiles if t.id != tile_id),
            },
        )
        self.dashboards[dashboard_id] = updated
        return updated


def _dashboard(*tiles) -> Dashboard:
    return Dashboard(
        id="dsh_1",
        name="Northern region",
        owner="local",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        tiles=tuple(tiles),
    )


def _client(monkeypatch, store):
    monkeypatch.setattr(
        dashboards_module, "_compute_query_tiles", lambda *a, **kw: None
    )

    class _NoResults:
        def delete(self, *a):
            return 0

    from contextlib import contextmanager

    @contextmanager
    def _no_results(settings):
        yield _NoResults()

    monkeypatch.setattr(dashboards_module, "_open_query_results", _no_results)

    app = FastAPI()

    @app.middleware("http")
    async def _fake_auth(request: Request, call_next):
        request.state.authorization_enabled = False
        return await call_next(request)

    app.include_router(dashboards_module.router)
    app.dependency_overrides[dashboards_module._dashboard_store] = lambda: store
    return TestClient(app)


def test_patch_tile_description_only_keeps_the_id_and_touches_nothing_else(monkeypatch):
    tile = _query_tile()
    store = _FakeDashboardStore(_dashboard(tile))
    with _client(monkeypatch, store) as client:
        response = client.patch(
            "/dashboards/dsh_1/tiles/tl_q1",
            json={"description": "Edited under the chart."},
        )
    assert response.status_code == 200
    tiles = response.json()["tiles"]
    assert [t["id"] for t in tiles] == ["tl_q1"]
    assert tiles[0]["description"] == "Edited under the chart."
    assert tiles[0]["query"]["sql"] == tile.query.sql
    assert tiles[0]["chart"] == "line"


def test_patch_tile_chart_kind_changes_only_the_kind(monkeypatch):
    store = _FakeDashboardStore(_dashboard(_query_tile()))
    with _client(monkeypatch, store) as client:
        response = client.patch("/dashboards/dsh_1/tiles/tl_q1", json={"chart": "bar"})
    assert response.status_code == 200
    assert response.json()["tiles"][0]["chart"] == "bar"


def test_patch_tile_404s_for_an_unknown_tile(monkeypatch):
    store = _FakeDashboardStore(_dashboard(_query_tile()))
    with _client(monkeypatch, store) as client:
        response = client.patch("/dashboards/dsh_1/tiles/tl_nope", json={"title": "x"})
    assert response.status_code == 404


def test_delete_tile_removes_it_and_404s_the_second_time(monkeypatch):
    store = _FakeDashboardStore(_dashboard(_query_tile(), _query_tile(id="tl_q2")))
    with _client(monkeypatch, store) as client:
        first = client.delete("/dashboards/dsh_1/tiles/tl_q1")
        second = client.delete("/dashboards/dsh_1/tiles/tl_q1")
    assert first.status_code == 204
    assert second.status_code == 404
    assert [t.id for t in store.dashboards["dsh_1"].tiles] == ["tl_q2"]


def test_add_tile_409s_when_the_id_is_already_taken(monkeypatch):
    store = _FakeDashboardStore(_dashboard(_query_tile()))
    with _client(monkeypatch, store) as client:
        response = client.post(
            "/dashboards/dsh_1/tiles",
            json={
                "id": "tl_q1",
                "source": "kpi",
                "kpi_identifier": "some__kpi",
                "chart": "number",
                "position": {"x": 0, "y": 0, "w": 3, "h": 2},
            },
        )
    assert response.status_code == 409


def test_tile_write_for_a_query_tile_derives_source_tables_from_the_sql():
    write = wire.TileWrite(
        source=wire.TileSource.query,
        query=wire.TileQuery(
            sql="SELECT category, sum(total) AS revenue FROM silver.orders o JOIN silver.products p ON p.sku = o.sku GROUP BY 1",
            source_tables=["orders"],  # a client under-declaring is ignored
        ),
        chart=wire.ChartKind.bar,
        position=wire.TilePosition(x=0, y=0, w=6, h=2),
        title="Revenue by category",
    )
    tile = dashboards_module._tile_from_write(write)
    assert tile.source is TileSource.QUERY
    assert tile.kpi_identifier is None
    assert tile.query.source_tables == frozenset({"orders", "products"})
    assert tile.id.startswith("tl_")


def test_tile_write_keeps_a_supplied_id():
    write = wire.TileWrite(
        id="tl_keep",
        kpi_identifier="some__kpi",
        chart=wire.ChartKind.number,
        position=wire.TilePosition(x=0, y=0, w=3, h=2),
    )
    assert dashboards_module._tile_from_write(write).id == "tl_keep"


def test_patching_a_kpi_tile_with_a_query_detaches_it_into_a_query_tile():
    kpi_tile = Tile(
        id="tl_k",
        kpi_identifier="some__kpi",
        chart=ChartKind.NUMBER,
        position=TilePosition(x=0, y=0, w=3, h=2),
    )
    patched, changed = dashboards_module._patched_tile(
        kpi_tile,
        wire.TilePatch(
            query=wire.TileQuery(sql="SELECT count(*) FROM silver.orders LIMIT 1")
        ),
    )
    assert changed
    assert patched.source is TileSource.QUERY
    assert patched.kpi_identifier is None
    assert patched.query.source_tables == frozenset({"orders"})
