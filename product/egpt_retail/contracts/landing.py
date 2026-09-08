"""The landing contract -- storage layout and file shape between Lane C and Lane R.

Lane C owns the three connectors (analytics, CRM, session replay) and writes their raw
extracts to Azure object storage; Lane R never watches a connector run, only what landed
there (D4). This module is the seam: the pure, typed half of
:doc:`LANDING_CONTRACT.md </product/egpt_retail/contracts/LANDING_CONTRACT.md>` that Lane
R's loader -- and later C2-C4 -- import instead of re-deriving path parsing and row
validation from prose.

Everything here is pure. There is no I/O, so the repository's sync/async rule does not
apply (see the construction exemption in ``CLAUDE.md``); reading an actual file out of
object storage is R4's job, not this module's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import FrozenSet, Mapping, Tuple

__all__ = [
    "CONTRACT_VERSION",
    "SourceName",
    "LandingSchema",
    "LandingPathParts",
    "LANDING_SCHEMAS",
    "landing_schema",
    "landing_path",
    "parse_landing_path",
    "resolve_stream_id",
    "validate_row",
]

#: Bumped on any breaking change, per the change protocol in
#: ``product/egpt_retail/contracts/CHANGELOG.md``. Additive changes do not bump it.
CONTRACT_VERSION = "1.0.0"

#: ``strftime``/``strptime`` shape for the timestamp embedded in a landed filename.
#: Compact and colon-free on purpose -- a raw ISO timestamp survives every object store and
#: every local filesystem this fixture-driven verification runs on.
_FILENAME_TS_FORMAT = "%Y%m%dT%H%M%SZ"

#: ``raw/{source}/dt={YYYY-MM-DD}/{source}-{ingested_at}.ndjson``, relative to the landing
#: root. The two ``source`` captures must agree -- see :func:`parse_landing_path`.
_PATH_RE = re.compile(
    r"^raw/(?P<source>[a-z_]+)/dt=(?P<date>\d{4}-\d{2}-\d{2})/"
    r"(?P<source2>[a-z_]+)-(?P<ts>\d{8}T\d{6}Z)\.ndjson$"
)


class SourceName(str, Enum):
    """The three sources Lane C connects. Fixed for P0 -- a fourth source is a new contract."""

    ANALYTICS = "analytics"
    CRM = "crm"
    SESSION_REPLAY = "session_replay"


@dataclass(frozen=True)
class LandingSchema:
    """One landed record shape: what one row represents, its keys, and its watermark.

    CRM lands three shapes -- contact, deal, deal stage history -- inside the *same* source
    and the same file, distinguished by ``record_type`` (see :func:`resolve_stream_id`).
    Analytics and session replay each land exactly one shape. ``stream_id`` is therefore the
    unit this module validates against, not ``source``.

    Construction validates the schema against itself, the same discipline
    :class:`~egpt_retail.contracts.kpi.KpiDefinition` applies to a formula: a key or
    watermark column that is not also a required column is a contradiction no caller should
    have to discover at runtime.
    """

    stream_id: str
    source: SourceName
    #: One sentence: what one row represents.
    grain: str
    key_columns: Tuple[str, ...]
    watermark_column: str
    required_columns: Tuple[str, ...]
    #: Required columns permitted to be ``null`` on a conforming row -- either because the
    #: value is legitimately sometimes unknown (session replay's identity fields), or because
    #: the source system's data is known-dirty today (see ``LANDING_CONTRACT.md``). Never a
    #: key or the watermark column: those are structural, not business, fields.
    nullable_columns: FrozenSet[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.stream_id:
            raise ValueError("stream_id must not be empty")
        if not self.key_columns:
            raise ValueError(f"{self.stream_id}: key_columns must not be empty")
        missing_keys = set(self.key_columns) - set(self.required_columns)
        if missing_keys:
            raise ValueError(
                f"{self.stream_id}: key column(s) {sorted(missing_keys)} are not listed in "
                "required_columns"
            )
        if self.watermark_column not in self.required_columns:
            raise ValueError(
                f"{self.stream_id}: watermark_column {self.watermark_column!r} is not "
                "listed in required_columns"
            )
        structural = set(self.key_columns) | {self.watermark_column}
        clash = self.nullable_columns & structural
        if clash:
            raise ValueError(
                f"{self.stream_id}: nullable_columns {sorted(clash)} are key or watermark "
                "columns, which must never be null"
            )
        unknown_nullable = self.nullable_columns - set(self.required_columns)
        if unknown_nullable:
            raise ValueError(
                f"{self.stream_id}: nullable_columns {sorted(unknown_nullable)} are not "
                "required_columns"
            )


def _schemas() -> Tuple[LandingSchema, ...]:
    return (
        LandingSchema(
            stream_id="analytics.event",
            source=SourceName.ANALYTICS,
            grain=(
                "one row per event, keyed on the user, the session, the event name and "
                "when it occurred"
            ),
            key_columns=("user_id", "session_id", "event_name", "occurred_at"),
            watermark_column="occurred_at",
            required_columns=(
                "user_id",
                "session_id",
                "event_name",
                "occurred_at",
                "brand",
                "product_id",
            ),
        ),
        LandingSchema(
            stream_id="crm.contact",
            source=SourceName.CRM,
            grain="one row per contact, as of its last change",
            key_columns=("record_type", "object_id"),
            watermark_column="last_modified_at",
            required_columns=(
                "record_type",
                "object_id",
                "last_modified_at",
                "email",
                "brand",
            ),
        ),
        LandingSchema(
            stream_id="crm.deal",
            source=SourceName.CRM,
            grain="one row per deal, as of its last change",
            key_columns=("record_type", "object_id"),
            watermark_column="last_modified_at",
            required_columns=(
                "record_type",
                "object_id",
                "last_modified_at",
                "stage",
                "quote_request_source_url",
            ),
        ),
        LandingSchema(
            stream_id="crm.deal_stage_history",
            source=SourceName.CRM,
            grain="one row per deal stage transition",
            key_columns=("record_type", "object_id"),
            watermark_column="last_modified_at",
            required_columns=(
                "record_type",
                "object_id",
                "last_modified_at",
                "deal_id",
                "stage",
                "entered_at",
            ),
        ),
        LandingSchema(
            stream_id="session_replay.session",
            source=SourceName.SESSION_REPLAY,
            grain="one row per session",
            key_columns=("session_id",),
            watermark_column="session_start_at",
            required_columns=(
                "session_id",
                "session_start_at",
                "rage_click_count",
                "dead_click_count",
                "script_error_count",
                "session_url",
                "identity_at_login",
                "identity_at_submit",
            ),
            # A session may end before login, or be abandoned before submission -- unlike
            # CRM's null brand, an unset identity here is not dirty data, it is the truth.
            nullable_columns=frozenset({"identity_at_login", "identity_at_submit"}),
        ),
    )


#: Every landed record shape, keyed by ``stream_id``. Frozen at import time -- a fifth shape
#: is a contract change, not a runtime registration. ``MappingProxyType`` makes that a runtime
#: guarantee, not just a docstring claim: ``LANDING_SCHEMAS["x"] = ...`` raises ``TypeError``.
LANDING_SCHEMAS: Mapping[str, LandingSchema] = MappingProxyType(
    {s.stream_id: s for s in _schemas()}
)

#: The one shape a source lands, for the two sources that only ever land one. CRM has no
#: entry here -- see :func:`resolve_stream_id`.
_SINGLE_STREAM: Mapping[SourceName, str] = {
    SourceName.ANALYTICS: "analytics.event",
    SourceName.SESSION_REPLAY: "session_replay.session",
}


def landing_schema(stream_id: str) -> LandingSchema:
    """Look up a landed record shape by its stream id, or fail with the id that's wrong."""
    try:
        return LANDING_SCHEMAS[stream_id]
    except KeyError as exc:
        raise ValueError(f"unknown stream_id {stream_id!r}") from exc


