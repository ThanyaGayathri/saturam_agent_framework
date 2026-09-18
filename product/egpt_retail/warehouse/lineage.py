"""Per-table load freshness for a KPI's source tables (the detail panel's "data it reads"
section): the same ``quality.load_run``/``quality.load_stream_count`` rows the load pipeline
itself writes (``warehouse/loads.py``), read back per stream rather than per load.

Read-only and best-effort. A KPI's source table is a plain string on the control-plane
``KpiDefinition`` (``contracts/kpi.py``); the load history that says when it last loaded lives
in the warehouse database -- a different connection, per ``020_quality.sql``'s own note that
Postgres cannot join across databases. So this is two reads joined in application code, not one
query, and it is enrichment for a KPI's presentation, not core KPI data: a table nothing has
loaded yet, an unresolvable table name, or an unreachable warehouse all fall out of the result
quietly rather than raising -- the caller already has the full ``source_tables`` list to show
what's missing.
"""

from __future__ import annotations

import asyncio
from datetime import timezone
from typing import Any, Dict, List, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

from egpt_retail.warehouse.schema import SILVER_TABLES

__all__ = ["source_table_status", "asource_table_status"]

#: `SILVER_TABLES` maps stream id -> silver table stem; a KPI's `source_tables` are stems
#: (`contracts/kpi.py`'s `referenced_tables()`), so this is the reverse lookup, built once.
_STREAM_ID_BY_STEM: Mapping[str, str] = {
    stem: stream_id for stream_id, stem in SILVER_TABLES.items()
}


def _iso(value: Any) -> Any:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def source_table_status(tables: Sequence[str], engine: Engine) -> List[Dict[str, Any]]:
    """One row per ``tables`` entry that resolves to a known stream with at least one load,
    each that stream's most recent (``finished_at``-latest) ``load_run``/``load_stream_count``
    pair. Order follows ``tables``; a table absent from the result never loaded, isn't a
    recognised stream id, or (on any warehouse error) simply couldn't be read this time.
    """
    stream_id_by_table = {
        table: _STREAM_ID_BY_STEM[table]
        for table in tables
        if table in _STREAM_ID_BY_STEM
    }
    if not stream_id_by_table:
        return []
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text(
                    "SELECT DISTINCT ON (lsc.stream_id) lsc.stream_id, lsc.silver_rows, "
                    "lr.finished_at, lr.verdict "
                    "FROM quality.load_stream_count lsc "
                    "JOIN quality.load_run lr ON lr.load_id = lsc.load_id "
                    "WHERE lsc.stream_id = ANY(:stream_ids) AND lr.finished_at IS NOT NULL "
                    "ORDER BY lsc.stream_id, lr.finished_at DESC"
                ),
                {"stream_ids": list(stream_id_by_table.values())},
            )
            by_stream = {str(row["stream_id"]): dict(row) for row in result.mappings()}
    except Exception:
        # Enrichment, not core data (see module docstring) -- a KPI's own detail must still
        # render when the warehouse is unreachable or mid-migration, not 500 on its behalf.
        return []
    return [
        {
            "table": table,
            "loaded_at": _iso(by_stream[stream_id]["finished_at"]),
            "row_count": int(by_stream[stream_id]["silver_rows"]),
            "load_verdict": by_stream[stream_id]["verdict"],
        }
        for table, stream_id in stream_id_by_table.items()
        if stream_id in by_stream
    ]


async def asource_table_status(
    tables: Sequence[str], engine: Engine
) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(source_table_status, tables, engine)
