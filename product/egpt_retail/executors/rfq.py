"""``sample_rows`` (R10, D12): the bounded detail ``funnel_explanation`` cites.

``read_gold`` (R5) already answers the top-line numbers -- one value per brand, for whatever
KPIs the spec named. What it does not answer is *why* they moved: which brands changed most
since the prior period, and whether on-site friction attaches to the funnel stage where it
happened. This executor computes both, through R6's guard like every other retail read, and
hands the agent step rows to quote rather than a number to reason about from nothing.

**The friction join is the package's known hard case, and it is handled by reporting rather
than guessing.** ``silver.analytics_event.session_id`` and
``silver.session_replay_session.session_id`` are two vendors' own ids in two separate spaces
-- the column comment on ``analytics_event`` says so verbatim, and says this executor is the
one that needs the bridge. The only column both tables carry is ``identity_key``, the shared
identity hash reserved by the landing contract and gated on privacy sign-off (build plan,
"Blocked, and not by engineering"). Until that hash is populated, every row on both sides has
it ``NULL`` and the join returns nothing -- correctly: an empty result is the honest answer to
"do these joinable-in-principle tables share any resolved identities today", not a defect in
this executor. ``friction.available`` says which of the two is true, so
``funnel_explanation`` can say "friction data is not attributable yet" instead of silently
producing a funnel with a gap-shaped hole in it.

Registered as the executor registry's exemption: ``async def`` under one name, since every
branch does I/O.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence

from egpt.exceptions import SQLGuardRefusal
from egpt.guardrails.sql_guard import SQLGuard
from egpt.workflow.types import StepInput, StepOutput
from sqlalchemy import text
from sqlalchemy.engine import Engine

from egpt_retail.app.db import build_warehouse_engine, build_warehouse_readonly_engine
from egpt_retail.app.semantic import open_semantic_reader
from egpt_retail.app.settings import RetailSettings
from egpt_retail.app.sql_guard import build_warehouse_read_guard
from egpt_retail.executors.provenance import (
    note_dataset_read,
    note_row_sample,
    note_sql_execution,
)
from egpt_retail.warehouse.schema import SILVER_SCHEMA, gold_view

__all__ = [
    "DIRECTION_DOWN",
    "DIRECTION_FLAT",
    "DIRECTION_NEW",
    "DIRECTION_UNKNOWN",
    "DIRECTION_UP",
    "FRICTION_QUERY",
    "friction_unavailable",
    "prior_period_statement",
    "rank_movers",
    "sample_rows",
]

log = logging.getLogger(__name__)

# The five outcomes `rank_movers` reports. A closed set, not free text -- the alignment rule
# ("agent output is a typed schema, values from closed sets") starts here: `funnel_explanation`
# receives these strings and is never asked to characterise a delta in its own words.
DIRECTION_UP = "up"
DIRECTION_DOWN = "down"
DIRECTION_FLAT = "flat"  # both values known and equal -- a real comparison, not a gap
DIRECTION_NEW = "new"  # a brand present now but absent from the prior period
DIRECTION_UNKNOWN = (
    "unknown"  # a value that could not be compared (either side missing/None)
)

#: Every friction column `silver.session_replay_session` carries, joined by the one column it
#: shares with `silver.analytics_event` -- see the module docstring. Both tables are already
#: exposed by the guard once harvested (they are silver tables, not KPI views), so no
#: `kpi_identifiers` widening is needed for this statement specifically.
#:
#: `session_replay_session` is aggregated to one row per `identity_key` *before* the join:
#: a returning visitor's `identity_key` can legitimately span multiple sessions, so joining
#: the raw table directly against `analytics_event` (whose own primary key admits many rows
#: per `identity_key`) would re-sum the same session's counts once per matching event row.
FRICTION_QUERY = (
    "WITH sessions_by_identity AS ("
    "SELECT identity_key, "
    "sum(rage_click_count) AS rage_clicks, "
    "sum(dead_click_count) AS dead_clicks, "
    "sum(script_error_count) AS script_errors "
    f"FROM {SILVER_SCHEMA}.session_replay_session "
    "WHERE identity_key IS NOT NULL "
    "GROUP BY identity_key"
    ") "
    "SELECT e.event_name AS event_name, "
    "sum(s.rage_clicks) AS rage_clicks, "
    "sum(s.dead_clicks) AS dead_clicks, "
    "sum(s.script_errors) AS script_errors, "
    "count(*) AS sessions "
    f"FROM {SILVER_SCHEMA}.analytics_event e "
    "JOIN sessions_by_identity s ON s.identity_key = e.identity_key "
    "WHERE e.identity_key IS NOT NULL "
    "GROUP BY e.event_name"
)


def friction_unavailable(reason: str) -> Dict[str, Any]:
    """The honest shape when no row joined. Exported so the agent's cookbook and its schema's
    default both build the same "nothing to report" value from one place."""
    return {"available": False, "reason": reason, "rows": []}


def prior_period_statement(kpi_identifier: str) -> str:
    """One row per brand, the most recent value strictly before ``:as_of`` -- through the same
    per-KPI view ``read_gold`` reads, so a brand this returns is a brand ``read_gold`` could
    also have returned, and the guard's allow-list needs nothing this executor did not already
    ask for."""
    view = gold_view(kpi_identifier)
    return (
        f"SELECT DISTINCT ON (brand) brand, value, as_of "
        f"FROM gold.{view} WHERE as_of < :as_of ORDER BY brand, as_of DESC"
    )


def rank_movers(
    current_rows: Sequence[Mapping[str, Any]], prior_rows: Sequence[Mapping[str, Any]]
) -> List[Dict[str, Any]]:
    """Pair current and prior rows by ``brand`` and rank by absolute delta, largest first.

    Pure: takes two row sequences, returns data, has no model and no client to be persuaded
    by -- the same guarantee K11's ``assess_registry`` gives feasibility, applied here to
    "which brands moved most" so `funnel_explanation` is handed a ranking rather than asked
    to eyeball one out of an unordered table.

    A brand in ``current_rows`` absent from ``prior_rows`` is ``new`` rather than dropped: the
    build plan's evidence line is "none appears that is absent from the provenance record",
    and silently excluding a brand because it has no history is its own kind of invented gap.
    """
    prior_by_brand = {
        str(row["brand"]): row for row in prior_rows if row.get("brand") is not None
    }
    movers: List[Dict[str, Any]] = []
    for row in current_rows:
        brand = row.get("brand")
        if brand is None:
            continue
        brand = str(brand)
        current_value = row.get("value")
        prior_row = prior_by_brand.get(brand)
        prior_value = prior_row["value"] if prior_row is not None else None
        delta: Optional[Decimal] = None
        direction = DIRECTION_UNKNOWN
        if prior_row is None:
            direction = DIRECTION_NEW
        elif current_value is not None and prior_value is not None:
            # `.normalize()` strips the trailing zeros a wide-scale `numeric` column leaves
            # behind (and turns Postgres's own zero, `Decimal("0E-20")`, into a plain "0")
            # without touching the value -- `Decimal` alone, unlike `float`, keeps this exact.
            delta = (
                Decimal(str(current_value)) - Decimal(str(prior_value))
            ).normalize()
            direction = (
                DIRECTION_UP
                if delta > 0
                else DIRECTION_DOWN
                if delta < 0
                else DIRECTION_FLAT
            )
        movers.append(
            {
                "brand": brand,
                "current_value": None if current_value is None else str(current_value),
                "prior_value": None if prior_value is None else str(prior_value),
                "delta": None if delta is None else str(delta),
                "direction": direction,
            }
        )
    movers.sort(
        key=lambda m: (
            abs(Decimal(m["delta"])) if m["delta"] is not None else Decimal(-1)
        ),
        reverse=True,
    )
    return movers


def _run_guarded(
    engine: Engine,
    guard: SQLGuard,
    step_input: StepInput,
    statement: str,
    **params: Any,
) -> List[Dict[str, Any]]:
    """One guarded read, noting the statement and dataset in R7's bag either way. Raises
    ``SQLGuardRefusal`` for the caller to turn into a ``stop=True`` step, per the API
    contract's ``permits_or_blocks: true`` on this step."""
    guard.check(statement)
    with engine.begin() as connection:
        rows = [
            dict(row) for row in connection.execute(text(statement), params).mappings()
        ]
    note_sql_execution(
        step_input, statement=statement, row_count=len(rows), guard="allowed"
    )
    return rows


