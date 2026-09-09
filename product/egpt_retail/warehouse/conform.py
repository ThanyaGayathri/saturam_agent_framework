"""Stage 2: landing into the five silver entities (R4, G8).

This module runs SQL and counts rows. It contains no conformance logic, because the
conformance specification per entity lives in ``sql/conform/<entity>.sql`` -- one file, one
statement, keys and de-duplication and type coercion all visible in it. That separation is
the standing decision (D6, and "keep transformations as inspectable SQL, never as code, so
they can be handed over later"), and it is the reason a data engineer can read what happens
to a row without reading Python.

Each statement is a full recompute over all of landing for its stream, not an incremental
pass. That is deliberate: the result then depends only on landing's contents, so re-running
a load produces the same winner and writes nothing -- which is what makes "the duplicate load
changes no counts" true by construction rather than by care.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Dict, Mapping, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from egpt_retail.contracts.landing import LANDING_SCHEMAS
from egpt_retail.warehouse.schema import silver_table
from egpt_retail.warehouse.sql import conform_sql

__all__ = [
    "ConformResult",
    "conform_silver",
    "aconform_silver",
    "record_stream_counts",
    "arecord_stream_counts",
]

log = logging.getLogger(__name__)

ConformResult = Mapping[str, Tuple[int, int]]
"""Per stream: (rows that reached the dedup, total rows in silver afterwards)."""


def _conformed_count(
    connection: Connection, stream_id: str, source_paths: Sequence[str]
) -> int:
    """Rows of this stream **from the files this load read** that carry every structural column.

    Counted with the same predicate the conformance statement filters on, so "conformed" here
    means the same thing it means there. Rows excluded by it are exactly the ones whose
    structural defects were recorded at load time.

    Scoped by ``source_path``, not by ``load_id`` and not unscoped. Unscoped, this counted all
    of landing for the stream forever, which put a cumulative-history number in a per-load
    column. Scoped by ``load_id`` it would read zero on an idempotent re-load, because such a
    load inserts nothing and so owns none of the rows its pull carried. The files the load
    read are the only identifier that means the same thing on a first load and a re-load.
    """
    if not source_paths:
        return 0
    schema = LANDING_SCHEMAS[stream_id]
    structural = list(dict.fromkeys([*schema.key_columns, schema.watermark_column]))
    predicate = " AND ".join(
        f"NULLIF(btrim(payload ->> '{column}'), '') IS NOT NULL"
        for column in structural
    )
    return int(
        connection.execute(
            text(
                "SELECT COUNT(*) FROM landing.raw_row "
                f"WHERE stream_id = :stream_id AND source_path = ANY(:paths) AND {predicate}"
            ),
            {"stream_id": stream_id, "paths": list(source_paths)},
        ).scalar_one()
    )


def conform_silver(
    engine: Engine,
    load_id: uuid.UUID,
    source_paths: Optional[Mapping[str, Sequence[str]]] = None,
) -> ConformResult:
    """Run all five conformance statements and record the per-stream counts.

    One transaction for the whole set. Silver is what gold reads, and half a conformance is
    a set of entities that disagree with each other -- referential integrity across streams
    would be measuring a state that never really existed.

    ``source_paths`` is what the load read, per stream, and scopes the conformed count to it.
    """
    read = source_paths or {}
    counts: Dict[str, Tuple[int, int]] = {}
    with engine.begin() as connection:
        for stream_id in LANDING_SCHEMAS:
            connection.execute(
                conform_sql_statement(stream_id), {"load_id": str(load_id)}
            )
            conformed = _conformed_count(connection, stream_id, read.get(stream_id, ()))
            silver_rows = int(
                connection.execute(
                    text(f"SELECT COUNT(*) FROM {silver_table(stream_id)}")
                ).scalar_one()
            )
            counts[stream_id] = (conformed, silver_rows)
            log.info(
                "conform: %s -- %d conformed, %d in silver",
                stream_id,
                conformed,
                silver_rows,
            )
    return counts


def conform_sql_statement(stream_id: str):
    """The stream's conformance statement, as a bindable SQLAlchemy construct."""
    return text(conform_sql(stream_id))


async def aconform_silver(
    engine: Engine,
    load_id: uuid.UUID,
    source_paths: Optional[Mapping[str, Sequence[str]]] = None,
) -> ConformResult:
    """:func:`conform_silver`, off the event loop."""
    return await asyncio.to_thread(conform_silver, engine, load_id, source_paths)


def record_stream_counts(
    engine: Engine,
    load_id: uuid.UUID,
    landed: Mapping[str, int],
    inserted: Mapping[str, int],
    conformed: ConformResult,
    source_paths: Optional[Mapping[str, Sequence[str]]] = None,
) -> None:
    """Write the per-stream counts the gates measure against.

    Every stream gets a row, including one that landed nothing. A stream absent from this
    table is indistinguishable from a stream nobody looked at, and the row-count gate would
    then have nothing to notice when a source stops delivering entirely -- which is the
    failure it most needs to catch.
    """
    read = source_paths or {}
    rows = []
    for stream_id in LANDING_SCHEMAS:
        conformed_rows, silver_rows = conformed.get(stream_id, (0, 0))
        rows.append(
            {
                "load_id": str(load_id),
                "stream_id": stream_id,
                "landed_rows": int(landed.get(stream_id, 0)),
                "inserted_rows": int(inserted.get(stream_id, 0)),
                "conformed_rows": int(conformed_rows),
                "silver_rows": int(silver_rows),
                "source_paths": list(read.get(stream_id, ())),
            }
        )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO quality.load_stream_count "
                "(load_id, stream_id, landed_rows, inserted_rows, conformed_rows, "
                " silver_rows, source_paths) "
                "VALUES (:load_id, :stream_id, :landed_rows, :inserted_rows, "
                "        :conformed_rows, :silver_rows, :source_paths) "
                "ON CONFLICT (load_id, stream_id) DO UPDATE "
                "SET landed_rows = EXCLUDED.landed_rows, "
                "    inserted_rows = EXCLUDED.inserted_rows, "
                "    conformed_rows = EXCLUDED.conformed_rows, "
                "    silver_rows = EXCLUDED.silver_rows, "
                "    source_paths = EXCLUDED.source_paths"
            ),
            rows,
        )


async def arecord_stream_counts(
    engine: Engine,
    load_id: uuid.UUID,
    landed: Mapping[str, int],
    inserted: Mapping[str, int],
    conformed: ConformResult,
    source_paths: Optional[Mapping[str, Sequence[str]]] = None,
) -> None:
    await asyncio.to_thread(
        record_stream_counts, engine, load_id, landed, inserted, conformed, source_paths
    )
