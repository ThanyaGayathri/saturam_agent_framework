"""Unit tests for the `query` tile half of dashboards (contract 1.2.0), DB-free: the domain
contract's own validation, the JSONB round trip, the guard-and-rewrite rules in
`dashboards/queries.py` over a fake allow-list, the cached-result-to-`TileData` mapping, and
the per-tile routes over a fake store. The real guard is the framework's `SQLGuard`; only
its allow-list is faked here, the way `test_r6_sql_guard*.py` fakes it.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
import sqlglot
from egpt.guardrails.sql_guard import SQLGuard
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from egpt_retail.api import models as wire
from egpt_retail.api.routers import dashboards as dashboards_module
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.dashboards import (
    AxisKind,
    ChartAxis,
    ChartKind,
    Dashboard,
    DashboardFilterColumn,
    RefreshSchedule,
    Tile,
    TilePosition,
    TileQuery,
    TileSource,
    Visibility,
)
from egpt_retail.contracts.semantic import Column, Table, TableRef
from egpt_retail.dashboards import queries as queries_module
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


def test_check_query_refuses_select_star_hidden_inside_a_cte():
    """Round-1 review, finding #1 (critical): `_alias_map` never resolved a CTE alias back to
    its real table, so `WITH s AS (SELECT * FROM silver.customers) SELECT s.email FROM s`
    passed with `customer.email` never attributed to `customers` -- reproduced verbatim from
    the review and confirmed refused now that the star check scans the whole tree rather than
    only the outer projection."""
    verdict = check_query(
        "WITH s AS (SELECT * FROM silver.customers) SELECT s.email FROM s LIMIT 10",
        guard=_guard(),
        personal_data=_personal,
    )
    assert verdict.status == "refused"
    assert "selects `*`" in verdict.detail
    assert "customers.email" in verdict.detail


def test_check_query_refuses_select_star_hidden_inside_a_derived_table():
    """The subquery-in-FROM variant of the same bypass, also reproduced verbatim."""
    verdict = check_query(
        "SELECT sub.email FROM (SELECT * FROM silver.customers) sub LIMIT 10",
        guard=_guard(),
        personal_data=_personal,
    )
    assert verdict.status == "refused"
    assert "selects `*`" in verdict.detail


def test_check_query_still_allows_select_star_over_a_table_with_no_personal_data_anywhere():
    """The blanket star refusal must not fire on a query that reads no personal data at all,
    including through a CTE."""
    verdict = check_query(
        "WITH p AS (SELECT * FROM silver.products) SELECT * FROM p LIMIT 10",
        guard=_guard(),
        personal_data=_personal,
    )
    assert verdict.status == "ok"


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
    assert "selects `*`" in refused.detail
    assert "orders.customer_email" in refused.detail
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

    def create(
        self,
        *,
        name,
        owner,
        description="",
        tiles=(),
        filters=(),
        refresh_schedule=RefreshSchedule.NIGHTLY,
        visibility=Visibility.PRIVATE,
    ):
        dashboard_id = f"dsh_{len(self.dashboards) + 1}"
        dashboard = Dashboard(
            id=dashboard_id,
            name=name,
            owner=owner,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            description=description,
            tiles=tuple(tiles),
            filters=tuple(filters),
            refresh_schedule=refresh_schedule,
            visibility=visibility,
        )
        self.dashboards[dashboard_id] = dashboard
        return dashboard

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


def _dashboard(*tiles, filters=()) -> Dashboard:
    return Dashboard(
        id="dsh_1",
        name="Northern region",
        owner="local",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        tiles=tuple(tiles),
        filters=tuple(filters),
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


def test_add_tile_twice_with_no_client_id_mints_two_distinct_ids_each_time(monkeypatch):
    """Round-2 review, finding #1 (major): the frontend's "add a chart by prompt" fix used to
    send a hardcoded placeholder id on every such add, which -- because `_tile_from_write`'s
    `write.id or f"tl_{...}"` only mints a fresh id when the client sent none -- persisted
    literally and 409'd the *second* add to the same dashboard. The frontend fix is to never
    send an `id` at all for a prompt-added chart; this is the server-side half of that
    invariant: two consecutive adds with no client id must each get their own real id, never
    collide, and never 409."""
    # DB-free: the guard itself (a real semantic reader, a real warehouse) is exercised by
    # `check_query`'s own unit tests above; this test is only about id-minting behaviour.
    monkeypatch.setattr(
        dashboards_module,
        "_check_query_tiles",
        lambda tiles, settings, only=None: list(tiles),
    )
    store = _FakeDashboardStore(_dashboard())
    body = {
        "source": "query",
        "query": {"sql": "SELECT count(*) AS n FROM silver.stock_level LIMIT 1"},
        "chart": "number",
        "position": {"x": 0, "y": 0, "w": 3, "h": 2},
        "title": "First",
    }
    with _client(monkeypatch, store) as client:
        first = client.post("/dashboards/dsh_1/tiles", json=body)
        second = client.post(
            "/dashboards/dsh_1/tiles", json={**body, "title": "Second"}
        )

    assert first.status_code == 201 and second.status_code == 201
    ids = [t["id"] for t in second.json()["tiles"]]
    assert len(ids) == 2
    assert len(set(ids)) == 2, "each no-id add must mint its own id, never reuse one"
    assert all(i and i != "__new__" for i in ids)


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


# ---------------------------------------------------------------------------
# Router-level fixes: duplicate tile ids, scoped recompute (round-1 review, #8/#11)
# ---------------------------------------------------------------------------


def test_reject_duplicate_tile_ids_raises_on_a_repeated_id():
    tiles = [
        _query_tile(id="tl_a"),
        _query_tile(id="tl_b"),
        _query_tile(id="tl_a"),
    ]
    with pytest.raises(dashboards_module.DashboardValidationError, match="tl_a"):
        dashboards_module._reject_duplicate_tile_ids(tiles)


def test_reject_duplicate_tile_ids_passes_distinct_ids():
    tiles = [_query_tile(id="tl_a"), _query_tile(id="tl_b")]
    dashboards_module._reject_duplicate_tile_ids(tiles)  # must not raise


def test_changed_query_tile_ids_flags_new_and_edited_query_tiles_only():
    before = _dashboard(
        _query_tile(
            id="tl_a", query=_query(sql="SELECT wk FROM silver.stock_level LIMIT 1")
        ),
        _query_tile(id="tl_b"),
    )
    after = [
        # tl_a's query text changed -> flagged.
        _query_tile(
            id="tl_a",
            query=_query(sql="SELECT wk FROM silver.stock_level LIMIT 5"),
        ),
        # tl_b unchanged -> not flagged.
        _query_tile(id="tl_b"),
        # tl_c is new -> flagged.
        _query_tile(id="tl_c"),
        # A kpi tile is never a candidate for recompute at all.
        Tile(
            id="tl_k",
            kpi_identifier="some__kpi",
            chart=ChartKind.NUMBER,
            position=TilePosition(x=0, y=0, w=3, h=2),
        ),
    ]
    assert set(dashboards_module._changed_query_tile_ids(before, after)) == {
        "tl_a",
        "tl_c",
    }


def test_changed_query_tile_ids_is_empty_when_nothing_about_any_query_changed():
    before = _dashboard(_query_tile(id="tl_a"))
    after = [_query_tile(id="tl_a")]  # same id, same query
    assert dashboards_module._changed_query_tile_ids(before, after) == []


# ---------------------------------------------------------------------------
# arefresh_query_tiles: genuine concurrency (round-1 review, finding #12)
# ---------------------------------------------------------------------------


class _NoTablesReader:
    def get_table(self, name, *, include_proposed=False):
        return None

    async def aget_table(self, name, *, include_proposed=False):
        return None


class _RecordingResults:
    def __init__(self):
        self.written = {}

    async def aput(
        self, dashboard_id, tile_id, *, columns, rows, duration_ms, error, sql
    ):
        record = QueryResultRecord(
            dashboard_id=dashboard_id,
            tile_id=tile_id,
            computed_at=datetime.now(timezone.utc),
            columns=tuple(columns),
            rows=tuple(rows),
            duration_ms=duration_ms,
            error=error,
            sql=sql,
        )
        self.written[tile_id] = record
        return record


@pytest.mark.anyio
async def test_arefresh_query_tiles_costs_max_duration_not_sum_across_tiles(
    monkeypatch,
):
    """The synchronous refresh loop this replaced (round 1; deleted outright in round 3 once
    nothing called it any more -- there was a `refresh_query_tiles`/`_refresh_one` pair, no
    longer present in this module) ran every tile's check-then-query serially, so wall-clock
    cost scaled with `sum(duration)` across tiles. Simulates two tiles each with a 150ms
    warehouse round trip and asserts the concurrent version costs roughly one of them, not
    both, with genuine overlap (not just fast enough sequential calls to coincidentally land
    under the threshold)."""

    @contextmanager
    def _fake_open_reader(settings):
        yield _NoTablesReader()

    monkeypatch.setattr(queries_module, "open_semantic_reader", _fake_open_reader)
    monkeypatch.setattr(
        queries_module, "build_warehouse_read_guard", lambda reader: _guard()
    )

    class _FakeEngine:
        def dispose(self):
            pass

    monkeypatch.setattr(
        queries_module, "build_query_engine", lambda settings: _FakeEngine()
    )

    async def _fake_acheck_query(
        sql, *, guard, context_tables=None, personal_data=None
    ):
        return queries_module.QueryVerdict(
            status="ok", detail="", sql=sql, tables=("stock_level",)
        )

    events = []

    async def _fake_arun_query(engine, sql, *, limit, statement_timeout_ms):
        events.append(("start", sql))
        await asyncio.sleep(0.15)
        events.append(("end", sql))
        return ["value"], [{"value": 1}], 150

    monkeypatch.setattr(queries_module, "acheck_query", _fake_acheck_query)
    monkeypatch.setattr(queries_module, "arun_query", _fake_arun_query)

    dashboard = _dashboard(
        _query_tile(
            id="tl_a",
            query=_query(sql="SELECT 1 AS value FROM silver.stock_level LIMIT 1"),
        ),
        _query_tile(
            id="tl_b",
            query=_query(sql="SELECT 2 AS value FROM silver.stock_level LIMIT 1"),
        ),
    )
    results = _RecordingResults()

    started = time.monotonic()
    written = await queries_module.arefresh_query_tiles(
        dashboard, RetailSettings(), results
    )
    elapsed = time.monotonic() - started

    assert set(written) == {"tl_a", "tl_b"}
    assert set(results.written) == {"tl_a", "tl_b"}
    assert elapsed < 0.28, (
        "two 150ms tiles should cost ~150ms concurrently, not ~300ms serially"
    )
    starts = [i for i, (kind, _) in enumerate(events) if kind == "start"]
    ends = [i for i, (kind, _) in enumerate(events) if kind == "end"]
    assert max(starts) < min(ends), (
        "both queries must be in flight at once, not merely fast"
    )


@pytest.mark.anyio
async def test_arefresh_query_tiles_isolates_one_tiles_unexpected_exception(
    monkeypatch,
):
    """Round-2 review, finding #2 (major): only `arun_query`'s failure was caught inside
    `_arun_one`; an unguarded exception from `acheck_query` (e.g. a semantic-layer hiccup
    inside its `personal_data` callback) propagated out of the gathered task, out of
    `asyncio.gather` (no `return_exceptions=True`), discarding every other tile's
    already-computed result along with it -- including one whose check and warehouse round
    trip had already succeeded. `tl_bad`'s check raises; `tl_good`'s result must still be
    written."""

    @contextmanager
    def _fake_open_reader(settings):
        yield _NoTablesReader()

    monkeypatch.setattr(queries_module, "open_semantic_reader", _fake_open_reader)
    monkeypatch.setattr(
        queries_module, "build_warehouse_read_guard", lambda reader: _guard()
    )

    class _FakeEngine:
        def dispose(self):
            pass

    monkeypatch.setattr(
        queries_module, "build_query_engine", lambda settings: _FakeEngine()
    )

    async def _fake_acheck_query(
        sql, *, guard, context_tables=None, personal_data=None
    ):
        if "bad" in sql:
            raise RuntimeError("semantic layer hiccup")
        return queries_module.QueryVerdict(
            status="ok", detail="", sql=sql, tables=("stock_level",)
        )

    async def _fake_arun_query(engine, sql, *, limit, statement_timeout_ms):
        return ["value"], [{"value": 1}], 10

    monkeypatch.setattr(queries_module, "acheck_query", _fake_acheck_query)
    monkeypatch.setattr(queries_module, "arun_query", _fake_arun_query)

    dashboard = _dashboard(
        _query_tile(
            id="tl_good",
            query=_query(sql="SELECT 1 AS value FROM silver.stock_level LIMIT 1"),
        ),
        _query_tile(
            id="tl_bad",
            query=_query(sql="SELECT bad AS value FROM silver.stock_level LIMIT 1"),
        ),
    )
    results = _RecordingResults()

    written = await queries_module.arefresh_query_tiles(
        dashboard, RetailSettings(), results
    )

    assert set(written) == {"tl_good", "tl_bad"}
    assert results.written["tl_good"].error is None
    assert results.written["tl_good"].rows == ({"value": 1},)
    assert results.written["tl_bad"].error is not None
    assert "semantic layer hiccup" in results.written["tl_bad"].error


# --- run_query: the dry run's session setup ------------------------------------------------


class _RecordingCursor:
    """A DBAPI cursor that records every ``execute`` call as it was made -- statement text
    plus whatever parameters came with it -- and answers one row."""

    def __init__(self, calls):
        self.calls = calls
        self.description = None

    def execute(self, statement, *args):
        self.calls.append((statement, args))
        if statement.lstrip().upper().startswith("SELECT"):
            self.description = [("n",)]

    def fetchmany(self, size):
        return [(1,)]

    def close(self):
        self.calls.append(("<cursor.close>", ()))


class _RecordingRawConnection:
    def __init__(self, calls):
        self.calls = calls

    def cursor(self):
        return _RecordingCursor(self.calls)

    def rollback(self):
        self.calls.append(("<rollback>", ()))

    def close(self):
        self.calls.append(("<connection.close>", ()))


class _RecordingEngine:
    def __init__(self):
        self.calls = []

    def raw_connection(self):
        return _RecordingRawConnection(self.calls)

    def dispose(self):
        self.calls.append(("<engine.dispose>", ()))


def test_run_query_inlines_the_statement_timeout_rather_than_binding_it():
    """PostgreSQL takes no bind parameter in a ``SET`` statement, and psycopg 3 binds
    server-side (``$1``), so ``SET LOCAL statement_timeout = %s`` failed with ``syntax error
    at or near "$1"`` before the chart's SQL ran -- every composed chart's preview came back
    "refused by the warehouse". The timeout must land in the statement text as an integer,
    with no parameters attached, the way ``gold.py`` already does it."""
    engine = _RecordingEngine()

    columns, rows, _duration = queries_module.run_query(
        engine,
        "SELECT COUNT(*) AS n FROM silver.crm_deal LIMIT 1000",
        limit=50,
        statement_timeout_ms=15_000,
    )

    set_calls = [c for c in engine.calls if c[0].startswith("SET LOCAL")]
    assert set_calls == [("SET LOCAL statement_timeout = 15000", ())]
    assert columns == ["n"]
    assert rows == [{"n": 1}]
    # Session hygiene is unchanged: the transaction is rolled back and both handles closed.
    assert [c[0] for c in engine.calls][-3:] == [
        "<cursor.close>",
        "<rollback>",
        "<connection.close>",
    ]


def test_run_query_casts_the_timeout_to_an_integer_before_inlining_it():
    engine = _RecordingEngine()
    queries_module.run_query(
        engine, "SELECT 1 AS n", limit=1, statement_timeout_ms=2500.9
    )
    assert engine.calls[0] == ("SET LOCAL statement_timeout = 2500", ())


# ---------------------------------------------------------------------------
# Filter columns (R13.1): candidates, WHERE injection, distinct values, live filtered reads
# ---------------------------------------------------------------------------


class _FakeSemanticReader:
    """A reader with a small, fixed set of tables/columns -- enough to exercise
    `available_filter_columns`'s type/personal-data filtering without a real semantic layer."""

    def __init__(self, tables):
        self._tables = tables

    def get_table(self, name, *, include_proposed=False):
        return self._tables.get(name)

    async def aget_table(self, name, *, include_proposed=False):
        return self._tables.get(name)


