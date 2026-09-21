"""R5's two executors: ``materialise_gold`` (the nightly writer) and ``read_gold`` (the reader).

Both ``async def`` under one name each, per the registry convention's I/O exemption. Both
populate R7's provenance bag through the exported ``note_*`` helpers, because every executor
on a path that ends in ``record_provenance`` must -- the nightly chain has no provenance step
today, but a chain that appends one gets the datasets, statements and freshness stamps for free.

``materialise_gold`` is the last step of the ``nightly-refresh`` workflow, after R4's six.
``decide_load_verdict`` returns ``stop=True`` on a blocked load, so on a blocked night this
step never starts: the run completes, the schedule reports success, and gold is untouched --
which is the correct outcome, and the cookbook asserts exactly that shape so nobody mistakes
"success" for "gold was rebuilt". The refusal below covers the other way in: a hand run of
this step alone after a blocked night.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from egpt.exceptions import SQLGuardRefusal
from egpt.guardrails.sql_guard import SQLGuard
from egpt.workflow.types import StepInput, StepOutput
from sqlalchemy import text
from sqlalchemy.engine import Engine

from egpt_retail.app.db import (
    build_warehouse_engine,
    build_warehouse_readonly_engine,
    psycopg_dsn,
)
from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.app.sql_guard import build_warehouse_read_guard
from egpt_retail.contracts.kpi import KpiDefinition, assess_registry
from egpt_retail.contracts.semantic import SemanticRead, available_tables
from egpt_retail.executors.provenance import (
    note_dataset_read,
    note_freshness,
    note_sql_execution,
)
from egpt_retail.kpi.store import KpiRegistryStore
from egpt_retail.warehouse.gold import (
    FAILED,
    MATERIALISED,
    SKIPPED,
    KpiRunResult,
    alatest_as_of,
    amaterialise_registry,
    read_gold_history_statement,
    read_gold_statement,
)
from egpt_retail.warehouse.loads import LoadLock
from egpt_retail.warehouse.schema import GOLD_SCHEMA, SILVER_SCHEMA, gold_view

__all__ = [
    "GOLD_EXECUTORS",
    "materialise_gold",
    "read_gold",
    "last_load_verdict",
    "GOLD_ROW_METADATA_KEYS",
    "grain_key_columns",
    "row_for_grain_value",
]

log = logging.getLogger(__name__)

#: Every column a gold view row carries besides whatever grain-key columns the view itself
#: projects for a given KPI (``warehouse/gold.py``'s ``view_statements``/``classify_result_
#: columns``). One definition, shared by every consumer of a ``read_gold`` row that needs to
#: tell a dimension column apart from bookkeeping -- ``executors/dashboards/data.py``'s tile
#: rendering and ``executors/alerts.py``'s scope matching both used to carry their own copy of
#: this set; a new metadata column here (``executors/gold.py``'s own row shape) now only needs
#: updating once.
GOLD_ROW_METADATA_KEYS = frozenset(
    {"as_of", "value", "computed_at", "source_snapshot", "run_id"}
)


def grain_key_columns(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    """The non-metadata (grain-key) column names the first of ``rows`` carries -- assumes
    every row of one KPI's result set shares the same shape, true for a gold view's rows."""
    if not rows:
        return []
    return [key for key in rows[0] if key not in GOLD_ROW_METADATA_KEYS]


def row_for_grain_value(
    rows: Sequence[Mapping[str, Any]], grain_value: Any
) -> Optional[Mapping[str, Any]]:
    """The first row where any non-metadata column equals ``grain_value``, whichever column
    that turns out to be.

    This is the loose match every caller used before a caller could name the column outright
    (``AlertScope.grain_column``, R12): ambiguous when a row carries more than one grain-key
    column, since a value that happens to match two different columns silently picks whichever
    is found first. Kept for callers with no way to name the column (``dashboards/data.py``'s
    tile rendering, whose ``Tile`` contract has no ``grain_column``); a caller that can name
    the column should match against it directly instead (``executors/alerts.py``'s own
    ``_select_row``).
    """
    target = str(grain_value)
    for row in rows:
        for key, value in row.items():
            if key not in GOLD_ROW_METADATA_KEYS and str(value) == target:
                return row
    return None


def _options(step_input: StepInput) -> Mapping[str, Any]:
    return step_input.additional_data or {}


#: How far back a caller-supplied `as_of` may reach -- generous enough for a real backfill,
#: tight enough to catch a fat-fingered date (a four-digit year typo, an accidental epoch
#: value) before it reaches `gold.kpi_value` and corrupts `latest_as_of()` for every reader.
_AS_OF_MAX_LOOKBACK_DAYS = 1095


def _validate_as_of(value: date) -> date:
    """Bounds-checks an explicit ``as_of`` against ``[today - _AS_OF_MAX_LOOKBACK_DAYS,
    today]`` -- the actual choke point, shared by :func:`_as_of` (``materialise_gold``'s own
    resolution, which *defaults* to today when ``as_of`` is absent) and ``read_gold`` (below;
    its resolution instead defaults an absent ``as_of`` to a KPI's own latest materialised date
    via ``alatest_as_of``, so it cannot reuse ``_as_of`` outright -- only this check, on the
    date it already parsed). A future date would otherwise become the KPI's own
    ``latest_as_of()`` (``MAX(as_of) WHERE status='materialised'``), silently corrupting every
    viewer's default series/trend window until a later date is materialised.
    """
    today = datetime.now(timezone.utc).date()
    if value > today:
        raise ValueError(
            f"as_of {value.isoformat()} is in the future; today (UTC) is {today.isoformat()}."
        )
    floor = today - timedelta(days=_AS_OF_MAX_LOOKBACK_DAYS)
    if value < floor:
        raise ValueError(
            f"as_of {value.isoformat()} is more than {_AS_OF_MAX_LOOKBACK_DAYS} days in the "
            f"past (earliest allowed: {floor.isoformat()})."
        )
    return value


def _as_of(options: Mapping[str, Any]) -> date:
    """Today (UTC) when ``options["as_of"]`` is absent, else that date, bounds-checked by
    :func:`_validate_as_of`. ``materialise_gold``'s own resolution -- ``read_gold`` (below)
    parses and validates an explicit ``as_of`` the same way but falls back differently when one
    isn't given, so it calls :func:`_validate_as_of` directly rather than this function.
    """
    raw = options.get("as_of")
    if raw is None:
        return datetime.now(timezone.utc).date()
    return _validate_as_of(date.fromisoformat(str(raw)))


def _run_id(step_input: StepInput, options: Mapping[str, Any]) -> str:
    """The workflow run's id when the engine gives one, else ``additional_data["run_id"]``, else
    a fresh uuid -- the same precedence R7's provenance uses, so the two join."""
    pinned = options.get("run_id")
    if pinned:
        return str(pinned)
    session = step_input.workflow_session
    runs = getattr(session, "runs", None) if session is not None else None
    if runs:
        run_id = getattr(runs[-1], "run_id", None)
        if run_id:
            return str(run_id)
    return str(uuid.uuid4())


