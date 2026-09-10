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
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from egpt.exceptions import SQLGuardRefusal
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
    read_gold_statement,
)
from egpt_retail.warehouse.loads import LoadLock
from egpt_retail.warehouse.schema import GOLD_SCHEMA, SILVER_SCHEMA, gold_view

__all__ = ["GOLD_EXECUTORS", "materialise_gold", "read_gold", "last_load_verdict"]

log = logging.getLogger(__name__)


def _options(step_input: StepInput) -> Mapping[str, Any]:
    return step_input.additional_data or {}


def _as_of(options: Mapping[str, Any]) -> date:
    raw = options.get("as_of")
    if raw is None:
        return datetime.now(timezone.utc).date()
    return date.fromisoformat(str(raw))


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
    engine: Engine, statement: str, as_of: date
) -> List[Dict[str, Any]]:
    """The blocking half of one KPI's read. :func:`read_gold` offloads this to a thread via
    ``asyncio.to_thread`` -- an ``async def`` executor is ``await``ed directly by the workflow
    engine with no automatic offload (``workflow/step.py``'s ``_is_async_callable`` branch), so
    a plain synchronous call here would block the event loop for the round-trip's duration on
    every interactive read."""
    with engine.begin() as connection:
        return [
            dict(row)
            for row in connection.execute(text(statement), {"as_of": as_of}).mappings()
        ]


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

    This is the seam an interactive playground reads through, so every warehouse round-trip
    (``alatest_as_of``, then the per-KPI read via :func:`_fetch_gold_rows`) is offloaded with
    ``asyncio.to_thread`` rather than run inline -- an ``async def`` executor is ``await``ed
    directly by the workflow engine with no automatic thread offload.
    """
    options = _options(step_input)
    settings = RetailSettings()
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
                    date.fromisoformat(str(options["as_of"]))
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
                        _fetch_gold_rows, engine, statement, as_of
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
                    "rows": [
                        {
                            k: (
                                v.isoformat()
                                if hasattr(v, "isoformat")
                                # `str`, not `float`. The DDL chose `numeric` so a money
                                # or ratio value is exact; float on the way out
                                # reintroduces the binary rounding that choice
                                # exists to avoid, in the value a customer reads.
                                else (str(v) if k == "value" and v is not None else v)
                            )
                            for k, v in row.items()
                        }
                        for row in rows
                    ],
                }
    finally:
        engine.dispose()

    return StepOutput(content={"kpis": values, "missing": missing}, success=True)


#: Registered in ``app/components.py``; ``materialise_gold`` is the last step of nightly-refresh.
GOLD_EXECUTORS = (materialise_gold, read_gold)