_TEXT_AND_PII_COLUMNS = (
    Column(name="region", data_type="text"),
    Column(name="category", data_type="character varying"),
    Column(name="customer_email", data_type="text", is_personal_data=True),
    Column(name="total", data_type="numeric"),
)


def _fake_table(name, columns=_TEXT_AND_PII_COLUMNS):
    return Table(ref=TableRef(name=name, schema="silver"), columns=tuple(columns))


def test_available_filter_columns_keeps_only_text_non_personal_columns():
    reader = _FakeSemanticReader({"orders": _fake_table("orders")})
    candidates = queries_module.available_filter_columns(reader, ["orders"])
    assert candidates == (
        DashboardFilterColumn(table="orders", column="category"),
        DashboardFilterColumn(table="orders", column="region"),
    )


@pytest.mark.anyio
async def test_aavailable_filter_columns_agrees_with_its_sync_twin():
    reader = _FakeSemanticReader({"orders": _fake_table("orders")})
    assert await queries_module.aavailable_filter_columns(
        reader, ["orders"]
    ) == queries_module.available_filter_columns(reader, ["orders"])


def test_available_filter_columns_skips_a_table_the_layer_does_not_expose():
    assert (
        queries_module.available_filter_columns(_FakeSemanticReader({}), ["ghost"])
        == ()
    )