def last_load_verdict(engine: Engine) -> Tuple[Optional[str], Optional[str]]:
    """``(verdict, reason)`` of the most recent finished load, or ``(None, None)`` before any."""
    with engine.begin() as connection:
        row = connection.execute(
            text(
                "SELECT verdict, reason FROM quality.load_run "
                "WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
            )
        ).first()
    return (row[0], row[1]) if row else (None, None)


def _scope_filters(
    reader: SemanticRead, kpis: Sequence[KpiDefinition]
) -> Dict[str, str]:
    """Concept name to accepted ``sql_filter``, for every concept the KPIs name. One read per
    concept, not per KPI."""
    filters: Dict[str, str] = {}
    for concept_name in sorted({kpi.concept for kpi in kpis if kpi.concept}):
        concept = reader.get_concept(concept_name)
        if concept is not None and concept.sql_filter.strip():
            filters[concept_name] = concept.sql_filter.strip()
    return filters


def _refusal(reason: str, **content: Any) -> StepOutput:
    return StepOutput(
        content={"refused": reason, **content}, success=False, error=reason, stop=True
    )


def _fetch_gold_rows(
    engine: Engine, statement: str, params: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """The blocking half of one KPI's read. :func:`read_gold` offloads this to a thread via
    ``asyncio.to_thread`` -- an ``async def`` executor is ``await``ed directly by the workflow
    engine with no automatic offload (``workflow/step.py``'s ``_is_async_callable`` branch), so
    a plain synchronous call here would block the event loop for the round-trip's duration on
    every interactive read."""
    with engine.begin() as connection:
        return [
            dict(row)
            for row in connection.execute(text(statement), dict(params)).mappings()
        ]


def _serialise_gold_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """One view row as ``read_gold`` hands it out: dates and timestamps as ISO strings, and
    ``value`` as ``str``, not ``float``. The DDL chose ``numeric`` so a money or ratio value
    is exact; float on the way out reintroduces the binary rounding that choice exists to
    avoid, in the value a customer reads."""
    return {
        k: (
            v.isoformat()
            if hasattr(v, "isoformat")
            else (str(v) if k == "value" and v is not None else v)
        )
        for k, v in row.items()
    }


def _history_days(options: Mapping[str, Any]) -> int:
    """``0`` (no history read) for anything malformed, logged rather than raised -- every
    other bad-input path in :func:`read_gold` is reported this way (a `missing` entry, or an
    empty, logged `history`), not by letting the whole step raise. `read_gold` is the seam an
    interactive playground reads through, so a client-supplied `additional_data` should not
    be able to fail the entire read over one bad option."""
    raw = options.get("history_days")
    if raw is None:
        return 0
    try:
        days = int(raw)
    except (TypeError, ValueError):
        log.warning("read_gold: ignoring non-numeric history_days=%r", raw)
        return 0
    if days < 0:
        log.warning("read_gold: ignoring negative history_days=%r", raw)
        return 0
    return days


def _history_for(options: Mapping[str, Any]) -> Optional[FrozenSet[str]]:
    """The identifiers that asked for ``history`` this call, or ``None`` when every
    identifier in the request should get it -- the call-wide behaviour every caller before
    dashboards relied on (``history_days`` with no ``history_for`` still applies to every
    ``kpi_identifiers`` entry, unchanged).

    A bare string is one identifier, not an iterable of characters -- without this, a caller
    passing ``history_for="k1"`` (this option's own docstring calls it "a set/list of
    identifiers", but nothing stops a single-identifier caller from writing it as a plain
    string) would silently narrow to a frozenset of single letters that no real identifier
    ever matches, so history would come back empty for everyone with no error at all."""
    raw = options.get("history_for")
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = (raw,)
    return frozenset(str(identifier) for identifier in raw)


async def materialise_gold(step_input: StepInput) -> StepOutput:
    """Materialise every ready, scalar KPI in the registry for tonight's ``as_of``.

    Refuses (``stop=True``) when the last finished load was not ``passed`` -- reachable only
    from a hand run, since in the nightly chain a blocked verdict stops the run before this
    step -- and when a landing-to-silver load holds the advisory lock, so gold never reads a
    silver mid-conformance. A KPI's own failure is never a refusal: it is a ``failed`` row in
    ``gold.kpi_run`` and an entry in this step's content, next to the ones that materialised.

    ``additional_data``: ``as_of`` (ISO date; default today UTC), ``kpi_identifiers`` (a
    subset; default the whole registry), ``business_area`` (a registry filter), ``run_id``.
    """
    options = _options(step_input)
    settings = RetailSettings()
    as_of = _as_of(options)
    run_id = _run_id(step_input, options)

    # `additional_data` is a workflow's client-supplied bag. Two of its keys turn off a
    # safety property -- the quality gate and the statement timeout -- so they are honoured
    # only when the deployment has opted in. Without this, whoever can start a workflow can
    # write gold from unverified silver and run an unbounded statement against the warehouse.
    if not settings.retail_allow_unsafe_gold_options:
        for unsafe in ("allow_unverified_silver", "statement_timeout_ms"):
            if unsafe in options:
                log.warning(
                    "materialise_gold: ignoring %s from additional_data; set "
                    "EGPT_RETAIL_ALLOW_UNSAFE_GOLD_OPTIONS to permit it",
                    unsafe,
                )
        options = {
            key: value
            for key, value in options.items()
            if key not in {"allow_unverified_silver", "statement_timeout_ms"}
        }

    engine = build_warehouse_engine(settings)
    try:
        verdict, reason = last_load_verdict(engine)
        if (
            verdict is not None
            and verdict != "passed"
            and not options.get("allow_unverified_silver")
        ):
            return _refusal(
                f"the last finished load was {verdict!r}, not 'passed' ({reason}); gold is not "
                "rebuilt from silver the gates refused. Fix the load, or pass "
                "allow_unverified_silver=true to override deliberately.",
                as_of=as_of.isoformat(),
                run_id=run_id,
            )
        lock = LoadLock(psycopg_dsn(str(settings.retail_warehouse_db_url)))
        if not lock.acquire():
            return _refusal(
                "a landing-to-silver load holds the advisory lock; gold would read silver "
                "mid-conformance. Wait for it to finish.",
                as_of=as_of.isoformat(),
                run_id=run_id,
            )
        try:
            import psycopg

            connection = psycopg.connect(psycopg_dsn(settings.retail_db_url))
            try:
                store = KpiRegistryStore(connection)
                kpis = list(store.list(business_area=options.get("business_area")))
            finally:
                connection.close()
            wanted = options.get("kpi_identifiers")
            if wanted:
                keep = {str(identifier) for identifier in wanted}
                kpis = [kpi for kpi in kpis if kpi.identifier in keep]

            with open_semantic_reader(settings) as reader:
                snapshot = reader.snapshot()
                feasibility = assess_registry(kpis, available_tables(reader))
                scope_filters = _scope_filters(reader, kpis)
                guard = build_warehouse_read_guard(
                    reader,
                    kpi_identifiers=[kpi.identifier for kpi in kpis],
                    statement_timeout_ms=int(
                        options.get("statement_timeout_ms", 60_000)
                    ),
                )
                results = await amaterialise_registry(
                    engine,
                    kpis,
                    feasibility=feasibility,
                    scope_filters=scope_filters,
                    as_of=as_of,
                    snapshot_version=snapshot.version,
                    run_id=run_id,
                    guard=guard,
                    statement_timeout_ms=int(
                        options.get("statement_timeout_ms", 60_000)
                    ),
                )
        finally:
            lock.release()
    finally:
        engine.dispose()

    _note_materialisation(step_input, results, as_of)
    by_status = {
        status: [r.kpi_identifier for r in results if r.status == status]
        for status in (MATERIALISED, FAILED, SKIPPED)
    }
    log.info(
        "gold as_of=%s run=%s: %d materialised, %d failed, %d skipped",
        as_of,
        run_id,
        len(by_status[MATERIALISED]),
        len(by_status[FAILED]),
        len(by_status[SKIPPED]),
    )
    return StepOutput(
        content={
            "as_of": as_of.isoformat(),
            "run_id": run_id,
            "snapshot_version": snapshot.version,
            "materialised": by_status[MATERIALISED],
            "failed": by_status[FAILED],
            "skipped": by_status[SKIPPED],
            "results": [result.to_dict() for result in results],
        }
    )


def _note_materialisation(
    step_input: StepInput, results: Sequence[KpiRunResult], as_of: date
) -> None:
    """R7's bag: every source table read, every statement the warehouse ran, every stamp."""
    if step_input.additional_data is None:
        return
    stamp = as_of.isoformat()
    for result in results:
        if result.status == SKIPPED:
            continue
        for table in result.source_tables:
            note_dataset_read(
                step_input, table=table, schema=SILVER_SCHEMA, as_of=stamp
            )
        if result.statement:
            note_sql_execution(
                step_input,
                statement=result.statement,
                row_count=result.rows_written
                if result.status == MATERIALISED
                else None,
                duration_ms=result.duration_ms,
                guard="allowed"
                if result.status == MATERIALISED
                or not (result.error or "").startswith("guard refused")
                else "refused",
            )
        if result.status == MATERIALISED and result.finished_at is not None:
            note_freshness(
                step_input,
                table=gold_view(result.kpi_identifier),
                as_of=stamp,
                computed_at=result.finished_at.isoformat(),
            )


async def read_gold(step_input: StepInput) -> StepOutput:
    """Read materialised values for ``kpi_identifiers`` at ``as_of`` -- the contract's own
    ``read_gold`` step, through the per-KPI views and the guard.

    ``as_of`` defaults, per KPI, to the latest business date that KPI materialised for. A KPI
    with no gold at that date is reported (``missing``), never invented. Reads through the
    read-only role when ``EGPT_RETAIL_WAREHOUSE_READONLY_DB_URL`` is set, otherwise through
    the warehouse engine with a warning -- the guard runs either way.

    ``history_days`` (optional, default 0) additionally reads each KPI's rows for the
    business dates in ``(as_of - history_days, as_of]`` and returns them under the KPI's
    ``history`` key, oldest first, in the same row shape as ``rows`` -- what a trend tile or a
    period-over-period delta is computed from. ``history_for`` (optional) narrows which of
    ``kpi_identifiers`` actually get that second read -- a set/list of identifiers; omitted or
    ``None`` means every requested identifier gets it, the original call-wide behaviour. This
    is how a caller batching several KPIs in one call (e.g. a dashboard mixing a ``table``
    tile with a ``line`` tile) avoids paying for a history read on a KPI nothing renders it
    for. ``gold.kpi_value`` keeps every nightly row (``070_gold.sql``), so this is a second
    read of the same view through the same guard,
    never a second computation. A KPI whose history read fails still returns its current
    ``rows``; ``history`` is then empty and the failure is logged.

    This is the seam an interactive playground reads through, so every warehouse round-trip
    (``alatest_as_of``, then the per-KPI read via :func:`_fetch_gold_rows`) is offloaded with
    ``asyncio.to_thread`` rather than run inline -- an ``async def`` executor is ``await``ed
    directly by the workflow engine with no automatic thread offload.
    """
    options = _options(step_input)
    settings = RetailSettings()
    history_days = _history_days(options)
    history_for = _history_for(options)
    identifiers = [str(i) for i in (options.get("kpi_identifiers") or ())]
    if not identifiers:
        return StepOutput(
            content={
                "kpis": {},
                "missing": [],
                "reason": "no kpi_identifiers requested",
            },
            success=True,
        )

    if settings.retail_warehouse_readonly_db_url:
        engine = build_warehouse_readonly_engine(settings)
    else:
        log.warning(
            "read_gold: EGPT_RETAIL_WAREHOUSE_READONLY_DB_URL unset; reading through the warehouse role"
        )
        engine = build_warehouse_engine(settings)
    try:
        with open_semantic_reader(settings) as reader:
            guard = build_warehouse_read_guard(reader, kpi_identifiers=identifiers)
            values: Dict[str, Dict[str, Any]] = {}
            missing: List[Dict[str, Any]] = []
            for identifier in identifiers:
                as_of = (
                    _validate_as_of(date.fromisoformat(str(options["as_of"])))
                    if options.get("as_of")
                    else await alatest_as_of(engine, identifier)
                )
                if as_of is None:
                    missing.append(
                        {"kpi_identifier": identifier, "reason": "never materialised"}
                    )
                    continue
                statement = read_gold_statement(identifier)
                try:
                    guard.check(statement)
                except SQLGuardRefusal as exc:
                    note_sql_execution(step_input, statement=statement, guard="refused")
                    missing.append(
                        {
                            "kpi_identifier": identifier,
                            "reason": f"guard refused: {exc.message}",
                        }
                    )
                    continue
                started = datetime.now(timezone.utc)
                try:
                    rows = await asyncio.to_thread(
                        _fetch_gold_rows, engine, statement, {"as_of": as_of}
                    )
                except (
                    Exception
                ) as exc:  # the view does not exist yet, or the role cannot see it
                    missing.append(
                        {
                            "kpi_identifier": identifier,
                            "reason": str(getattr(exc, "orig", exc)).splitlines()[0],
                        }
                    )
                    continue
                duration = int(
                    (datetime.now(timezone.utc) - started).total_seconds() * 1000
                )
                note_sql_execution(
                    step_input,
                    statement=statement,
                    row_count=len(rows),
                    duration_ms=duration,
                    guard="allowed",
                )
                note_dataset_read(
                    step_input,
                    table=gold_view(identifier),
                    schema=GOLD_SCHEMA,
                    as_of=as_of.isoformat(),
                )
                if not rows:
                    missing.append(
                        {
                            "kpi_identifier": identifier,
                            "reason": f"no gold row at as_of {as_of.isoformat()}",
                        }
                    )
                    continue
                computed = max(row["computed_at"] for row in rows)
                note_freshness(
                    step_input,
                    table=gold_view(identifier),
                    as_of=as_of.isoformat(),
                    computed_at=computed.isoformat(),
                )
                values[identifier] = {
                    "as_of": as_of.isoformat(),
                    "computed_at": computed.isoformat(),
                    "source_snapshot": rows[0]["source_snapshot"],
                    "run_id": rows[0]["run_id"],
                    "rows": [_serialise_gold_row(row) for row in rows],
                }
                if history_days and (history_for is None or identifier in history_for):
                    values[identifier]["history"] = await _read_history(
                        step_input,
                        engine,
                        guard,
                        identifier,
                        as_of=as_of,
                        history_days=history_days,
                    )
    finally:
        engine.dispose()

    return StepOutput(content={"kpis": values, "missing": missing}, success=True)


async def _read_history(
    step_input: StepInput,
    engine: Engine,
    guard: SQLGuard,
    identifier: str,
    *,
    as_of: date,
    history_days: int,
) -> List[Dict[str, Any]]:
    """One KPI's rows for the ``history_days`` business dates ending at ``as_of``, through the
    same guard the current-date read passed. Empty, with a log line, when the guard refuses or
    the read fails -- the caller already holds the current value and must not lose it.

    A second real read of ``gold.kpi_<identifier>``, so it notes all three of R7's bag the
    current-date read does on success: ``note_sql_execution``, ``note_dataset_read``, and --
    when the rows carry a ``computed_at`` -- ``note_freshness``.

    Runs as its own transaction (``_fetch_gold_rows`` opens a fresh ``engine.begin()``),
    separate from the current-date read that precedes it -- even though this window's upper
    bound (``as_of <= :as_of``) re-reads that same date's row a second time. A same-night
    re-run of ``materialise_gold`` landing between the two reads could in theory make the
    last history point disagree with the just-read current value for the same ``as_of``.
    Low-probability given nightly cadence and not exploitable; combine both reads into one
    connection if this ever needs a stronger guarantee.
    """
    statement = read_gold_history_statement(identifier)
    try:
        guard.check(statement)
    except SQLGuardRefusal as exc:
        note_sql_execution(step_input, statement=statement, guard="refused")
        log.warning("read_gold: history for %s refused: %s", identifier, exc.message)
        return []
    params = {"since": as_of - timedelta(days=history_days), "as_of": as_of}
    started = datetime.now(timezone.utc)
    try:
        rows = await asyncio.to_thread(_fetch_gold_rows, engine, statement, params)
    except Exception as exc:
        log.warning(
            "read_gold: history for %s failed: %s",
            identifier,
            str(getattr(exc, "orig", exc)).splitlines()[0],
        )
        return []
    note_sql_execution(
        step_input,
        statement=statement,
        row_count=len(rows),
        duration_ms=int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
        guard="allowed",
    )
    note_dataset_read(
        step_input,
        table=gold_view(identifier),
        schema=GOLD_SCHEMA,
        as_of=as_of.isoformat(),
    )
    if rows:
        computed = max(row["computed_at"] for row in rows)
        note_freshness(
            step_input,
            table=gold_view(identifier),
            as_of=as_of.isoformat(),
            computed_at=computed.isoformat(),
        )
    return [_serialise_gold_row(row) for row in rows]


#: Registered in ``app/components.py``; ``materialise_gold`` is the last step of nightly-refresh.
GOLD_EXECUTORS = (materialise_gold, read_gold)
