"""Stage 1: raw NDJSON out of object storage and into ``landing.raw_row`` (R4, D4).

Rows are landed **as-is**. Each NDJSON line becomes one ``jsonb`` value plus the ingest
metadata parsed out of its path -- nothing is typed, nothing is coerced, nothing is dropped.
Typing happens in the conformance SQL, where it is inspectable.

**Defects are detected here, in Python, by calling the landing contract's own
:func:`~egpt_retail.contracts.landing.validate_row`.** Restating those rules in SQL would
create a second definition of "conforming" that has to be kept in agreement with the first,
and the agreement would be a test rather than a fact. The contract owns what a defect is; R4
records what it found.

Loading is idempotent. ``landing.raw_row``'s primary key is ``(source_path, line_number)``
and raw is immutable, so re-loading a file inserts nothing. That is what makes "the duplicate
load changes no counts" a property of the schema rather than of the loader being careful.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from egpt_retail.contracts.landing import (
    LANDING_SCHEMAS,
    parse_landing_path,
    resolve_stream_id,
    validate_row,
)
from egpt_retail.storage.base import ObjectStore
from egpt_retail.warehouse.schema import COERCED_COLUMNS
from egpt_retail.warehouse.sql import defect_sql

__all__ = ["LandingResult", "RowDefect", "load_landing", "aload_landing"]

log = logging.getLogger(__name__)

#: Columns COPY writes, in order. ``stream_id`` is generated and ``loaded_at`` defaults, so
#: neither appears here.
_LANDING_COLUMNS = (
    "source_path",
    "line_number",
    "source",
    "dt",
    "ingested_at",
    "payload",
    "load_id",
)


@dataclass(frozen=True)
class RowDefect:
    """One way one landed row fails to conform, ready for ``quality.row_defect``."""

    stream_id: Optional[str]
    source_path: str
    line_number: int
    row_key: Optional[str]
    column_name: Optional[str]
    defect_kind: str
    detail: str
    structural: bool


@dataclass
class LandingResult:
    """What one pass over the raw drop found."""

    files: Tuple[str, ...] = ()
    #: Rows the files carried, per stream. Not rows inserted -- see the column comment on
    #: ``quality.load_stream_count.landed_rows``.
    landed_rows: Dict[str, int] = field(default_factory=dict)
    inserted_rows: Dict[str, int] = field(default_factory=dict)
    #: The files this load read rows for, per stream. Recorded from what was parsed rather
    #: than from what was inserted: a re-load inserts nothing, so provenance taken from
    #: insertions would report "no file" for a pull that was in fact complete.
    source_paths: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    defects: Tuple[RowDefect, ...] = ()

    @property
    def total_landed(self) -> int:
        return sum(self.landed_rows.values())


def _defect_kind(row: Mapping[str, Any], column: str, message: str) -> str:
    """Translate one ``validate_row`` message into a stored defect kind.

    The contract reports in prose because prose is what a person reads. This maps it back to
    a small closed set so the gates can count by kind, and it reads the row to tell "absent"
    from "present but empty" -- a distinction the message does not always draw, and the one
    that tells a connector bug from a source-data gap.
    """
    if "not a parseable ISO-8601 timestamp" in message:
        return "uncoercible"
    if column and row.get(column, None) is None and column not in row:
        return "missing_column"
    if "missing" in message and "null" not in message:
        return "missing_column"
    return "null_or_blank"


def _row_key(stream_id: str, row: Mapping[str, Any]) -> Optional[str]:
    """The key tuple rendered for a human: ``contact/c_501``."""
    schema = LANDING_SCHEMAS.get(stream_id)
    if schema is None:
        return None
    parts = [str(row.get(column)) for column in schema.key_columns if row.get(column)]
    return "/".join(parts) if parts else None


def _column_of(message: str) -> Optional[str]:
    """The column a defect message names, if it names one.

    Every message ``validate_row`` produces quotes the column with ``!r``, so the first
    single-quoted token is it. Parsing prose is not lovely, but it is better than a second
    implementation of the rules that could disagree with the first.
    """
    if "'" not in message:
        return None
    return message.split("'")[1]


def _inspect_row(
    source: Any, path: str, line_number: int, raw: Any
) -> Tuple[Optional[str], List[RowDefect]]:
    """Resolve a row's stream and collect every way it fails to conform."""
    if not isinstance(raw, dict):
        return None, [
            RowDefect(
                stream_id=None,
                source_path=path,
                line_number=line_number,
                row_key=None,
                column_name=None,
                defect_kind="not_an_object",
                detail=f"line is not a JSON object: {type(raw).__name__}",
                structural=True,
            )
        ]
    try:
        stream_id = resolve_stream_id(source, raw)
    except ValueError as exc:
        # resolve_stream_id raises, which would abort a whole file for one bad row. Caught
        # here so an unroutable row is reported and the other rows still land.
        return None, [
            RowDefect(
                stream_id=None,
                source_path=path,
                line_number=line_number,
                row_key=None,
                column_name="record_type",
                defect_kind="unknown_stream",
                detail=str(exc),
                structural=True,
            )
        ]

    schema = LANDING_SCHEMAS[stream_id]
    structural_columns = set(schema.key_columns) | {schema.watermark_column}
    defects: List[RowDefect] = []
    for message in validate_row(stream_id, raw):
        column = _column_of(message)
        defects.append(
            RowDefect(
                stream_id=stream_id,
                source_path=path,
                line_number=line_number,
                row_key=_row_key(stream_id, raw),
                column_name=column,
                defect_kind=_defect_kind(raw, column or "", message),
                detail=message,
                structural=column in structural_columns if column else True,
            )
        )
    return stream_id, defects