def test_apply_filter_predicates_and_combines_two_active_columns():
    statement = sqlglot.parse_one(
        "SELECT o.id, o.region FROM orders o WHERE o.status = 'open' LIMIT 100",
        read="postgres",
    )
    changed = queries_module._apply_filter_predicates(
        statement,
        {("orders", "region"): ("North", "South"), ("orders", "channel"): ("Online",)},
        personal={},
    )
    sql = statement.sql(dialect="postgres")
    assert changed is not None
    assert "o.status = 'open'" in sql
    assert "o.region IN ('North', 'South')" in sql
    assert "o.channel IN ('Online')" in sql
    assert " AND " in sql


def test_apply_filter_predicates_skips_a_table_the_statement_does_not_reference():
    statement = sqlglot.parse_one("SELECT id FROM orders LIMIT 10", read="postgres")
    changed = queries_module._apply_filter_predicates(
        statement, {("customers", "region"): ("North",)}, personal={}
    )
    assert changed is None
    assert "customers" not in statement.sql(dialect="postgres")


def test_apply_filter_predicates_refuses_a_personal_data_column():
    statement = sqlglot.parse_one("SELECT id FROM orders LIMIT 10", read="postgres")
    changed = queries_module._apply_filter_predicates(
        statement,
        {("orders", "customer_email"): ("a@x.com",)},
        personal={"orders": frozenset({"customer_email"})},
    )
    assert changed is None
    assert "customer_email" not in statement.sql(dialect="postgres")


