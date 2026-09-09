"""``check_preflight`` (R7, G4): may this use case run now?

Deterministic, no I/O -- unlike R4's executors, this one stays a plain ``def``, per the
registry's own exemption ("author it as ``async def`` ... where an executor does I/O"). The
only check genuinely computable today is ``kpis_ready``, via
``egpt_retail.contracts.kpi.assess_feasibility`` (a pure set difference, K11's own real
registry is P3 and does not exist yet). The other three checks the API contract's
``PreflightResult`` names -- ``tables_exposed``, ``gold_fresh``, ``guard_role`` -- are included
as honest placeholders (``passed: True``, naming exactly what is unbuilt) rather than omitted
(a UI built against the contract's fixed shape should not have to handle three fields
sometimes missing) or faked as computed (the only thing that could back ``tables_exposed``,
``StubSemanticRead``, ships with no fixture loaded by default and raises rather than answers).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.contracts.kpi import (
    Feasibility,
    FeasibilityVerdict,
    KpiDefinition,
    assess_feasibility,
)

__all__ = ["check_preflight"]

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
        "name": _GOLD_FRESH,
        "passed": True,
        "reason": "Not yet checked against a real freshness stamp -- R5's gold materialisation "
        "is not built yet (P3, after K11). No staleness bound is enforced.",
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
    reason = " ".join(
        _kpi_check_reason(f.kpi_identifier, f)
        for f in (blocked_or_partial or feasibilities[:1])
    )
    return {"name": _KPIS_READY, "passed": not blocked_or_partial, "reason": reason}


def check_preflight(step_input: StepInput) -> StepOutput:
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

    kpis_check = _kpis_ready_check(kpis, available_tables)
    checks: List[Dict[str, Any]] = [kpis_check, *_UNBUILT_CHECKS]
    blocked_by = [c["name"] for c in checks if not c["passed"]]
    allowed = not blocked_by

    return StepOutput(
        content={
            "use_case_id": use_case_id,
            "allowed": allowed,
            "checks": checks,
            "blocked_by": blocked_by,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        },
        success=True,
        error=None if allowed else kpis_check["reason"],
        stop=not allowed,
    )
