"""``check_spec_gaps`` (R9): what the spec needs that the connected data lacks, deterministically.

"A deterministic gap check deciding what is still missing for the chosen autonomy level"
(delivery plan, R9). Composed from what already exists and consults no model: KPI feasibility
is K11's set difference; a concept is known or it is not (``get_concept``); an area is one the
semantic layer supports or it is not (``list_business_areas``); two source tables join or they
do not (``find_join_path``); and the autonomy shape's parameters (build plan, section 07) are
filled by the draft or they are not. The output is the API contract's ``GapCheck``, with
``feasible`` meaning every gap list is empty.

``assess_gaps`` is the pure core -- data in, a ``GapCheck`` dict out -- so every classification
is testable over the contract fixture without a service. ``check_spec_gaps`` resolves the
registry and the semantic layer from settings and calls it.
"""

from __future__ import annotations

import asyncio
import itertools
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.contracts.kpi import (
    FeasibilityVerdict,
    KpiDefinition,
    assess_feasibility,
)
from egpt_retail.contracts.semantic import SemanticRead, available_tables

__all__ = [
    "AutonomyShape",
    "SHAPE_PARAMETERS",
    "assess_gaps",
    "check_spec_gaps",
    "shape_for",
]


class AutonomyShape(str, Enum):
    """The build plan's four shapes minus watch-and-notify, which is P4's alert machinery."""

    INFORM = "inform"
    RECOMMEND = "recommend"
    ACT = "act"


#: The parameter slots per shape, from build-plan.md section 07's table, verbatim in order.
SHAPE_PARAMETERS: Mapping[AutonomyShape, Tuple[str, ...]] = {
    AutonomyShape.INFORM: (
        "metrics",
        "dimensions",
        "comparison",
        "schedule",
        "recipient",
    ),
    AutonomyShape.RECOMMEND: (
        "candidates",
        "metrics",
        "guardrails",
        "approver",
        "write target",
        "timeout behaviour",
        "schedule",
    ),
    AutonomyShape.ACT: (
        "candidates",
        "metrics",
        "guardrails",
        "write target",
        "per-run cap",
        "rollback",
        "schedule",
    ),
}

#: Which builder field fills each slot, or that none does yet. The Recommend and Act slots
#: with no field are P4's (R10 onward); naming them as gaps now is what stops a Recommend
#: draft passing a check that only knew about Inform.
_SLOT_SOURCES: Mapping[str, str] = {
    "metrics": "spec.kpi_identifiers",
    "dimensions": "spec.grain",
    "comparison": "spec.time_window",
    "schedule": "draft.step8.schedule",
    "recipient": "draft.step8.recipient",
    "guardrails": "draft.step8.guardrail_amount",
    "candidates": "no builder field yet (P4)",
    "approver": "no builder field yet (P4)",
    "write target": "no builder field yet (P4)",
    "timeout behaviour": "no builder field yet (P4)",
    "per-run cap": "no builder field yet (P4)",
    "rollback": "no builder field yet (P4)",
}


def shape_for(autonomy: Optional[str], level: Optional[int]) -> AutonomyShape:
    """The contract's ``autonomy`` (copilot/autopilot) and ``level`` (1-5) as a shape.

    Levels one and two never write, whatever the autonomy says; from three up, copilot is
    Recommend (a person approves) and autopilot is Act. R9's reading of section 07; the
    activation screen (P5) is where it becomes visible.
    """
    if level is None or level <= 2:
        return AutonomyShape.INFORM
    return (
        AutonomyShape.ACT
        if (autonomy or "").lower() == "autopilot"
        else AutonomyShape.RECOMMEND
    )


def _slot_filled(slot: str, spec: Mapping[str, Any], draft: Mapping[str, Any]) -> bool:
    step8 = draft.get("step8") or {}
    if slot == "metrics":
        return bool(spec.get("kpi_identifiers"))
    if slot == "dimensions":
        return bool(spec.get("grain"))
    if slot == "comparison":
        return spec.get("time_window") is not None
    if slot == "schedule":
        return bool(step8.get("schedule"))
    if slot == "recipient":
        return bool(step8.get("recipient"))
    if slot == "guardrails":
        return step8.get("guardrail_amount") is not None
    return False


def _gap(kind: str, subject: str, detail: str) -> Dict[str, str]:
    return {"kind": kind, "subject": subject, "detail": detail}