def test_apply_filter_predicates_with_no_predicates_is_a_no_op():
    statement = sqlglot.parse_one("SELECT id FROM orders LIMIT 10", read="postgres")
    assert queries_module._apply_filter_predicates(statement, {}, personal={}) is None


def test_distinct_column_values_runs_a_guard_checked_statement():
    engine = _RecordingEngine()
    values = queries_module.distinct_column_values(
        engine, _guard("orders"), "orders", "region"
    )
    executed = next(c[0] for c in engine.calls if c[0].upper().startswith("SELECT"))
    assert "DISTINCT" in executed.upper()
    assert '"region"' in executed
    assert '"orders"' in executed
    assert values == (
        "1",
    )  # `_RecordingCursor` always answers one row, column "n" -> 1


def test_distinct_column_values_refuses_a_table_outside_the_allow_list():
    engine = _RecordingEngine()
    values = queries_module.distinct_column_values(
        engine, _guard("orders"), "shipments", "carrier"
    )
    assert values == ()
    assert engine.calls == []  # never ran -- the guard refused before execution


@pytest.mark.anyio
async def test_arun_filtered_tile_ignores_a_filter_column_absent_from_the_tile_sql(
    monkeypatch,
):
    """A dashboard filter on a table this tile's own SQL never reads must not change what the
    tile runs -- it just isn't affected by that filter (a dashboard mixing tiles over
    different tables works exactly as it does without any filter active)."""
    engine = _RecordingEngine()
    tile = _query_tile()  # reads `stock_level`, not `orders`
    columns, rows, duration, error, sql = await queries_module.arun_filtered_tile(
        tile,
        _FakeSemanticReader({"stock_level": _fake_table("stock_level")}),
        _guard("stock_level"),
        engine,
        {("orders", "region"): ("North",)},
    )
    assert error is None
    assert "region" not in sql
    assert sql == tile.query.sql  # the guard's own check made no rewrite either


