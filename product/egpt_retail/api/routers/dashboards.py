"""R13's real router: dashboard storage plus gold reads.

Every ``kpi_identifier`` a tile names must be a registered, ``ready`` KPI (K11's
feasibility, the same set difference ``materialise_gold``/``assess_kpi_feasibility`` use) --
checked here, not in ``dashboards/store.py``, because it needs the KPI registry and an open
semantic reader, which are concerns this store deliberately does not carry.
"""

from __future__ import annotations

import uuid
from typing import Iterator, List, Sequence

from fastapi import APIRouter, Depends, HTTPException, Request

from egpt_retail.api import models as wire
from egpt_retail.app.db import psycopg_dsn
from egpt_retail.app.scopes import require_scopes
from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.dashboards import (
    ChartKind,
    Dashboard,
    DashboardFilter,
    RefreshSchedule,
    Tile,
    TilePosition,
    Visibility,
)
from egpt_retail.contracts.kpi import FeasibilityVerdict, assess_registry
from egpt_retail.contracts.semantic import available_tables
from egpt_retail.dashboards.data import read_dashboard_data
from egpt_retail.dashboards.store import UNSET, DashboardStore
from egpt_retail.kpi.store import KpiRegistryStore

__all__ = ["router"]

router = APIRouter()


class DashboardValidationError(ValueError):
    """A tile names a KPI that is not registered or not ready. The router turns this into a
    422 with the message the spec's own example uses."""


# --- store dependency, owner, readiness gate ----------------------------------------------


def _dashboard_store() -> Iterator[DashboardStore]:
    import psycopg

    settings = RetailSettings()
    connection = psycopg.connect(psycopg_dsn(settings.retail_db_url))
    try:
        yield DashboardStore(connection)
    finally:
        connection.close()


def _owner(request: Request) -> str:
    return getattr(request.state, "user_id", None) or "local"


def _check_tiles_ready(tiles: Sequence[Tile], settings: RetailSettings) -> None:
    """Refuses (raising :class:`DashboardValidationError`) unless every tile's KPI is
    registered and ``ready`` -- a blocked or partial KPI has nothing in gold to read."""
    if not tiles:
        return
    wanted = sorted({tile.kpi_identifier for tile in tiles})

    import psycopg

    connection = psycopg.connect(psycopg_dsn(settings.retail_db_url))
    try:
        definitions = [
            d for d in KpiRegistryStore(connection).list() if d.identifier in wanted
        ]
    finally:
        connection.close()

    found = {d.identifier for d in definitions}
    unregistered = set(wanted) - found
    if unregistered:
        raise DashboardValidationError(
            f"{sorted(unregistered)[0]} is not a registered KPI and cannot be tiled."
        )

    with open_semantic_reader(settings) as reader:
        feasibility = assess_registry(definitions, available_tables(reader))
    for identifier in wanted:
        result = feasibility[identifier]
        if result.verdict is not FeasibilityVerdict.READY:
            missing = ", ".join(sorted(result.missing_tables)) or "everything"
            raise DashboardValidationError(
                f"{identifier} is {result.verdict.value} (missing: {missing}) and cannot "
                "be tiled."
            )


# --- wire conversions ------------------------------------------------------------------------


def _tile_from_write(write: wire.TileWrite) -> Tile:
    return Tile(
        id=f"tl_{uuid.uuid4().hex[:8]}",
        kpi_identifier=write.kpi_identifier,
        chart=ChartKind(write.chart.value),
        position=TilePosition(
            x=write.position.x,
            y=write.position.y,
            w=write.position.w,
            h=write.position.h,
        ),
        title=write.title or "",
        grain_value=write.grain_value,
    )


def _filter_from_wire(item: wire.DashboardFilter) -> DashboardFilter:
    return DashboardFilter(field=item.field.value, value=item.value)


def _tile_out(tile: Tile) -> wire.Tile:
    return wire.Tile(
        id=tile.id,
        kpi_identifier=tile.kpi_identifier,
        title=tile.title,
        chart=wire.ChartKind(tile.chart.value),
        position=wire.TilePosition(
            x=tile.position.x, y=tile.position.y, w=tile.position.w, h=tile.position.h
        ),
        grain_value=tile.grain_value,
    )