def resolve_stream_id(source: SourceName, row: Mapping[str, object]) -> str:
    """Which :class:`LandingSchema` a row conforms to.

    Analytics and session replay land one shape each. CRM lands three -- contact, deal, deal
    stage history -- multiplexed into the same file and distinguished by ``record_type``,
    which is CRM's own resolution of "one row per what": the source's *what* is a CRM change
    event, not a single flat grain.
    """
    if not isinstance(row, Mapping):
        # NDJSON permits any JSON value per line -- a bare string, list or number is a
        # legal *file*, illegal per this contract's "one JSON object per line" row grain.
        # Refusing it here, with a message naming what it actually is, is what turns an
        # implementation-leaking AttributeError on the ``.get()`` below into an intentional
        # one -- consistent with the unknown-record_type refusal further down.
        raise ValueError(f"row is not a JSON object: {type(row).__name__}")
    if source is not SourceName.CRM:
        return _SINGLE_STREAM[source]
    record_type = row.get("record_type")
    if not isinstance(record_type, str) or not record_type:
        raise ValueError("crm row is missing a non-empty 'record_type' field")
    stream_id = f"crm.{record_type}"
    if stream_id not in LANDING_SCHEMAS:
        raise ValueError(f"crm row has unknown record_type {record_type!r}")
    return stream_id


def _parses_as_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _is_blank(value: object) -> bool:
    """``None``, or a string with nothing in it but whitespace.

    Both count as the same "not actually filled in" signal a required-but-known-dirty column
    (CRM's ``brand``) exists to catch -- treating only literal ``null`` as dirty would let a
    connector paper over the gap with ``""`` and pass clean.
    """
    return value is None or (isinstance(value, str) and not value.strip())


