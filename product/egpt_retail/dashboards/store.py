"""``DashboardStore`` -- dashboard definitions in the control-plane database (R13).

One table, ``tiles`` and ``filters`` held as JSONB the same way KPI's ``source_tables``/
``parameters`` are (``kpi/store.py``), self-provisioning behind an advisory lock like every
other retail-owned table in this database. The readiness gate on write -- every tile's
``kpi_identifier`` must be ``ready`` -- lives in ``api/routers/dashboards.py``, not here: it
needs the KPI registry and an open semantic reader, and this store's job stays "the dashboard
table", not "everything a dashboard touches".
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from egpt_retail.contracts.dashboards import (
    ChartKind,
    Dashboard,
    DashboardFilter,
    RefreshSchedule,
    Tile,
    TilePosition,
    Visibility,
)

__all__ = ["DASHBOARDS_SCHEMA", "UNSET", "DashboardStore"]

DASHBOARDS_SCHEMA = "dashboards"

#: A partial-update sentinel: ``update(..., tiles=UNSET)`` means "leave tiles unchanged",
#: while ``update(..., tiles=[])`` means "replace with no tiles". ``None`` cannot carry this
#: distinction since it is also a valid value for nothing on some fields.
UNSET: Any = object()


def _ddl(schema: str) -> Tuple[str, ...]:
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""CREATE TABLE IF NOT EXISTS {schema}.dashboard (
            id               text        PRIMARY KEY,
            name             text        NOT NULL,
            description      text        NOT NULL DEFAULT '',
            tiles            jsonb       NOT NULL DEFAULT '[]'::jsonb,
            filters          jsonb       NOT NULL DEFAULT '[]'::jsonb,
            refresh_schedule text        NOT NULL DEFAULT 'nightly',
            visibility       text        NOT NULL DEFAULT 'private',
            owner            text        NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now()
        )""",
        f"CREATE INDEX IF NOT EXISTS dashboard_owner_idx ON {schema}.dashboard (owner)",
        f"""CREATE INDEX IF NOT EXISTS dashboard_visibility_idx
            ON {schema}.dashboard (visibility) WHERE visibility <> 'private'""",
    )


def _tile_to_json(tile: Tile) -> Dict[str, Any]:
    return {
        "id": tile.id,
        "kpi_identifier": tile.kpi_identifier,
        "chart": tile.chart.value,
        "position": {
            "x": tile.position.x,
            "y": tile.position.y,
            "w": tile.position.w,
            "h": tile.position.h,
        },
        "title": tile.title,
        "grain_value": tile.grain_value,
    }


def _tile_from_json(payload: Dict[str, Any]) -> Tile:
    position = payload["position"]
    return Tile(
        id=payload["id"],
        kpi_identifier=payload["kpi_identifier"],
        chart=ChartKind(payload["chart"]),
        position=TilePosition(
            x=position["x"], y=position["y"], w=position["w"], h=position["h"]
        ),
        title=payload.get("title", ""),
        grain_value=payload.get("grain_value"),
    )


def _filter_to_json(item: DashboardFilter) -> Dict[str, Any]:
    return {"field": item.field, "value": item.value}


def _filter_from_json(payload: Dict[str, Any]) -> DashboardFilter:
    return DashboardFilter(field=payload["field"], value=payload["value"])


def _row_to_dashboard(row: Sequence[Any]) -> Dashboard:
    tiles = row[3]
    if isinstance(tiles, str):
        tiles = json.loads(tiles)
    filters = row[4]
    if isinstance(filters, str):
        filters = json.loads(filters)
    return Dashboard(
        id=row[0],
        name=row[1],
        description=row[2] or "",
        tiles=tuple(_tile_from_json(t) for t in tiles),
        filters=tuple(_filter_from_json(f) for f in filters),
        refresh_schedule=RefreshSchedule(row[5]),
        visibility=Visibility(row[6]),
        owner=row[7],
        created_at=row[8],
        updated_at=row[9],
    )