@pytest.mark.anyio
async def test_arun_filtered_tile_applies_a_matching_predicate():
    engine = _RecordingEngine()
    tile = _query_tile(
        query=_query(
            sql="SELECT wk, stockouts FROM stock_level LIMIT 100",
            source_tables=frozenset({"stock_level"}),
        )
    )
    columns, rows, duration, error, sql = await queries_module.arun_filtered_tile(
        tile,
        _FakeSemanticReader({"stock_level": _fake_table("stock_level")}),
        _guard("stock_level"),
        engine,
        {("stock_level", "region"): ("North", "South")},
    )
    assert error is None
    assert "region IN ('North', 'South')" in sql


@pytest.mark.anyio
async def test_arun_filtered_query_tiles_never_writes_to_the_cache(monkeypatch):
    """The whole point of a filtered read: it is computed live and handed back as
    `QueryResultRecord`s, never persisted -- unlike `arefresh_query_tiles`."""

    @contextmanager
    def _fake_open_reader(settings):
        yield _NoTablesReader()

    monkeypatch.setattr(queries_module, "open_semantic_reader", _fake_open_reader)
    monkeypatch.setattr(
        queries_module, "build_warehouse_read_guard", lambda reader: _guard()
    )
    monkeypatch.setattr(
        queries_module, "build_query_engine", lambda settings: _RecordingEngine()
    )

    dashboard = _dashboard(_query_tile())
    records = await queries_module.arun_filtered_query_tiles(
        dashboard, RetailSettings(), {}
    )
    assert set(records) == {"tl_q1"}
    assert isinstance(records["tl_q1"], QueryResultRecord)


