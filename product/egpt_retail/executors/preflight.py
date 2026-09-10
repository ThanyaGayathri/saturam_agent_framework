"""``check_preflight`` (R7, G4): may this use case run now?

Two of the four checks the API contract's ``PreflightResult`` names are real:

* ``kpis_ready`` -- ``egpt_retail.contracts.kpi.assess_feasibility``, a pure set difference
  over the KPIs and tables handed in through ``additional_data``.
* ``gold_fresh`` (R5) -- the referenced KPIs' latest ``gold.kpi_run`` rows, read from the
  warehouse when ``EGPT_RETAIL_WAREHOUSE_DB_URL`` is configured: a KPI whose last run failed,
  never ran, or materialised longer ago than the staleness bound fails the check, naming
  which. With no warehouse configured the check stays the honest placeholder it was.

``async def`` since R5, because ``gold_fresh`` does I/O -- the registry convention's
exemption. ``tables_exposed`` and ``guard_role`` remain placeholders (``passed: True``, naming
exactly what is unbuilt) rather than omitted -- a UI built against the contract's fixed shape
should not have to handle fields sometimes missing -- or faked as computed.

Deliberately *not* here: the compile probe R5's materialiser runs. Preflight answers per
interactive request and must stay cheap; a formula that does not compile against silver shows
up one night later as a ``failed`` run, which ``gold_fresh`` then reports. Same signal, one
place that pays for it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import (
    Feasibility,
    FeasibilityVerdict,
    KpiDefinition,
    assess_feasibility,
)
from egpt_retail.warehouse.health import FRESHNESS_BOUND_HOURS

__all__ = ["check_preflight", "gold_fresh_check", "GOLD_STALENESS_BOUND_HOURS"]

log = logging.getLogger(__name__)

#: Past this many hours since a KPI last materialised, ``gold_fresh`` fails for it. The same
#: name ``warehouse/health.py`` draws its own ``fresh``/``stale`` line at -- imported rather
#: than restated, so the two cannot drift into two different bounds behind one claimed
#: meaning; overridable per run through ``additional_data["gold_staleness_hours"]``.
GOLD_STALENESS_BOUND_HOURS = FRESHNESS_BOUND_HOURS

#: The check names the API contract's own worked examples use
#: (``product/egpt_retail/api/openapi/paths/use-cases.yaml``, ``runUseCasePreflight``).
_KPIS_READY = "kpis_ready"
_TABLES_EXPOSED = "tables_exposed"
_GOLD_FRESH = "gold_fresh"
_GUARD_ROLE = "guard_role"

#: Placeholder checks that always pass today, naming exactly what would make them real.
_UNBUILT_CHECKS: Sequence[Dict[str, Any]] = (
    {
        "name": _TABLES_EXPOSED,
        "passed": True,
        "reason": "Not yet checked against a live semantic snapshot -- K5's read API is not "
        "built yet (P2, after K4). Every table a KPI's SQL reads is assumed exposed.",
    },
    {
        "name": _GUARD_ROLE,
        "passed": True,
        "reason": "Not yet verified live -- this deployment's read path is assumed to be "
        "wired through R6's SQL guard and the egpt_reader role.",
    },
)


def _kpi_check_reason(identifier: str, feasibility: Feasibility) -> str:
    """The same text for a single KPI's reason and (joined) the aggregate check's reason --
    one function, reused everywhere, matching R4's decide_load_verdict discipline. Reproduces
    the API contract's own worked example verbatim: "product__avg_rating: partial -- missing
    from the semantic snapshot: web_sessions."
    """
    if feasibility.verdict is FeasibilityVerdict.READY:
        return f"{identifier}: ready."
    missing = ", ".join(sorted(feasibility.missing_tables))
    return f"{identifier}: {feasibility.verdict.value} -- missing from the semantic snapshot: {missing}."


def _kpis_ready_check(
    kpis: Sequence[KpiDefinition], available_tables: Iterable[str]
) -> Dict[str, Any]:
    available = frozenset(t.lower() for t in available_tables)
    if not kpis:
        return {"name": _KPIS_READY, "passed": True, "reason": "No KPIs referenced."}
    feasibilities = [assess_feasibility(kpi, available) for kpi in kpis]
    blocked_or_partial = [
        f for f in feasibilities if f.verdict is not FeasibilityVerdict.READY
    ]
    # Every KPI's reason, not just the first -- a use case referencing several KPIs must not
    # have its later ones silently dropped from the aggregate reason, ready or not.
    reason = " ".join(
        _kpi_check_reason(f.kpi_identifier, f)
        for f in (blocked_or_partial or feasibilities)
    )
    return {"name": _KPIS_READY, "passed": not blocked_or_partial, "reason": reason}


_GOLD_FRESH_UNCONFIGURED = {
    "name": _GOLD_FRESH,
    "passed": True,
    "reason": "Not checked: EGPT_RETAIL_WAREHOUSE_DB_URL is not set on this process, so there "
    "is no gold to read a freshness stamp from. No staleness bound is enforced.",
}


def gold_fresh_check(
    kpis: Sequence[KpiDefinition],
    health: Mapping[str, Mapping[str, Any]],
    *,
    bound_hours: float = GOLD_STALENESS_BOUND_HOURS,
) -> Dict[str, Any]:
    """The ``gold_fresh`` check over already-read health rows (``warehouse/health.py``). Pure.

    Fails for a referenced KPI that never computed, whose last run failed or was skipped, or
    whose last value is older than ``bound_hours`` -- each named with why, joined the way
    ``kpis_ready`` joins its reasons. A use case referencing no KPI passes trivially.
    """
    if not kpis:
        return {"name": _GOLD_FRESH, "passed": True, "reason": "No KPIs referenced."}
    problems: List[str] = []
    fresh: List[str] = []
    for kpi in kpis:
        row: Mapping[str, Any] = health.get(kpi.identifier) or {}
        status = row.get("status") or "never_computed"
        if status == "fresh":
            fresh.append(f"{kpi.identifier}: fresh ({row['staleness_hours']} h old).")
        elif status == "stale":
            problems.append(
                f"{kpi.identifier}: stale -- last computed {row['staleness_hours']} h ago, "
                f"bound is {bound_hours:g} h."
            )
        elif status == "failed":
            problems.append(
                f"{kpi.identifier}: last run failed -- {row.get('last_error')}"
            )
        elif status == "not_scheduled":
            problems.append(
                f"{kpi.identifier}: not materialised -- {row.get('last_error')}"
            )
        else:
            problems.append(f"{kpi.identifier}: never computed; no gold row exists.")
    return {
        "name": _GOLD_FRESH,
        "passed": not problems,
        "reason": " ".join(problems or fresh),
    }


def _read_gold_health(
    kpis: Sequence[KpiDefinition], settings: RetailSettings, bound_hours: float
) -> Mapping[str, Mapping[str, Any]]:
    from egpt_retail.app.db import build_warehouse_engine
    from egpt_retail.warehouse.health import kpi_health

    engine = build_warehouse_engine(settings)
    try:
        return {
            row["identifier"]: row
            for row in kpi_health(kpis, engine, bound_hours=bound_hours)
        }
    finally:
        engine.dispose()


async def check_preflight(step_input: StepInput) -> StepOutput:
    """May this use case run now? ``stop=True`` on a block -- the same mechanism R4's
    ``decide_load_verdict`` uses to halt a chain -- so a compiled plan's later steps
    (``read_gold``, ``sample_rows``, the explanation) never run against a KPI that cannot be
    computed, matching this step's ``permits_or_blocks: true`` in the API contract's example
    plan.
    """
    options: Dict[str, Any] = step_input.additional_data or {}
    use_case_id: Optional[str] = options.get("use_case_id")
    kpis: Sequence[KpiDefinition] = options.get("kpis") or ()
    available_tables: Iterable[str] = options.get("available_tables") or ()
    bound_hours = float(options.get("gold_staleness_hours", GOLD_STALENESS_BOUND_HOURS))

    kpis_check = _kpis_ready_check(kpis, available_tables)

    settings = RetailSettings()
    if settings.retail_warehouse_db_url and kpis:
        try:
            health = await asyncio.to_thread(
                _read_gold_health, kpis, settings, bound_hours
            )
            gold_check = gold_fresh_check(kpis, health, bound_hours=bound_hours)
        except (
            Exception
        ) as exc:  # the warehouse is configured but not reachable: that is a block
            # The exception text, not the exception. A driver's message carries the DSN,
            # the host and sometimes the role -- this dict is rendered by the preflight
            # screen, so a connection failure would put the warehouse's address in front of
            # whoever opened the page. The type is enough for a reader to act on; the detail
            # goes to the log, which is where an operator looks.
            log.warning("preflight: gold health unreadable", exc_info=exc)
            gold_check = {
                "name": _GOLD_FRESH,
                "passed": False,
                "reason": (
                    f"gold could not be read ({type(exc).__name__}); see the server log for "
                    "detail"
                ),
            }
    elif settings.retail_warehouse_db_url:
        gold_check = {
            "name": _GOLD_FRESH,
            "passed": True,
            "reason": "No KPIs referenced.",
        }
    else:
        gold_check = dict(_GOLD_FRESH_UNCONFIGURED)

    # The contract's order (`runUseCasePreflight` example): kpis_ready, tables_exposed,
    # gold_fresh, guard_role. A UI keyed on position would notice a swap.
    tables_check, guard_check = _UNBUILT_CHECKS
    checks: List[Dict[str, Any]] = [kpis_check, tables_check, gold_check, guard_check]
    blocked_by = [c["name"] for c in checks if not c["passed"]]
    allowed = not blocked_by
    error = (
        None if allowed else " ".join(c["reason"] for c in checks if not c["passed"])
    )

    return StepOutput(
        content={
            "use_case_id": use_case_id,
            "allowed": allowed,
            "checks": checks,
            "blocked_by": blocked_by,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        },
        success=True,
        error=error,
        stop=not allowed,
    )