_COLUMNS = (
    "id, name, description, tiles, filters, refresh_schedule, visibility, owner, "
    "created_at, updated_at"
)


class DashboardStore:
    """The dashboards schema in the control-plane database. The connection is injected; the
    caller owns its lifetime."""

    def __init__(self, connection: Any, schema: str = DASHBOARDS_SCHEMA) -> None:
        self._connection = connection
        self._schema = schema

    # --- schema ------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("egpt_retail.dashboards.ddl",),
            )
            for statement in _ddl(self._schema):
                cursor.execute(statement)
        self._connection.commit()

    async def aensure_schema(self) -> None:
        await asyncio.to_thread(self.ensure_schema)

    # --- writing -----------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        owner: str,
        description: str = "",
        tiles: Sequence[Tile] = (),
        filters: Sequence[DashboardFilter] = (),
        refresh_schedule: RefreshSchedule = RefreshSchedule.NIGHTLY,
        visibility: Visibility = Visibility.PRIVATE,
    ) -> Dashboard:
        dashboard_id = f"dsh_{uuid.uuid4().hex[:12]}"
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.dashboard
                    (id, name, description, tiles, filters, refresh_schedule, visibility, owner)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING created_at, updated_at""",
                (
                    dashboard_id,
                    name,
                    description,
                    json.dumps([_tile_to_json(t) for t in tiles]),
                    json.dumps([_filter_to_json(f) for f in filters]),
                    refresh_schedule.value,
                    visibility.value,
                    owner,
                ),
            )
            created_at, updated_at = cursor.fetchone()
        self._connection.commit()
        return Dashboard(
            id=dashboard_id,
            name=name,
            owner=owner,
            created_at=created_at,
            updated_at=updated_at,
            description=description,
            tiles=tuple(tiles),
            filters=tuple(filters),
            refresh_schedule=refresh_schedule,
            visibility=visibility,
        )

    async def acreate(
        self,
        *,
        name: str,
        owner: str,
        description: str = "",
        tiles: Sequence[Tile] = (),
        filters: Sequence[DashboardFilter] = (),
        refresh_schedule: RefreshSchedule = RefreshSchedule.NIGHTLY,
        visibility: Visibility = Visibility.PRIVATE,
    ) -> Dashboard:
        return await asyncio.to_thread(
            self.create,
            name=name,
            owner=owner,
            description=description,
            tiles=tiles,
            filters=filters,
            refresh_schedule=refresh_schedule,
            visibility=visibility,
        )

    def update(
        self,
        dashboard_id: str,
        owner: str,
        *,
        name: Any = UNSET,
        description: Any = UNSET,
        tiles: Any = UNSET,
        filters: Any = UNSET,
        refresh_schedule: Any = UNSET,
        visibility: Any = UNSET,
    ) -> Optional[Dashboard]:
        """``None`` if the dashboard does not exist or ``owner`` does not own it -- writes
        are owner-only even though a ``team``/``everyone`` dashboard is readable by others
        (:meth:`Dashboard.visible_to`); the spec does not distinguish "can see" from "can
        edit", and defaulting edit rights to the owner alone is the safer reading."""
        current = self.get(dashboard_id)
        if current is None or current.owner != owner:
            return None

        # (column, provided, value-to-write) -- built explicitly rather than looped over a
        # dict keyed by column name, so "was this argument passed" and "what SQL value does
        # it become" never have to be re-derived from each other.
        candidates: List[Tuple[str, bool, Any]] = [
            ("name", name is not UNSET, name),
            ("description", description is not UNSET, description),
            (
                "tiles",
                tiles is not UNSET,
                json.dumps([_tile_to_json(t) for t in tiles])
                if tiles is not UNSET
                else None,
            ),
            (
                "filters",
                filters is not UNSET,
                json.dumps([_filter_to_json(f) for f in filters])
                if filters is not UNSET
                else None,
            ),
            (
                "refresh_schedule",
                refresh_schedule is not UNSET,
                refresh_schedule.value if refresh_schedule is not UNSET else None,
            ),
            (
                "visibility",
                visibility is not UNSET,
                visibility.value if visibility is not UNSET else None,
            ),
        ]
        set_clauses: List[str] = []
        params: List[Any] = []
        for column, provided, value in candidates:
            if not provided:
                continue
            set_clauses.append(f"{column} = %s")
            params.append(value)
        if not set_clauses:
            return current
        set_clauses.append("updated_at = now()")
        params.extend([dashboard_id, owner])
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.dashboard SET {", ".join(set_clauses)}
                    WHERE id = %s AND owner = %s""",
                tuple(params),
            )
        self._connection.commit()
        return self.get(dashboard_id)

    async def aupdate(
        self,
        dashboard_id: str,
        owner: str,
        *,
        name: Any = UNSET,
        description: Any = UNSET,
        tiles: Any = UNSET,
        filters: Any = UNSET,
        refresh_schedule: Any = UNSET,
        visibility: Any = UNSET,
    ) -> Optional[Dashboard]:
        return await asyncio.to_thread(
            self.update,
            dashboard_id,
            owner,
            name=name,
            description=description,
            tiles=tiles,
            filters=filters,
            refresh_schedule=refresh_schedule,
            visibility=visibility,
        )

    def delete(self, dashboard_id: str, owner: str) -> bool:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {self._schema}.dashboard WHERE id = %s AND owner = %s",
                (dashboard_id, owner),
            )
            removed = bool(cursor.rowcount)
        self._connection.commit()
        return removed

    async def adelete(self, dashboard_id: str, owner: str) -> bool:
        return await asyncio.to_thread(self.delete, dashboard_id, owner)

    # --- reading -----------------------------------------------------------------------

    def get(self, dashboard_id: str) -> Optional[Dashboard]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM {self._schema}.dashboard WHERE id = %s",
                (dashboard_id,),
            )
            row = cursor.fetchone()
        return _row_to_dashboard(row) if row is not None else None

    async def aget(self, dashboard_id: str) -> Optional[Dashboard]:
        return await asyncio.to_thread(self.get, dashboard_id)

    def list_visible_to(self, caller: str) -> Tuple[Dashboard, ...]:
        """Every dashboard ``caller`` may see: their own, plus anything ``team`` or
        ``everyone`` -- the SQL mirrors :meth:`Dashboard.visible_to` so the two cannot drift."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT {_COLUMNS} FROM {self._schema}.dashboard
                    WHERE owner = %s OR visibility IN ('team', 'everyone')
                    ORDER BY created_at""",
                (caller,),
            )
            rows = cursor.fetchall()
        return tuple(_row_to_dashboard(row) for row in rows)

    async def alist_visible_to(self, caller: str) -> Tuple[Dashboard, ...]:
        return await asyncio.to_thread(self.list_visible_to, caller)

    def list_referencing(self, kpi_identifier: str) -> Tuple[Dashboard, ...]:
        """Every dashboard with a tile naming ``kpi_identifier``, across *all* owners and
        visibilities -- unlike :meth:`list_visible_to`, which answers "what can this one
        caller see", not "does anything, anywhere, still need this KPI".

        Used by ``KpiRegistryStore.delete_if_unreferenced``'s reference check (K11/#240
        review): a KPI referenced only by another user's private dashboard must still block
        delete, even though the deleting caller cannot see that dashboard through
        :meth:`list_visible_to`.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT DISTINCT {_COLUMNS}
                    FROM {self._schema}.dashboard AS d,
                         jsonb_array_elements(d.tiles) AS tile
                    WHERE tile ->> 'kpi_identifier' = %s
                    ORDER BY created_at""",
                (kpi_identifier,),
            )
            rows = cursor.fetchall()
        return tuple(_row_to_dashboard(row) for row in rows)

    async def alist_referencing(self, kpi_identifier: str) -> Tuple[Dashboard, ...]:
        """Async variant."""
        return await asyncio.to_thread(self.list_referencing, kpi_identifier)