def validate_row(stream_id: str, row: Mapping[str, object]) -> Tuple[str, ...]:
    """Every way a row can fail to conform, reported rather than dropped.

    An empty result means the row conforms. Structural fields (the key columns and the
    watermark column) are checked unconditionally: a null key or an unparseable watermark
    breaks the loader's own logic, not just a downstream business rule. Every other required
    column is checked against :attr:`LandingSchema.nullable_columns`, which is where a
    known-dirty source field (CRM's ``brand``) and a legitimately-sometimes-unknown one
    (session replay's identity fields) are told apart.

    ``row`` is untrusted input -- one parsed JSON line -- so this never raises on a
    malformed shape (not an object; a key column holding a list or dict rather than a
    scalar). Both are reported as defects rather than left to crash whatever reads the
    result, which for the key-column case is this contract's own downstream dedup: a list
    or dict is unhashable, and grouping by key is exactly what a caller does with it next.
    """
    schema = landing_schema(stream_id)
    if not isinstance(row, Mapping):
        return (f"row is not a JSON object: {type(row).__name__}",)

    defects: list[str] = []
    structural = set(schema.key_columns) | {schema.watermark_column}

    for column in schema.required_columns:
        if column in structural:
            continue
        if column not in row:
            defects.append(f"missing required column {column!r}")
        elif _is_blank(row[column]) and column not in schema.nullable_columns:
            defects.append(f"required column {column!r} is null or blank")

    for column in schema.key_columns:
        value = row.get(column)
        if _is_blank(value):
            defects.append(f"key column {column!r} is missing or null")
        elif not isinstance(value, (str, int, float, bool)):
            # A key column must be a scalar: every current key column is string-typed, and
            # this contract's own dedup groups rows by a tuple of key values, which requires
            # each to be hashable. A list or dict is legal NDJSON and illegal here.
            defects.append(
                f"key column {column!r} is not a scalar value: {type(value).__name__}"
            )

    watermark = row.get(schema.watermark_column)
    watermark_already_reported = _is_blank(watermark) and (
        schema.watermark_column in schema.key_columns
    )
    if _is_blank(watermark):
        if not watermark_already_reported:
            defects.append(
                f"watermark column {schema.watermark_column!r} is missing or null"
            )
    elif not _parses_as_timestamp(watermark):
        defects.append(
            f"watermark column {schema.watermark_column!r} is not a parseable ISO-8601 "
            f"timestamp: {watermark!r}"
        )

    return tuple(defects)


@dataclass(frozen=True)
class LandingPathParts:
    """What a landing path names, decomposed."""

    source: SourceName
    dt: date
    ingested_at: datetime


def landing_path(source: SourceName, dt: date, ingested_at: datetime) -> str:
    """``raw/{source}/dt={date}/{source}-{ingested_at}.ndjson``, relative to the landing root.

    ``ingested_at`` names the file, not the date partition -- a re-pulled window lands a new
    file under the same ``dt=`` partition rather than overwriting the earlier one, which is
    the landing contract's re-run clause made structural: raw is append-only, and
    de-duplication is downstream (Lane R's R4), never a write to this path.

    ``ingested_at`` must be timezone-aware. A naive value is refused rather than assumed to
    be UTC: ``astimezone`` on a naive datetime interprets it as the *process's local
    timezone*, so the common ``datetime.utcnow()`` idiom -- naive, but already UTC wall-clock
    -- would silently shift on any host not itself running in UTC, corrupting the ordering
    this filename exists to make replayable.
    """
    if ingested_at.tzinfo is None:
        raise ValueError(
            "ingested_at must be timezone-aware -- a naive datetime is ambiguous between "
            "'already UTC' (e.g. datetime.utcnow()) and 'local time', and astimezone() "
            "silently assumes the latter"
        )
    ts = ingested_at.astimezone(timezone.utc).strftime(_FILENAME_TS_FORMAT)
    return f"raw/{source.value}/dt={dt.isoformat()}/{source.value}-{ts}.ndjson"


def parse_landing_path(path: str) -> LandingPathParts:
    """The inverse of :func:`landing_path`. Refuses anything that doesn't round-trip.

    Every rejection -- an unmatched shape, an unknown source, or a regex-matching but
    calendar-invalid date (``dt=2026-13-40``) -- raises the same wrapped message rather than
    leaking a raw stdlib exception for some cases and not others.
    """
    match = _PATH_RE.match(path)
    if not match or match.group("source") != match.group("source2"):
        raise ValueError(f"path does not conform to the landing convention: {path!r}")
    try:
        source = SourceName(match.group("source"))
        dt = date.fromisoformat(match.group("date"))
        ingested_at = datetime.strptime(match.group("ts"), _FILENAME_TS_FORMAT).replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError(
            f"path does not conform to the landing convention: {path!r}"
        ) from exc
    return LandingPathParts(source=source, dt=dt, ingested_at=ingested_at)
