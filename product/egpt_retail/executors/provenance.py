"""``record_provenance`` (R7, G4): the executor every path calls to write one provenance
record per answer.

The record's data model and storage are a framework primitive
(``egpt.answer_provenance``, ``docs/plan/delivery-plan.md``'s "Where the code lives" table:
"every deployment needs an answer it can trace"). This module is the thin retail seam that
assembles one from a running workflow step and writes it.

``record_provenance`` sits downstream of steps this package does not own -- R5's
``read_gold``, R10's ``sample_rows``, an agent step -- in the real compiled plan (the API
contract's own example names all six). A private, R7-only ``additional_data`` bag (R4's own
``"r4_load"`` pattern) would hide data from exactly the packages that need to contribute to
it, so the five ``note_*`` functions below are the public, documented, cross-package
convention every future data-reading or SQL-executing executor calls into.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from egpt.answer_provenance import MODEL_TIERS, ProvenanceRecord, awrite_provenance
from egpt.workflow.types import StepInput, StepOutput

from egpt_retail.app.db import build_control_plane_engine
from egpt_retail.app.settings import RetailSettings

__all__ = [
    "PROV_KEY",
    "note_dataset_read",
    "note_sql_execution",
    "note_row_sample",
    "note_freshness",
    "note_retrieval_hit",
    "record_provenance",
]

log = logging.getLogger(__name__)

#: The cross-package convention key. Deliberately not namespaced "r7_..." -- unlike R4's
#: private "r4_load" bag, this one is a public contract other lanes' executors write into.
PROV_KEY = "prov"

_PROV_LISTS = (
    "datasets_read",
    "sql_executed",
    "rows_sampled",
    "freshness",
    "retrieval_hits",
)


def _bag(step_input: StepInput) -> Dict[str, List[Dict[str, Any]]]:
    if step_input.additional_data is None:
        step_input.additional_data = {}
    return step_input.additional_data.setdefault(
        PROV_KEY, {name: [] for name in _PROV_LISTS}
    )


def note_dataset_read(
    step_input: StepInput,
    *,
    table: str,
    schema: str = "silver",
    as_of: Optional[str] = None,
) -> None:
    """Record one dataset an upstream step read. Call this from any executor that reads a
    table on a path that ends in ``record_provenance``."""
    entry: Dict[str, Any] = {"schema": schema, "table": table}
    if as_of is not None:
        entry["as_of"] = as_of
    _bag(step_input)["datasets_read"].append(entry)


def note_sql_execution(
    step_input: StepInput,
    *,
    statement: str,
    row_count: Optional[int] = None,
    duration_ms: Optional[int] = None,
    guard: Optional[str] = None,
) -> None:
    """Record one SQL statement an upstream step executed. ``guard`` accepts R6's
    ``GuardDecision`` value (``"allowed"``/``"refused"``) directly, when a guarded SQLTools
    was involved."""
    entry: Dict[str, Any] = {"statement": statement}
    if row_count is not None:
        entry["row_count"] = row_count
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if guard is not None:
        entry["guard"] = guard
    _bag(step_input)["sql_executed"].append(entry)


def note_row_sample(
    step_input: StepInput, *, table: str, rows: List[Dict[str, Any]]
) -> None:
    """Record a bounded sample pulled for an explanation to cite."""
    _bag(step_input)["rows_sampled"].append({"table": table, "rows": rows})


def note_freshness(
    step_input: StepInput, *, table: str, as_of: str, computed_at: Optional[str] = None
) -> None:
    """Record the freshness stamp a gold row carried."""
    entry: Dict[str, Any] = {"table": table, "as_of": as_of}
    if computed_at is not None:
        entry["computed_at"] = computed_at
    _bag(step_input)["freshness"].append(entry)


def note_retrieval_hit(
    step_input: StepInput,
    *,
    source: str,
    id: str,
    score: float,
    origin: Optional[str] = None,
) -> None:
    """Record one unified-retrieval (K10) hit, with its origin score."""
    entry: Dict[str, Any] = {"source": source, "id": id, "score": score}
    if origin is not None:
        entry["origin"] = origin
    _bag(step_input)["retrieval_hits"].append(entry)


def _run_id(step_input: StepInput, options: Dict[str, Any]) -> str:
    """Same fallback chain R4's warehouse.py uses for workflow_run_id, plus a fresh id as the
    last resort -- every provenance record needs one, unlike R4's optional workflow_run_id."""
    pinned = options.get("run_id")
    if pinned:
        return str(pinned)
    session_run_id = getattr(step_input.workflow_session, "run_id", None)
    if session_run_id:
        return str(session_run_id)
    return str(uuid.uuid4())


def _model_tier(step_input: StepInput, options: Dict[str, Any]) -> str:
    """R8 (the real tier-routing table) is P3, after R7 -- this executor cannot map a model id
    to a tier. An explicit override wins (matching PlanStep.model_tier being static plan
    metadata); otherwise, if any upstream step actually ran a model (StepOutput.metrics set)
    with no tier supplied, that is a real gap once R8 exists and is logged as one; a run with
    no model step at all is genuinely "none" -- R7's own "workflow completes without a model"
    evidence.
    """
    override = options.get("model_tier")
    if override is not None:
        if override not in MODEL_TIERS:
            raise ValueError(
                f"model_tier override must be one of {MODEL_TIERS}, got {override!r}"
            )
        return override
    outputs = step_input.previous_step_outputs or {}
    if any(getattr(output, "metrics", None) is not None for output in outputs.values()):
        log.warning(
            "record_provenance: an upstream step ran a model but no model_tier was supplied "
            "in additional_data -- R8's tier-routing table is not built yet (P3), so this run "
            "is recorded as model_tier='none' even though a model served part of it."
        )
    return "none"


async def record_provenance(step_input: StepInput) -> StepOutput:
    """Write one provenance record for this run. Never refuses -- ``permits_or_blocks: false``
    in the API contract's example plan."""
    options: Dict[str, Any] = step_input.additional_data or {}
    bag = _bag(step_input)

    record = ProvenanceRecord(
        run_id=_run_id(step_input, options),
        use_case_id=options.get("use_case_id"),
        produced_at=datetime.now(timezone.utc),
        semantic_snapshot=options.get("semantic_snapshot", "unversioned"),
        vocabulary_version=options.get("vocabulary_version"),
        skill=options.get("skill"),
        model_tier=_model_tier(step_input, options),
        datasets_read=tuple(bag["datasets_read"]),
        sql_executed=tuple(bag["sql_executed"]),
        rows_sampled=tuple(bag["rows_sampled"]) or None,
        freshness=tuple(bag["freshness"]) or None,
        retrieval_hits=tuple(bag["retrieval_hits"]) or None,
    )

    engine = build_control_plane_engine(RetailSettings())
    try:
        await awrite_provenance(engine, record)
    finally:
        engine.dispose()

    return StepOutput(content={"provenance_ref": record.run_id})
