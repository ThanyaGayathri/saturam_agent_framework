"""``DashboardStore`` -- dashboard definitions in the control-plane database (R13).

One table, ``tiles`` and ``filters`` held as JSONB the same way KPI's ``source_tables``/
``parameters`` are (``kpi/store.py``), self-provisioning behind an advisory lock like every
other retail-owned table in this database. The readiness gate on write -- every ``kpi`` tile's
``kpi_identifier`` must be ``ready``, every ``query`` tile's SQL must pass the guard -- lives
in ``api/routers/dashboards.py``, not here: it needs the KPI registry, an open semantic reader
and the warehouse, and this store's job stays "the dashboard table", not "everything a
dashboard touches".

Per-tile edits (``upsert_tile``/``remove_tile``, contract 1.2.0) are single JSONB statements
rather than read-modify-write of the whole array, so two people editing two charts of the
same dashboard at once cannot overwrite each other, and tile ids stay stable across edits --
the interface keys its grid on them.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from egpt_retail.contracts.dashboards import (
    AxisKind,
    ChartAxis,
    ChartKind,
    Dashboard,
    DashboardFilter,
    RefreshSchedule,
    Tile,
    TilePosition,
    TileQuery,
    TileSource,
    Visibility,
)

__all__ = [
    "DASHBOARDS_SCHEMA",
    "UNSET",
    "DashboardKpiVanishedError",
    "DashboardStore",
]

DASHBOARDS_SCHEMA = "dashboards"

#: A partial-update sentinel: ``update(..., tiles=UNSET)`` means "leave tiles unchanged",
#: while ``update(..., tiles=[])`` means "replace with no tiles". ``None`` cannot carry this
#: distinction since it is also a valid value for nothing on some fields.
UNSET: Any = object()


class DashboardKpiVanishedError(ValueError):
    """A tile in this write names a KPI identifier that no longer exists in the registry at
    the moment the write actually lands.

    ``api/routers/dashboards.py``'s own ``_check_tiles_ready`` already confirmed every tile's
    KPI was registered and ready -- but that check runs before this write, over a separate
    connection, with nothing holding the KPI row still in between (#240 review, round 2: the
    same dangling-reference bug ``KpiRegistryStore.delete_if_unreferenced`` closes from the
    delete side is still reachable from this side, a KPI deleted in the gap between that check
    and this write). Raised only when the atomic existence guard inside :meth:`DashboardStore.
    create`/:meth:`DashboardStore.update` itself trips, not by the router's own earlier check.
    """


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


def _axis_to_json(axis: ChartAxis) -> Dict[str, Any]:
    return {
        "column": axis.column,
        "kind": axis.kind.value,
        "label": axis.label,
        "unit": axis.unit,
    }


def _axis_from_json(payload: Dict[str, Any]) -> ChartAxis:
    return ChartAxis(
        column=payload["column"],
        kind=AxisKind(payload.get("kind") or AxisKind.CATEGORY.value),
        label=payload.get("label") or "",
        unit=payload.get("unit") or "",
    )


def _query_to_json(query: TileQuery) -> Dict[str, Any]:
    return {
        "sql": query.sql,
        "dialect": query.dialect,
        "source_tables": sorted(query.source_tables),
        "x": _axis_to_json(query.x) if query.x is not None else None,
        "y": [_axis_to_json(axis) for axis in query.y],
        "series_by": query.series_by,
    }


def _query_from_json(payload: Dict[str, Any]) -> TileQuery:
    x = payload.get("x")
    return TileQuery(
        sql=payload["sql"],
        source_tables=frozenset(payload.get("source_tables") or ()),
        dialect=payload.get("dialect") or "postgres",
        x=_axis_from_json(x) if x else None,
        y=tuple(_axis_from_json(axis) for axis in (payload.get("y") or ())),
        series_by=payload.get("series_by"),
    )


def _tile_to_json(tile: Tile) -> Dict[str, Any]:
    return {
        "id": tile.id,
        "source": tile.source.value,
        "kpi_identifier": tile.kpi_identifier,
        "query": _query_to_json(tile.query) if tile.query is not None else None,
        "chart": tile.chart.value,
        "position": {
            "x": tile.position.x,
            "y": tile.position.y,
            "w": tile.position.w,
            "h": tile.position.h,
        },
        "title": tile.title,
        "grain_value": tile.grain_value,
        "description": tile.description,
        "rationale": tile.rationale,
    }


def _tile_from_json(payload: Dict[str, Any]) -> Tile:
    """A row written before 1.2.0 has no ``source`` -- it is a ``kpi`` tile, so the default
    reads it back unchanged; nothing on disk needs migrating."""
    position = payload["position"]
    query = payload.get("query")
    return Tile(
        id=payload["id"],
        kpi_identifier=payload.get("kpi_identifier"),
        chart=ChartKind(payload["chart"]),
        position=TilePosition(
            x=position["x"], y=position["y"], w=position["w"], h=position["h"]
        ),
        title=payload.get("title", ""),
        grain_value=payload.get("grain_value"),
        source=TileSource(payload.get("source") or TileSource.KPI.value),
        query=_query_from_json(query) if query else None,
        description=payload.get("description") or "",
        rationale=payload.get("rationale") or "",
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


def _kpi_existence_guard_sql() -> str:
    """SQL predicate, true only if every tile in a ``%s``-bound tiles JSON array still names
    a KPI that exists in the registry right now -- one ``%s`` parameter (the tiles JSON).

    Imports ``KPI_SCHEMA`` from ``egpt_retail.kpi.store`` lazily, at call time rather than at
    module import time: that module already imports this one at import time
    (``DASHBOARDS_SCHEMA``/``DashboardStore``, for its own ``delete_if_unreferenced``
    reference check -- this is that exact reach, the other direction), so a top-level import
    here would be circular. Reaching into the KPI schema by name/shape mirrors the one
    exception ``delete_if_unreferenced`` already makes for the reverse case: the alternative
    is a check-then-write across two round trips, which is exactly the race this guard exists
    to close (#240 review, round 2 -- the dangling-reference bug closed on the delete side was
    still reachable from this side, a KPI deleted between ``_check_tiles_ready`` and the write
    actually landing).
    """
    from egpt_retail.kpi.store import KPI_SCHEMA as _kpi_schema

    # A `query` tile names no KPI (`kpi_identifier` is JSON null), so it has nothing to
    # guard here -- its own gate is the SQL guard, applied by the router before the write.
    return f"""NOT EXISTS (
        SELECT 1
        FROM jsonb_array_elements(%s::jsonb) AS t
        WHERE t ->> 'kpi_identifier' IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM {_kpi_schema}.kpi_definition AS kd
            WHERE kd.identifier = t ->> 'kpi_identifier'
        )
    )"""


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
        """Raises :class:`DashboardKpiVanishedError` if any tile names a KPI that no longer
        exists at the moment this statement runs -- see that class's docstring."""
        dashboard_id = f"dsh_{uuid.uuid4().hex[:12]}"
        tiles_json = json.dumps([_tile_to_json(t) for t in tiles])
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.dashboard
                    (id, name, description, tiles, filters, refresh_schedule, visibility, owner)
                    SELECT %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s
                    WHERE {_kpi_existence_guard_sql()}
                    RETURNING created_at, updated_at""",
                (
                    dashboard_id,
                    name,
                    description,
                    tiles_json,
                    json.dumps([_filter_to_json(f) for f in filters]),
                    refresh_schedule.value,
                    visibility.value,
                    owner,
                    tiles_json,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                self._connection.rollback()
                raise DashboardKpiVanishedError(
                    "one or more tiles reference a KPI that no longer exists"
                )
            created_at, updated_at = row
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
        edit", and defaulting edit rights to the owner alone is the safer reading.

        Raises :class:`DashboardKpiVanishedError` if ``tiles`` is being replaced and any of
        the new tiles names a KPI that no longer exists at the moment this statement runs --
        leaving tiles unchanged (the ``UNSET`` default) never raises it, since that path
        cannot introduce a dangling reference this call itself creates.
        """
        current = self.get(dashboard_id)
        if current is None or current.owner != owner:
            return None

        tiles_json = (
            json.dumps([_tile_to_json(t) for t in tiles])
            if tiles is not UNSET
            else None
        )

        # (column, provided, value-to-write) -- built explicitly rather than looped over a
        # dict keyed by column name, so "was this argument passed" and "what SQL value does
        # it become" never have to be re-derived from each other.
        candidates: List[Tuple[str, bool, Any]] = [
            ("name", name is not UNSET, name),
            ("description", description is not UNSET, description),
            ("tiles", tiles is not UNSET, tiles_json),
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

        where_sql = "WHERE id = %s AND owner = %s"
        if tiles is not UNSET:
            where_sql += f" AND {_kpi_existence_guard_sql()}"
            params.append(tiles_json)

        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.dashboard SET {", ".join(set_clauses)}
                    {where_sql}""",
                tuple(params),
            )
            matched = cursor.rowcount == 1
        if not matched and tiles is not UNSET:
            # 0 rows affected while replacing tiles: either the dashboard vanished
            # concurrently (a pre-existing race, not introduced here) or the existence guard
            # above tripped. Tell those apart before deciding what to report.
            self._connection.rollback()
            if self.get(dashboard_id) is not None:
                raise DashboardKpiVanishedError(
                    "one or more tiles reference a KPI that no longer exists"
                )
            return None
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

    def upsert_tile(
        self, dashboard_id: str, owner: str, tile: Tile
    ) -> Optional[Dashboard]:
        """Replace the tile with ``tile.id`` in place, or append it when no tile has that id.
        One statement over the JSONB array, so a concurrent edit of another tile is never
        lost. ``None`` if the dashboard does not exist or ``owner`` does not own it; raises
        :class:`DashboardKpiVanishedError` for a ``kpi`` tile whose KPI is gone."""
        tile_json = json.dumps(_tile_to_json(tile))
        one_tile_array = json.dumps([_tile_to_json(tile)])
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.dashboard SET
                        tiles = CASE
                            WHEN EXISTS (
                                SELECT 1 FROM jsonb_array_elements(tiles) AS t
                                WHERE t ->> 'id' = %s
                            )
                            THEN (
                                SELECT COALESCE(jsonb_agg(
                                    CASE WHEN t ->> 'id' = %s THEN %s::jsonb ELSE t END
                                    ORDER BY ord), '[]'::jsonb)
                                FROM jsonb_array_elements(tiles) WITH ORDINALITY AS e(t, ord)
                            )
                            ELSE tiles || %s::jsonb
                        END,
                        updated_at = now()
                    WHERE id = %s AND owner = %s AND {_kpi_existence_guard_sql()}""",
                (
                    tile.id,
                    tile.id,
                    tile_json,
                    tile_json,
                    dashboard_id,
                    owner,
                    one_tile_array,
                ),
            )
            matched = cursor.rowcount == 1
        if not matched:
            self._connection.rollback()
            current = self.get(dashboard_id)
            if current is not None and current.owner == owner:
                raise DashboardKpiVanishedError(
                    "the tile references a KPI that no longer exists"
                )
            return None
        self._connection.commit()
        return self.get(dashboard_id)

    async def aupsert_tile(
        self, dashboard_id: str, owner: str, tile: Tile
    ) -> Optional[Dashboard]:
        """Async variant."""
        return await asyncio.to_thread(self.upsert_tile, dashboard_id, owner, tile)

    def remove_tile(
        self, dashboard_id: str, owner: str, tile_id: str
    ) -> Optional[Dashboard]:
        """Drop the tile with ``tile_id``. ``None`` if the dashboard is not the owner's or
        has no such tile -- the router turns both into a 404."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE {self._schema}.dashboard SET
                        tiles = (
                            SELECT COALESCE(jsonb_agg(t ORDER BY ord), '[]'::jsonb)
                            FROM jsonb_array_elements(tiles) WITH ORDINALITY AS e(t, ord)
                            WHERE t ->> 'id' <> %s
                        ),
                        updated_at = now()
                    WHERE id = %s AND owner = %s
                      AND EXISTS (
                        SELECT 1 FROM jsonb_array_elements(tiles) AS t
                        WHERE t ->> 'id' = %s
                      )""",
                (tile_id, dashboard_id, owner, tile_id),
            )
            matched = cursor.rowcount == 1
        self._connection.commit()
        return self.get(dashboard_id) if matched else None

    async def aremove_tile(
        self, dashboard_id: str, owner: str, tile_id: str
    ) -> Optional[Dashboard]:
        """Async variant."""
        return await asyncio.to_thread(self.remove_tile, dashboard_id, owner, tile_id)

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

    def map_referencing(
        self, kpi_identifiers: Optional[Sequence[str]] = None
    ) -> Mapping[str, Tuple[str, ...]]:
        """Dashboard names referencing each of ``kpi_identifiers`` (every KPI named by any
        tile, when omitted), one query rather than :meth:`list_referencing` called once per
        KPI -- the shape a registry list view needs, where ``list_referencing``'s single-KPI
        form would otherwise mean N round trips for an N-row registry.

        Same reach-across-schemas exception ``list_referencing`` already documents: every
        dashboard regardless of owner or visibility, because "who else still needs this KPI"
        does not depend on who is asking. A KPI absent from the result has no dashboard
        referencing it -- callers building a full map should default missing keys to ``()``
        rather than treat absence as an error.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT tile ->> 'kpi_identifier' AS kpi_identifier,
                           array_agg(DISTINCT d.name ORDER BY d.name) AS names
                    FROM {self._schema}.dashboard AS d,
                         jsonb_array_elements(d.tiles) AS tile
                    WHERE tile ->> 'kpi_identifier' IS NOT NULL
                    {"AND tile ->> 'kpi_identifier' = ANY(%s)" if kpi_identifiers is not None else ""}
                    GROUP BY kpi_identifier""",
                (list(kpi_identifiers),) if kpi_identifiers is not None else (),
            )
            rows = cursor.fetchall()
        return {row[0]: tuple(row[1]) for row in rows}

    async def amap_referencing(
        self, kpi_identifiers: Optional[Sequence[str]] = None
    ) -> Mapping[str, Tuple[str, ...]]:
        """Async variant."""
        return await asyncio.to_thread(self.map_referencing, kpi_identifiers)