# ---------------------------------------------------------------------------
# Router: filter validation on write, the two new endpoints, GET /data's `f` param
# ---------------------------------------------------------------------------


def test_create_dashboard_accepts_a_valid_filter_column(monkeypatch):
    @contextmanager
    def _fake_open_reader(settings):
        yield _FakeSemanticReader({"orders": _fake_table("orders")})

    monkeypatch.setattr(dashboards_module, "open_semantic_reader", _fake_open_reader)
    monkeypatch.setattr(
        dashboards_module, "build_warehouse_read_guard", lambda reader: _guard("orders")
    )
    store = _FakeDashboardStore(_dashboard())
    with _client(monkeypatch, store) as client:
        response = client.post(
            "/dashboards",
            json={
                "name": "Regional",
                "tiles": [
                    {
                        "source": "query",
                        "query": {
                            "sql": "SELECT region, count(*) AS n FROM silver.orders GROUP BY 1 LIMIT 10"
                        },
                        "chart": "bar",
                        "position": {"x": 0, "y": 0, "w": 3, "h": 2},
                    }
                ],
                "filters": [{"table": "orders", "column": "region"}],
            },
        )
    assert response.status_code == 201, response.text
    assert response.json()["filters"] == [{"table": "orders", "column": "region"}]


def test_create_dashboard_422s_on_a_filter_column_not_offered(monkeypatch):
    @contextmanager
    def _fake_open_reader(settings):
        yield _FakeSemanticReader({"orders": _fake_table("orders")})

    monkeypatch.setattr(dashboards_module, "open_semantic_reader", _fake_open_reader)
    monkeypatch.setattr(
        dashboards_module, "build_warehouse_read_guard", lambda reader: _guard("orders")
    )
    store = _FakeDashboardStore(_dashboard())
    with _client(monkeypatch, store) as client:
        response = client.post(
            "/dashboards",
            json={
                "name": "Regional",
                "tiles": [
                    {
                        "source": "query",
                        "query": {
                            "sql": "SELECT region, count(*) AS n FROM silver.orders GROUP BY 1 LIMIT 10"
                        },
                        "chart": "bar",
                        "position": {"x": 0, "y": 0, "w": 3, "h": 2},
                    }
                ],
                # `customer_email` is personal data -- never offered as a candidate.
                "filters": [{"table": "orders", "column": "customer_email"}],
            },
        )
    assert response.status_code == 422
    assert "orders.customer_email" in response.json()["detail"]


def test_get_filter_columns_returns_candidates_for_the_dashboards_query_tiles(
    monkeypatch,
):
    @contextmanager
    def _fake_open_reader(settings):
        yield _FakeSemanticReader({"stock_level": _fake_table("stock_level")})

    monkeypatch.setattr(dashboards_module, "open_semantic_reader", _fake_open_reader)
    store = _FakeDashboardStore(_dashboard(_query_tile()))
    with _client(monkeypatch, store) as client:
        response = client.get("/dashboards/dsh_1/filter-columns")
    assert response.status_code == 200
    assert response.json() == [
        {"table": "stock_level", "column": "category"},
        {"table": "stock_level", "column": "region"},
    ]