def _filter_out(item: DashboardFilter) -> wire.DashboardFilter:
    return wire.DashboardFilter(
        field=wire.DashboardFilterField(item.field), value=item.value
    )


def _dashboard_out(dashboard: Dashboard) -> wire.Dashboard:
    return wire.Dashboard(
        id=dashboard.id,
        name=dashboard.name,
        description=dashboard.description,
        tiles=[_tile_out(t) for t in dashboard.tiles],
        filters=[_filter_out(f) for f in dashboard.filters],
        refresh_schedule=wire.RefreshSchedule(dashboard.refresh_schedule.value),
        visibility=wire.Visibility(dashboard.visibility.value),
        owner=dashboard.owner,
        created_at=dashboard.created_at,
        updated_at=dashboard.updated_at,
    )


# --- routes ------------------------------------------------------------------------------------


@router.get("/dashboards", response_model=list[wire.Dashboard])
def list_dashboards(
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:read")),
):
    return [_dashboard_out(d) for d in store.list_visible_to(_owner(request))]


@router.post("/dashboards", response_model=wire.Dashboard, status_code=201)
def create_dashboard(
    body: wire.DashboardWrite,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:write")),
):
    tiles: List[Tile] = [_tile_from_write(t) for t in (body.tiles or [])]
    settings = RetailSettings()
    try:
        _check_tiles_ready(tiles, settings)
    except DashboardValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    dashboard = store.create(
        name=body.name,
        owner=_owner(request),
        description=body.description or "",
        tiles=tiles,
        filters=[_filter_from_wire(f) for f in (body.filters or [])],
        refresh_schedule=RefreshSchedule(
            (body.refresh_schedule or wire.RefreshSchedule.nightly).value
        ),
        visibility=Visibility((body.visibility or wire.Visibility.private).value),
    )
    return _dashboard_out(dashboard)


@router.get("/dashboards/{dashboard_id}", response_model=wire.Dashboard)
def get_dashboard(
    dashboard_id: str,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:read")),
):
    dashboard = store.get(dashboard_id)
    if dashboard is None or not dashboard.visible_to(_owner(request)):
        raise HTTPException(status_code=404, detail=f"No dashboard {dashboard_id!r}.")
    return _dashboard_out(dashboard)


@router.patch("/dashboards/{dashboard_id}", response_model=wire.Dashboard)
def update_dashboard(
    dashboard_id: str,
    body: wire.DashboardWrite,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:write")),
):
    settings = RetailSettings()
    tiles = UNSET if body.tiles is None else [_tile_from_write(t) for t in body.tiles]
    if tiles is not UNSET:
        try:
            _check_tiles_ready(tiles, settings)
        except DashboardValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    dashboard = store.update(
        dashboard_id,
        _owner(request),
        name=body.name if body.name is not None else UNSET,
        description=body.description if body.description is not None else UNSET,
        tiles=tiles,
        filters=(
            UNSET
            if body.filters is None
            else [_filter_from_wire(f) for f in body.filters]
        ),
        refresh_schedule=(
            RefreshSchedule(body.refresh_schedule.value)
            if body.refresh_schedule is not None
            else UNSET
        ),
        visibility=(
            Visibility(body.visibility.value) if body.visibility is not None else UNSET
        ),
    )
    if dashboard is None:
        raise HTTPException(status_code=404, detail=f"No dashboard {dashboard_id!r}.")
    return _dashboard_out(dashboard)


@router.delete("/dashboards/{dashboard_id}", status_code=204)
def delete_dashboard(
    dashboard_id: str,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:write")),
):
    if not store.delete(dashboard_id, _owner(request)):
        raise HTTPException(status_code=404, detail=f"No dashboard {dashboard_id!r}.")


@router.get("/dashboards/{dashboard_id}/data", response_model=wire.DashboardData)
async def get_dashboard_data(
    dashboard_id: str,
    request: Request,
    store: DashboardStore = Depends(_dashboard_store),
    _: None = Depends(require_scopes("dashboards:read")),
):
    dashboard = store.get(dashboard_id)
    if dashboard is None or not dashboard.visible_to(_owner(request)):
        raise HTTPException(status_code=404, detail=f"No dashboard {dashboard_id!r}.")
    return await read_dashboard_data(dashboard)