def assess_gaps(
    spec: Mapping[str, Any],
    *,
    reader: SemanticRead,
    kpis: Mapping[str, KpiDefinition],
    draft: Optional[Mapping[str, Any]] = None,
    autonomy: Optional[str] = None,
    level: Optional[int] = None,
    now: Optional[datetime] = None,
    grains: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """The contract's ``GapCheck`` for one spec. Pure given its inputs; consults no model.

    ``kpis`` is the registry keyed by identifier. ``grains``, when given, is this deployment's
    grain vocabulary (``VocabularySet["grain"].values``) -- the same ground truth ``grain`` is
    checked against at extraction time (``spec_extractor.validate_spec``), re-checked here so
    a coined ``grain`` is caught whether or not a caller ran that validator first. ``None``
    (the default) skips the check rather than treating an empty vocabulary as "nothing is
    valid" -- a caller that has not wired the vocabulary through yet should not have every
    spec's grain rejected. Gaps are emitted in the contract's kind order -- data gaps first,
    then the shape's parameters -- and each is written for the customer: what to connect or
    confirm.
    """
    draft = draft or {}
    gaps: List[Dict[str, str]] = []
    tables = available_tables(reader)
    snapshot = reader.snapshot()

    # KPIs: unknown, blocked or partial; each missing table is its own gap as well.
    referenced: List[KpiDefinition] = []
    for identifier in spec.get("kpi_identifiers") or ():
        kpi = kpis.get(identifier)
        if kpi is None:
            gaps.append(
                _gap(
                    "blocked_kpi",
                    identifier,
                    "No KPI with this identifier is registered. Choose one from the registry, or propose it.",
                )
            )
            continue
        referenced.append(kpi)
        feasibility = assess_feasibility(kpi, tables)
        if feasibility.verdict is FeasibilityVerdict.READY:
            continue
        missing = sorted(feasibility.missing_tables)
        kind = (
            "blocked_kpi"
            if feasibility.verdict is FeasibilityVerdict.BLOCKED
            else "partial_kpi"
        )
        gaps.append(
            _gap(
                kind,
                identifier,
                f"Missing from the semantic snapshot: {', '.join(missing)}. Connect the source that holds them to close this.",
            )
        )
        for table in missing:
            gaps.append(
                _gap(
                    "missing_table",
                    table,
                    f"Read by {identifier}; not in the semantic snapshot {snapshot.version}.",
                )
            )

    # Concepts the spec names.
    for entity in spec.get("entities") or ():
        concept = (
            entity.get("concept")
            if isinstance(entity, Mapping)
            else getattr(entity, "concept", None)
        )
        if not concept:
            continue
        if reader.get_concept(concept) is None:
            gaps.append(
                _gap(
                    "unknown_concept",
                    concept,
                    "No accepted concept with this name in the semantic layer. An enrichment proposal from the next refresh may add one, or the question may mean a different concept.",
                )
            )

    # The area.
    area = spec.get("business_area")
    if area and area not in reader.list_business_areas():
        gaps.append(
            _gap(
                "unsupported_area",
                area,
                "No accepted concept in the semantic layer belongs to this business area yet; connect a source that does.",
            )
        )

    # The grain, against the vocabulary -- the same closed set `validate_spec` checks at
    # extraction time, re-checked here so a coined value is caught regardless of whether that
    # validator ran first (see `grains`'s docstring above for why `None` skips this).
    grain = spec.get("grain")
    if grain and grains is not None and grain not in grains:
        gaps.append(
            _gap(
                "unknown_grain",
                str(grain),
                "No accepted grain in the vocabulary matches this value. Add it to the "
                "customer overlay if the business genuinely has it -- do not pick the "
                "nearest term.",
            )
        )

    # Join paths between the source tables the referenced KPIs read, where both are present.
    for kpi in referenced:
        present = sorted(t for t in kpi.source_tables if t in tables)
        for left, right in itertools.combinations(present, 2):
            if reader.find_join_path(left, right) is None:
                gaps.append(
                    _gap(
                        "no_join_path",
                        f"{left} -> {right}",
                        f"The semantic layer holds no join between {left} and {right}, which {kpi.identifier} reads together. A mapping proposal from the next harvest may add one.",
                    )
                )

    # The chosen autonomy level's parameters.
    shape = shape_for(autonomy, level)
    for slot in SHAPE_PARAMETERS[shape]:
        if not _slot_filled(slot, spec, draft):
            source = _SLOT_SOURCES[slot]
            gaps.append(
                _gap(
                    "missing_parameter",
                    slot,
                    f"The {shape.value} shape needs {slot}; filled by {source}."
                    if not source.startswith("no builder")
                    else f"The {shape.value} shape needs {slot}; {source}.",
                )
            )

    return {
        "use_case_id": str(spec.get("use_case_id") or ""),
        "feasible": not gaps,
        "gaps": gaps,
        "semantic_snapshot": snapshot.version,
        "checked_at": (now or datetime.now(timezone.utc))
        .isoformat()
        .replace("+00:00", "Z"),
    }


async def check_spec_gaps(step_input: StepInput) -> StepOutput:
    """Run the gap check over ``additional_data["spec"]`` against this deployment's semantic
    layer and registry. Never refuses: a gap is the answer, not a failure -- and
    ``permits_or_blocks`` is false for this step by the alignment rules.

    ``async def`` for the registry convention's exemption (every executor here does I/O), so
    the KPI registry read, the vocabulary load and the semantic-layer round trip are each run
    off the event loop through ``asyncio.to_thread`` -- the same pattern ``check_preflight``
    already uses for its own blocking read.
    """
    import psycopg

    from egpt_retail.app.db import psycopg_dsn
    from egpt_retail.app.semantic import open_semantic_reader
    from egpt_retail.app.settings import RetailSettings
    from egpt_retail.contracts.vocabulary import load_vocabulary_set
    from egpt_retail.kpi.store import KpiRegistryStore

    options: Mapping[str, Any] = step_input.additional_data or {}
    spec: Mapping[str, Any] = options.get("spec") or {}
    settings = RetailSettings()

    def _read_kpis_and_vocabulary() -> Tuple[Dict[str, KpiDefinition], Any]:
        connection = psycopg.connect(psycopg_dsn(settings.retail_db_url))
        try:
            kpis = {kpi.identifier: kpi for kpi in KpiRegistryStore(connection).list()}
        finally:
            connection.close()
        return kpis, load_vocabulary_set()

    def _assess(kpis: Dict[str, KpiDefinition], vocabularies: Any) -> Dict[str, Any]:
        with open_semantic_reader(settings) as reader:
            return assess_gaps(
                spec,
                reader=reader,
                kpis=kpis,
                draft=options.get("draft"),
                autonomy=options.get("autonomy"),
                level=options.get("level"),
                grains=vocabularies["grain"].values,
            )

    kpis, vocabularies = await asyncio.to_thread(_read_kpis_and_vocabulary)
    result = await asyncio.to_thread(_assess, kpis, vocabularies)
    return StepOutput(content=result, success=True)
