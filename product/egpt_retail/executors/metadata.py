"""What each registered executor is for, as data the plan view renders (R9).

``PlanStep`` carries a name, a description, inputs, outputs and ``permits_or_blocks`` for every
step of a compiled workflow. The stored workflow holds only the executor's name, and the
executor's docstring is prose for a reader, not fields for a screen -- so the fields live here,
keyed on the same ``__name__`` the registry resolves. A test asserts this table and
``build_registry()`` name exactly the same executors, so an executor cannot be added to one
without the other.

``permits_or_blocks`` is the alignment rule made data: true for exactly the executors that
return ``stop=True`` -- ``decide_load_verdict``, ``check_preflight``, ``materialise_gold`` on
its refusals -- and never for anything a model touches.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Tuple

__all__ = ["ExecutorMetadata", "EXECUTOR_METADATA"]


@dataclass(frozen=True)
class ExecutorMetadata:
    name: str
    description: str
    inputs: Tuple[str, ...]
    outputs: Tuple[str, ...]
    permits_or_blocks: bool
    estimated_seconds: int


EXECUTOR_METADATA: Mapping[str, ExecutorMetadata] = MappingProxyType(
    {
        # R4 -- landing to silver
        "load_landing_files": ExecutorMetadata(
            "Load the raw drop",
            "Read the landing files into landing.raw_row as-is and open the load's ledger row.",
            ("paths",),
            ("load_id", "landed_rows"),
            False,
            30,
        ),
        "conform_silver_entities": ExecutorMetadata(
            "Conform silver",
            "Run the five conformance statements and record the per-stream counts.",
            ("load_id",),
            ("conformed_rows",),
            False,
            30,
        ),
        "check_row_count_delta": ExecutorMetadata(
            "Check row-count delta",
            "Measure landed rows against the previous successful load and check silver did not shrink.",
            ("load_id",),
            ("gate_results",),
            False,
            2,
        ),
        "check_key_null_rates": ExecutorMetadata(
            "Check key null rates",
            "Measure missing, blank or uncoercible key and watermark columns per stream.",
            ("load_id",),
            ("gate_results",),
            False,
            2,
        ),
        "check_referential_integrity": ExecutorMetadata(
            "Check referential integrity",
            "Count orphans per declared edge over silver.",
            ("load_id",),
            ("gate_results",),
            False,
            2,
        ),
        "decide_load_verdict": ExecutorMetadata(
            "Decide the load verdict",
            "Aggregate every gate result, stamp quality.load_run.verdict, and stop the run if any gate refused.",
            ("gate_results",),
            ("verdict",),
            True,
            1,
        ),
        # R7 -- the executor library
        "check_preflight": ExecutorMetadata(
            "Preflight",
            "Confirm every KPI the spec names is ready and its gold is fresh; block naming the reason otherwise.",
            ("spec",),
            ("preflight_result",),
            True,
            1,
        ),
        "record_provenance": ExecutorMetadata(
            "Record provenance",
            "Write the provenance record for this run from what the earlier steps noted.",
            ("datasets_read", "sql_executed", "freshness"),
            ("provenance_ref",),
            False,
            1,
        ),
        "deliver_in_app": ExecutorMetadata(
            "Deliver",
            "Post the result in-app; under dry_run, record what would have been sent instead.",
            ("explanation", "provenance_ref"),
            ("output_ref",),
            False,
            1,
        ),
        # R5 -- gold
        "materialise_gold": ExecutorMetadata(
            "Materialise gold",
            "Bind, guard, compile and execute every ready KPI's formula into gold.kpi_value; refuse after a blocked load.",
            ("as_of",),
            ("kpi_runs",),
            True,
            60,
        ),
        # K9 / K11 -- Lane K's two deterministic steps
        "assess_kpi_feasibility": ExecutorMetadata(
            "Assess KPI feasibility",
            "Subtract the tables the semantic layer holds from the tables each registered "
            "KPI's formula needs, and report ready, partial or blocked with the missing "
            "tables named.",
            ("business_area",),
            ("summary", "kpis"),
            # Never blocks. A blocked KPI is this step's answer, not its failure -- the
            # caller decides what to do with it.
            False,
            3,
        ),
        "detect_business_areas": ExecutorMetadata(
            "Detect business areas",
            "Every area the shipped ontology covers, each marked supported when a confirmed "
            "mapping backs it, with the concepts and tables that back it named.",
            ("ontology_overlay",),
            ("supported_areas", "areas"),
            False,
            2,
        ),
        "read_gold": ExecutorMetadata(
            "Read gold",
            "Read the requested KPIs from their gold views at the bound as-of date, through the guard.",
            ("kpi_identifiers", "as_of"),
            ("rows", "freshness"),
            False,
            2,
        ),
        # R9 -- spec, gap check and plan
        "check_spec_gaps": ExecutorMetadata(
            "Check spec gaps",
            "Set differences and join-path lookups between the spec and the semantic layer and registry, plus the autonomy shape's parameters.",
            ("spec", "draft"),
            ("gap_check",),
            False,
            1,
        ),
        "derive_plan": ExecutorMetadata(
            "Derive the plan",
            "The compiled workflow as plan steps -- a view over the stored component, never authored.",
            ("workflow_component_id",),
            ("plan",),
            False,
            1,
        ),
        # R10 -- the RFQ chain's own
        "sample_rows": ExecutorMetadata(
            "Sample supporting rows",
            "Pull a bounded sample through the SQL guard for the explanation to cite.",
            ("rows",),
            ("movers", "friction", "samples", "kpi_identifiers", "snapshot_version"),
            # The API contract's own example plan (paths/use-cases.yaml): true, the same as
            # check_preflight -- a guard refusal here must stop the chain before an
            # explanation runs against rows nobody guaranteed were safe to read.
            True,
            2,
        ),
        "check_explanation_grounding": ExecutorMetadata(
            "Check explanation grounding",
            "Verify the funnel-explanation agent's output cites only what sample_rows "
            "handed it; stop the chain before delivery otherwise.",
            ("explanation", "movers"),
            ("explanation",),
            # Added after review found the schema's own checkers were reachable from nowhere
            # at run time; paths/use-cases.yaml was updated in the same change to name it as
            # step 5. True, the same as sample_rows: a failed check must stop the chain before
            # record_provenance/deliver_in_app run against an explanation nobody verified.
            True,
            1,
        ),
        # R14 -- monitoring: execution health, silent schedules, the quality/drift pass
        "assess_execution_health": ExecutorMetadata(
            "Assess execution health",
            "Per use case, real run history reduced to last status, 7-day runs/failures, "
            "gates waiting on a person, and token-cost drift.",
            ("use_cases",),
            ("use_cases",),
            # Reports, never gates -- a monitoring pass has no chain to stop.
            False,
            3,
        ),
        "list_silent_schedules": ExecutorMetadata(
            "List silent schedules",
            "Schedules whose cron cadence implies a fire this run history does not show, "
            "independent of whether the schedule is currently enabled.",
            ("schedules",),
            ("silent_schedules",),
            False,
            2,
        ),
        "assess_quality": ExecutorMetadata(
            "Assess quality",
            "Verdict from R15's golden pass rate and this package's input-drift score, "
            "whichever are available -- a use case can run green and still be flagged.",
            ("use_cases",),
            ("use_cases",),
            False,
            2,
        ),
    }
)
