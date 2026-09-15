"""``DashboardQueryResultStore`` -- the cached result of every ``query`` tile (contract 1.2.0).

The query-tile equivalent of gold. A ``kpi`` tile reads ``gold.kpi_<identifier>``, filled by
the nightly materialisation; a ``query`` tile reads this table, filled by
:func:`egpt_retail.dashboards.queries.refresh_query_tiles` -- once when the tile is written,
then whenever ``POST /dashboards/{id}/refresh`` asks. Opening a dashboard reads a row here
and never runs the tile's SQL, which is the build plan's rule for dashboards ("never a live
aggregation") carried over to the new tile kind.

One row per ``(dashboard_id, tile_id)``. ``error`` is set, and ``rows`` empty, when the last
run was refused by the guard or failed in the warehouse -- kept, not deleted, so the tile can
say *why* it has nothing rather than looking never-computed.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = ["QUERY_RESULTS_SCHEMA", "DashboardQueryResultStore", "QueryResultRecord"]

#: Same schema as the dashboard table itself.
QUERY_RESULTS_SCHEMA = "dashboards"


@dataclass(frozen=True)
class QueryResultRecord:
    dashboard_id: str
    tile_id: str
    computed_at: datetime
    columns: Tuple[str, ...]
    rows: Tuple[Dict[str, Any], ...]
    duration_ms: int
    error: Optional[str] = None
    #: The statement that actually ran (the guard's rewrite, when it made one).
    sql: str = ""


def _ddl(schema: str) -> Tuple[str, ...]:
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""CREATE TABLE IF NOT EXISTS {schema}.dashboard_query_result (
            dashboard_id text        NOT NULL,
            tile_id      text        NOT NULL,
            computed_at  timestamptz NOT NULL DEFAULT now(),
            columns      jsonb       NOT NULL DEFAULT '[]'::jsonb,
            rows         jsonb       NOT NULL DEFAULT '[]'::jsonb,
            duration_ms  integer     NOT NULL DEFAULT 0,
            error        text,
            sql          text        NOT NULL DEFAULT '',
            PRIMARY KEY (dashboard_id, tile_id)
        )""",
        f"""COMMENT ON TABLE {schema}.dashboard_query_result IS
        'The cached result of each query tile, replaced on refresh. Read by '
        'GET /dashboards/{{id}}/data; a dashboard open never runs a tile''s SQL.'""",
    )


_COLUMNS = "dashboard_id, tile_id, computed_at, columns, rows, duration_ms, error, sql"


def _record(row: Sequence[Any]) -> QueryResultRecord:
    columns = row[3]
    if isinstance(columns, str):
        columns = json.loads(columns)
    rows = row[4]
    if isinstance(rows, str):
        rows = json.loads(rows)
    return QueryResultRecord(
        dashboard_id=row[0],
        tile_id=row[1],
        computed_at=row[2],
        columns=tuple(columns),
        rows=tuple(rows),
        duration_ms=int(row[5] or 0),
        error=row[6],
        sql=row[7] or "",
    )


class DashboardQueryResultStore:
    """The query-result table. The connection is injected; the caller owns its lifetime."""

    def __init__(self, connection: Any, schema: str = QUERY_RESULTS_SCHEMA) -> None:
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

    def put(
        self,
        dashboard_id: str,
        tile_id: str,
        *,
        columns: Sequence[str],
        rows: Sequence[Mapping[str, Any]],
        duration_ms: int,
        error: Optional[str] = None,
        sql: str = "",
    ) -> QueryResultRecord:
        """Replace the tile's cached result."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""INSERT INTO {self._schema}.dashboard_query_result
                        ({_COLUMNS})
                    VALUES (%s, %s, now(), %s::jsonb, %s::jsonb, %s, %s, %s)
                    ON CONFLICT (dashboard_id, tile_id) DO UPDATE SET
                        computed_at = now(),
                        columns = EXCLUDED.columns,
                        rows = EXCLUDED.rows,
                        duration_ms = EXCLUDED.duration_ms,
                        error = EXCLUDED.error,
                        sql = EXCLUDED.sql
                    RETURNING {_COLUMNS}""",
                (
                    dashboard_id,
                    tile_id,
                    json.dumps(list(columns)),
                    json.dumps([dict(row) for row in rows]),
                    int(duration_ms),
                    error,
                    sql,
                ),
            )
            row = cursor.fetchone()
        self._connection.commit()
        return _record(row)

    async def aput(
        self,
        dashboard_id: str,
        tile_id: str,
        *,
        columns: Sequence[str],
        rows: Sequence[Mapping[str, Any]],
        duration_ms: int,
        error: Optional[str] = None,
        sql: str = "",
    ) -> QueryResultRecord:
        return await asyncio.to_thread(
            self.put,
            dashboard_id,
            tile_id,
            columns=columns,
            rows=rows,
            duration_ms=duration_ms,
            error=error,
            sql=sql,
        )

    def delete(self, dashboard_id: str, tile_id: Optional[str] = None) -> int:
        """Drop one tile's result, or every result of a dashboard when ``tile_id`` is
        ``None``. Returns how many rows went."""
        with self._connection.cursor() as cursor:
            if tile_id is None:
                cursor.execute(
                    f"DELETE FROM {self._schema}.dashboard_query_result WHERE dashboard_id = %s",
                    (dashboard_id,),
                )
            else:
                cursor.execute(
                    f"""DELETE FROM {self._schema}.dashboard_query_result
                        WHERE dashboard_id = %s AND tile_id = %s""",
                    (dashboard_id, tile_id),
                )
            removed = cursor.rowcount
        self._connection.commit()
        return int(removed or 0)

    async def adelete(self, dashboard_id: str, tile_id: Optional[str] = None) -> int:
        return await asyncio.to_thread(self.delete, dashboard_id, tile_id)

    # --- reading -----------------------------------------------------------------------

    def get_many(self, dashboard_id: str) -> Dict[str, QueryResultRecord]:
        """Every cached result of a dashboard, keyed by tile id."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT {_COLUMNS} FROM {self._schema}.dashboard_query_result
                    WHERE dashboard_id = %s""",
                (dashboard_id,),
            )
            rows: List[Sequence[Any]] = cursor.fetchall()
        return {row[1]: _record(row) for row in rows}

    async def aget_many(self, dashboard_id: str) -> Dict[str, QueryResultRecord]:
        return await asyncio.to_thread(self.get_many, dashboard_id)