def _copy_into_landing(
    connection: Connection, rows: Sequence[Tuple[Any, ...]]
) -> Dict[str, int]:
    """COPY into a temporary table, then insert what is genuinely new.

    COPY is the fastest way into Postgres but has no ``ON CONFLICT``, and idempotence is not
    negotiable here. Staging in a TEMP table buys both: COPY's speed, then one
    ``INSERT ... SELECT ... ON CONFLICT DO NOTHING`` that re-loads cleanly.
    """
    driver = connection.connection.driver_connection
    if driver is None:  # pragma: no cover -- only if the DBAPI connection was detached
        raise RuntimeError(
            "the warehouse connection has no underlying psycopg connection, so COPY is "
            "unavailable; this means the engine was built for a driver other than psycopg"
        )
    with driver.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE _incoming ("
            "source_path text, line_number integer, source text, dt date, "
            "ingested_at timestamptz, payload jsonb, load_id uuid) ON COMMIT DROP"
        )
        columns = ", ".join(_LANDING_COLUMNS)
        with cursor.copy(f"COPY _incoming ({columns}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
        cursor.execute(
            f"INSERT INTO landing.raw_row ({columns}) "
            f"SELECT {columns} FROM _incoming "
            "ON CONFLICT (source_path, line_number) DO NOTHING "
            "RETURNING stream_id"
        )
        inserted: Counter = Counter()
        for (stream_id,) in cursor.fetchall():
            inserted[stream_id if stream_id else "<unrouted>"] += 1
    return dict(inserted)


def load_landing(
    engine: Engine,
    store: ObjectStore,
    load_id: uuid.UUID,
    *,
    paths: Optional[Sequence[str]] = None,
) -> LandingResult:
    """Read every NDJSON object under ``raw/`` into ``landing.raw_row``.

    ``paths`` restricts the load to named objects, which is how the verification scenarios
    load one hand-written file at a time. Unset means everything the store holds.

    Files are folded oldest-``ingested_at`` first, because the store returns them sorted and
    the contract's path convention sorts that way -- the order de-duplication's tie-break
    depends on.
    """
    selected = tuple(paths) if paths is not None else store.list_objects()
    staged: List[Tuple[Any, ...]] = []
    landed: Counter = Counter()
    paths_by_stream: Dict[str, List[str]] = {}
    defects: List[RowDefect] = []

    for path in selected:
        parts = parse_landing_path(path)
        payload = store.get_bytes(path).decode("utf-8")
        for line_number, line in enumerate(payload.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                defects.append(
                    RowDefect(
                        stream_id=None,
                        source_path=path,
                        line_number=line_number,
                        row_key=None,
                        column_name=None,
                        defect_kind="not_an_object",
                        detail=f"line is not valid JSON: {exc.msg}",
                        structural=True,
                    )
                )
                continue
            stream_id, row_defects = _inspect_row(parts.source, path, line_number, raw)
            defects.extend(row_defects)
            key = stream_id if stream_id else "<unrouted>"
            landed[key] += 1
            if path not in paths_by_stream.setdefault(key, []):
                paths_by_stream[key].append(path)
            staged.append(
                (
                    path,
                    line_number,
                    parts.source.value,
                    parts.dt,
                    parts.ingested_at,
                    json.dumps(raw),
                    str(load_id),
                )
            )

    inserted: Dict[str, int] = {}
    with engine.begin() as connection:
        if staged:
            inserted = _copy_into_landing(connection, staged)
        # Only findable once the rows are in landing, because the check calls the same
        # silver.try_* functions the conformance statements do rather than reimplementing
        # "coercible" in Python.
        defects.extend(_uncoercible_defects(connection, selected))
        _write_defects(connection, load_id, defects)

    log.info(
        "load: %d file(s), %d row(s) carried, %d newly landed, %d defect(s)",
        len(selected),
        sum(landed.values()),
        sum(inserted.values()),
        len(defects),
    )
    return LandingResult(
        files=selected,
        landed_rows=dict(landed),
        inserted_rows=inserted,
        source_paths={
            stream_id: tuple(found) for stream_id, found in paths_by_stream.items()
        },
        defects=tuple(defects),
    )


def _uncoercible_defects(
    connection: Connection, paths: Sequence[str]
) -> List[RowDefect]:
    """Present, non-blank numbers that will not cast -- silently nulled by conformance otherwise.

    Scoped to numeric columns. Landing contract 1.1.0 added ``timestamp_columns``, so every
    timestamp R4 casts is now format-checked by ``validate_row`` and reported upstream; but
    nothing in the contract says ``rage_click_count`` is a number, so a malformed count is not
    blank, draws no defect, and would be quietly turned into NULL by ``silver.try_integer``.
    That would contradict ``040_coercion.sql``'s promise that a NULL it returns is never
    silent. This is what keeps the promise true for the half the contract does not cover.

    Reported, never blocking on its own: these are business columns, so the row is kept with a
    NULL exactly as a known-dirty value is, and the defect is what says so.
    """
    if not paths:
        return []
    streams: List[str] = []
    columns: List[str] = []
    kinds: List[str] = []
    for stream_id, coerced in COERCED_COLUMNS.items():
        for column, kind in coerced.items():
            # Timestamps are the contract's job since 1.1.0: `validate_row` format-checks its
            # watermark and its `timestamp_columns`, and has already reported anything
            # malformed. Sweeping them again here would double-report the same row.
            if kind != "integer":
                continue
            streams.append(stream_id)
            columns.append(column)
            kinds.append(kind)
    if not columns:
        return []

    rows = connection.execute(
        text(defect_sql("uncoercible")),
        {
            "paths": list(paths),
            "streams": streams,
            "columns": columns,
            "kinds": kinds,
        },
    ).mappings()

    return [
        RowDefect(
            stream_id=str(row["stream_id"]),
            source_path=str(row["source_path"]),
            line_number=int(row["line_number"]),
            row_key=None,
            column_name=str(row["column_name"]),
            defect_kind="uncoercible",
            detail=(
                f"column {row['column_name']!r} is present but will not cast: "
                f"{str(row['raw_value'])[:80]!r}"
            ),
            # A key or watermark column that will not cast is already excluded from silver by
            # the conformance filter, so it is structural; anything else keeps its row.
            structural=str(row["column_name"])
            in _structural_columns(str(row["stream_id"])),
        )
        for row in rows
    ]


def _structural_columns(stream_id: str) -> frozenset:
    schema = LANDING_SCHEMAS[stream_id]
    return frozenset({*schema.key_columns, schema.watermark_column})


def _write_defects(
    connection: Connection, load_id: uuid.UUID, defects: Sequence[RowDefect]
) -> None:
    if not defects:
        return
    connection.execute(
        text(
            "INSERT INTO quality.row_defect "
            "(load_id, stream_id, source_path, line_number, row_key, column_name, "
            " defect_kind, detail, structural) "
            "VALUES (:load_id, :stream_id, :source_path, :line_number, :row_key, "
            "        :column_name, :defect_kind, :detail, :structural)"
        ),
        [
            {
                "load_id": str(load_id),
                "stream_id": defect.stream_id,
                "source_path": defect.source_path,
                "line_number": defect.line_number,
                "row_key": defect.row_key,
                "column_name": defect.column_name,
                "defect_kind": defect.defect_kind,
                "detail": defect.detail,
                "structural": defect.structural,
            }
            for defect in defects
        ],
    )


async def aload_landing(
    engine: Engine,
    store: ObjectStore,
    load_id: uuid.UUID,
    *,
    paths: Optional[Sequence[str]] = None,
) -> LandingResult:
    """:func:`load_landing`, off the event loop."""
    return await asyncio.to_thread(load_landing, engine, store, load_id, paths=paths)