def test_get_filter_columns_is_empty_for_a_dashboard_with_no_query_tile(monkeypatch):
    store = _FakeDashboardStore(_dashboard())
    with _client(monkeypatch, store) as client:
        response = client.get("/dashboards/dsh_1/filter-columns")
    assert response.status_code == 200
    assert response.json() == []


def test_get_filter_values_422s_for_a_column_not_in_the_dashboards_filters(monkeypatch):
    store = _FakeDashboardStore(_dashboard(_query_tile()))  # declares no filters
    with _client(monkeypatch, store) as client:
        response = client.get(
            "/dashboards/dsh_1/filter-values",
            params={"table": "stock_level", "column": "region"},
        )
    assert response.status_code == 422


def test_get_filter_values_returns_distinct_values_for_a_declared_column(monkeypatch):
    @contextmanager
    def _fake_open_reader(settings):
        yield _NoTablesReader()

    monkeypatch.setattr(dashboards_module, "open_semantic_reader", _fake_open_reader)
    monkeypatch.setattr(
        dashboards_module,
        "build_warehouse_read_guard",
        lambda reader: _guard("stock_level"),
    )
    monkeypatch.setattr(
        dashboards_module, "build_query_engine", lambda settings: _RecordingEngine()
    )
    store = _FakeDashboardStore(
        _dashboard(
            _query_tile(),
            filters=(DashboardFilterColumn(table="stock_level", column="region"),),
        )
    )
    with _client(monkeypatch, store) as client:
        response = client.get(
            "/dashboards/dsh_1/filter-values",
            params={"table": "stock_level", "column": "region"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body == {"table": "stock_level", "column": "region", "values": ["1"]}


def test_get_dashboard_data_with_no_filter_never_computes_live(monkeypatch):
    store = _FakeDashboardStore(_dashboard(_query_tile()))
    monkeypatch.setattr(
        dashboards_module, "_load_query_results", lambda dashboard, settings: {}
    )

    async def _boom(*a, **kw):
        raise AssertionError("must not run a live filtered read with no filter active")

    monkeypatch.setattr(dashboards_module, "arun_filtered_query_tiles", _boom)
    with _client(monkeypatch, store) as client:
        response = client.get("/dashboards/dsh_1/data")
    assert response.status_code == 200


def test_get_dashboard_data_with_a_declared_filter_computes_live(monkeypatch):
    store = _FakeDashboardStore(
        _dashboard(
            _query_tile(),
            filters=(DashboardFilterColumn(table="stock_level", column="region"),),
        )
    )
    monkeypatch.setattr(
        dashboards_module, "_load_query_results", lambda dashboard, settings: {}
    )
    seen_predicates = {}

    async def _fake_filtered(dashboard, settings, predicates):
        seen_predicates.update(predicates)
        return {
            "tl_q1": QueryResultRecord(
                dashboard_id=dashboard.id,
                tile_id="tl_q1",
                computed_at=datetime.now(timezone.utc),
                columns=("region",),
                rows=({"region": "North"},),
                duration_ms=5,
            )
        }

    monkeypatch.setattr(dashboards_module, "arun_filtered_query_tiles", _fake_filtered)
    with _client(monkeypatch, store) as client:
        response = client.get(
            "/dashboards/dsh_1/data", params={"f": "stock_level.region:North,South"}
        )
    assert response.status_code == 200
    assert seen_predicates == {("stock_level", "region"): ("North", "South")}


def test_get_dashboard_data_ignores_a_filter_param_for_an_undeclared_column(
    monkeypatch,
):
    store = _FakeDashboardStore(_dashboard(_query_tile()))  # no `filters` declared
    monkeypatch.setattr(
        dashboards_module, "_load_query_results", lambda dashboard, settings: {}
    )

    async def _boom(*a, **kw):
        raise AssertionError("an undeclared filter column must be ignored, not applied")

    monkeypatch.setattr(dashboards_module, "arun_filtered_query_tiles", _boom)
    with _client(monkeypatch, store) as client:
        response = client.get(
            "/dashboards/dsh_1/data", params={"f": "stock_level.region:North"}
        )
    assert response.status_code == 200