async def sample_rows(step_input: StepInput) -> StepOutput:
    """Pull the movers and the friction sample ``funnel_explanation`` needs, through the guard.

    Reads ``step_input.previous_step_content`` -- ``read_gold``'s output, per the compiled
    plan's fixed ordering -- for the current period's KPI rows and their ``as_of``.
    ``stop=True`` only on a guard refusal; a friction join returning nothing is data
    (``friction["available"] = False``), never a refusal.
    """
    options: Dict[str, Any] = step_input.additional_data or {}
    previous = step_input.previous_step_content
    kpis: Mapping[str, Any] = (
        previous.get("kpis") if isinstance(previous, Mapping) else None
    ) or {}
    if not kpis:
        return StepOutput(
            content={
                "movers": {},
                "friction": friction_unavailable(
                    "no KPI rows from read_gold to compare"
                ),
                "samples": [],
                "kpi_identifiers": [],
                "snapshot_version": "",
            }
        )

    settings = RetailSettings()
    if settings.retail_warehouse_readonly_db_url:
        engine = build_warehouse_readonly_engine(settings)
    else:
        log.warning(
            "sample_rows: EGPT_RETAIL_WAREHOUSE_READONLY_DB_URL unset; reading through the warehouse role"
        )
        engine = build_warehouse_engine(settings)
    sample_size = int(options.get("mover_sample_size", 5))
    if sample_size < 0:
        raise ValueError(f"mover_sample_size must be >= 0, got {sample_size}")
    try:
        with open_semantic_reader(settings) as reader:
            guard = build_warehouse_read_guard(reader, kpi_identifiers=list(kpis))
            movers: Dict[str, List[Dict[str, Any]]] = {}
            samples: List[Dict[str, Any]] = []
            for identifier, current in kpis.items():
                current_rows = current.get("rows") or []
                as_of = date.fromisoformat(str(current.get("as_of")))
                try:
                    prior_rows = await asyncio.to_thread(
                        _run_guarded,
                        engine,
                        guard,
                        step_input,
                        prior_period_statement(identifier),
                        as_of=as_of,
                    )
                except SQLGuardRefusal as exc:
                    return StepOutput(
                        content={"refused": f"{identifier}: {exc.message}"},
                        success=False,
                        error=exc.message,
                        stop=True,
                    )
                ranked = rank_movers(current_rows, prior_rows)
                top = ranked[:sample_size]
                movers[identifier] = top
                samples.append({"kpi_identifier": identifier, "rows": top})
                if top:
                    note_row_sample(step_input, table=gold_view(identifier), rows=top)
                note_dataset_read(
                    step_input,
                    table=gold_view(identifier),
                    schema="gold",
                    as_of=as_of.isoformat(),
                )

            try:
                friction_rows = await asyncio.to_thread(
                    _run_guarded, engine, guard, step_input, FRICTION_QUERY
                )
            except SQLGuardRefusal as exc:
                return StepOutput(
                    content={"refused": f"friction: {exc.message}"},
                    success=False,
                    error=exc.message,
                    stop=True,
                )
            note_dataset_read(step_input, table="analytics_event", schema=SILVER_SCHEMA)
            note_dataset_read(
                step_input, table="session_replay_session", schema=SILVER_SCHEMA
            )
            if friction_rows:
                friction: Dict[str, Any] = {
                    "available": True,
                    "reason": "",
                    "rows": friction_rows,
                }
                note_row_sample(
                    step_input, table="session_replay_session", rows=friction_rows
                )
            else:
                friction = friction_unavailable(
                    "no row on either side carries a populated identity_key yet -- "
                    "analytics_event and session_replay_session use separate id spaces "
                    "(silver.analytics_event.session_id is not joinable to "
                    "silver.session_replay_session.session_id) and the shared identity hash "
                    "is gated on privacy sign-off"
                )
    finally:
        engine.dispose()

    kpi_identifiers = sorted(kpis)
    snapshot_version = str(
        kpis[kpi_identifiers[0]].get("source_snapshot", "") if kpi_identifiers else ""
    )
    return StepOutput(
        content={
            "movers": movers,
            "friction": friction,
            "samples": samples,
            "kpi_identifiers": kpi_identifiers,
            "snapshot_version": snapshot_version,
        }
    )
