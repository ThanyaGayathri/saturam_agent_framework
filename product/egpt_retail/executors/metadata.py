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
        # R12 -- alerts
        "evaluate_alert_condition": ExecutorMetadata(
            "Evaluate the alert condition",
            "Compare one KPI's current gold value to its threshold; stop the chain when the "
            "condition is not met, logging the outcome either way.",
            ("kpi_identifier", "operator", "threshold", "rows"),
            ("fired", "value", "detail"),
            # The same posture as decide_load_verdict and check_preflight: the one thing that
            # decides whether write_alert/deliver_in_app run afterward.
            True,
            1,
        ),
        "write_alert": ExecutorMetadata(
            "Write the alert",
            "Write one alert_events row in the contract's AlertEvent shape; only reached "
            "once evaluate_alert_condition did not stop the chain.",
            ("fired", "value", "severity", "recipients"),
            ("alert_event",),
            False,
            1,
        ),
        "evaluate_alerts": ExecutorMetadata(
            "Evaluate alerts",
            "Every enabled alert definition, checked against gold once per pass; fires and "
            "delivers through R7's own path, and bumps last_evaluated_at either way.",
            ("as_of",),
            ("evaluated", "fired", "events"),
            # Never itself refuses the run -- an individual definition's own KPI being
            # missing or unevaluable is reported per-definition, not a stop.
            False,
            5,
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
        # R15 -- the publish gate
        "check_golden_gate": ExecutorMetadata(
            "Check the publish gate",
            "Confirm the golden-set score is over threshold and a person has approved this "
            "exact (skill, version, score); block naming which check failed otherwise.",
            ("skill_version", "golden_pass_rate", "rehearsal_review"),
            ("gate_result",),
            # Same shape as check_preflight/sample_rows/check_explanation_grounding: a failing
            # check must stop the chain before a prompt version publishes on an unreviewed or
            # under-threshold score.
            True,
            1,
        ),
        # K15 / K-15 -- the document-to-record template's five, and the Act shape's four.
        # The branches among them (screen, resolve, guardrails, cap) stop the run only when
        # nothing at all is left -- no documents and no exceptions -- because an exception
        # that never reaches the Notify stage is one nobody sees. ``permits_or_blocks`` is
        # true for exactly those four: they are the ones that return ``stop=True``.
        "receive_messages": ExecutorMetadata(
            "Receive messages",
            "Poll the source mailbox for new messages matching the filter, each carried once by message id.",
            ("source mailbox", "message filter"),
            ("messages",),
            False,
            10,
        ),
        "screen_documents": ExecutorMetadata(
            "Screen documents",
            "Exactly one PDF, under the size limit, from a sender that is not ignored, with a text layer; read it once. Anything else is an exception before a model is called.",
            ("messages", "document type"),
            ("documents", "exceptions"),
            True,
            5,
        ),
        "resolve_entities": ExecutorMetadata(
            "Resolve against master data",
            "Customer by sender domain then name; ship-to among the customer's sites; product by part number, code, then description. Below threshold or a tie is an exception with the closest candidate.",
            ("documents", "master data", "match thresholds"),
            ("documents", "exceptions"),
            True,
            10,
        ),
        "apply_hard_limits": ExecutorMetadata(
            "Apply guardrails",
            "The enabled record guardrails over every resolved document (or the amount bound over a proposal's changes). Any failure holds that document; the rest go on.",
            ("documents", "guardrails"),
            ("documents", "exceptions"),
            True,
            2,
        ),
        "build_record": ExecutorMetadata(
            "Build the record",
            "Map the resolved fields onto the target's objects. A required field the mapping cannot fill is an exception, not a blank.",
            ("documents", "target objects", "created as"),
            ("documents", "exceptions"),
            False,
            1,
        ),
        "apply_per_run_cap": ExecutorMetadata(
            "Apply the change limit",
            "At most N writes this run; everything past the cap is held and reported, in arrival order.",
            ("documents", "per-run cap"),
            ("documents", "exceptions"),
            True,
            1,
        ),
        "write_to_target": ExecutorMetadata(
            "Write to the target",
            "Create each built record in the write target, or under dry run report what would have been created. A failed write is an exception on that document; the rest still go.",
            ("documents", "write target"),
            ("documents", "exceptions"),
            False,
            30,
        ),
        "record_rollback_point": ExecutorMetadata(
            "Record the rollback point",
            "One ledger row per record created, with the reverse action the mapping declared, so the run can be undone record by record.",
            ("documents", "rollback"),
            ("rollback",),
            False,
            2,
        ),
        "send_email": ExecutorMetadata(
            "Notify by email",
            "The run's created records and exceptions, by class, to the configured address through the source mailbox's own SMTP; composed and held under dry run.",
            ("documents", "exceptions", "recipient"),
            ("delivery",),
            False,
            5,
        ),
        # K23 -- the Inform shape's report chain
        "compare_to_baseline": ExecutorMetadata(
            "Compare to baseline",
            "Each KPI at the run's date against gold at the baseline date, per KPI and per breakdown row; a missing baseline date is materialised once from the KPI's own formula.",
            ("kpis", "comparison", "window"),
            ("comparison_result",),
            False,
            10,
        ),
        "decompose_change": ExecutorMetadata(
            "Decompose the change",
            "Where a KPI's change sits, by arithmetic: each slice of each candidate dimension gets its contribution (rate and mix effects for a rate), its surprise against its own history and a support floor; dimensions are ranked, the winning slice searched one level down, the rows behind it fetched.",
            (
                "comparison_result",
                "metrics",
                "comparison",
                "window",
                "dimensions",
                "sensitivity",
                "evidence columns",
            ),
            ("comparison_result", "diagnosis"),
            True,
            30,
        ),
        "score_risk": ExecutorMetadata(
            "Score the risk",
            "Every open quote's risk of not converting within its category's usual time to close, from a logistic model trained on the fact's own closed history at each run (the newest quarter held back and scored for the numbers readiness shows); the drivers behind each score in words; the account's RFM segment and its churn flag at the churn window; still-in-range quotes ranked first by value at risk.",
            ("kpis", "metrics", "churn window", "risk levels", "top n"),
            ("risk",),
            True,
            30,
        ),
        "recommend": ExecutorMetadata(
            "Recommend the next action",
            "One Recommendation record per at-risk quote carried from the scoring, with the sales play the catalogue chose by rule from its drivers, the account's last loss, its RFM state, stage and value, and why; an alert per high-risk quote for its rep when the outcome asks for them. Written to the recommendations store (held under dry run): an open record is refreshed, a decided one kept.",
            ("risk", "outcome", "top n"),
            ("recommendation_result",),
            True,
            10,
        ),
        "track_outcomes": ExecutorMetadata(
            "Track what happened",
            "Every recommendation whose horizon has ended and whose outcome is not yet written is looked up on the fact -- converted in time, lapsed, or still open -- and the outcome written on the record; from all tracked records the use case's own precision per risk level. Held under dry run.",
            ("kpis",),
            ("outcomes",),
            False,
            10,
        ),
        "send_alerts": ExecutorMetadata(
            "Send the alerts",
            "The alerts as one digest: the high-risk quotes grouped by the rep who owns each, with the catalogue's play, to the alert recipient (the report's recipient when unset); composed and held under dry run; nothing when the outcome asked for recommendations only.",
            ("recommendation_result", "recipient", "outcome"),
            ("alerts",),
            False,
            10,
        ),
        "check_report_grounding": ExecutorMetadata(
            "Check the narrative against the numbers",
            "Every figure the explanation states must be one the comparison carried; a narrative that invents one is withheld and the report goes out with the numbers alone.",
            ("comparison_result", "explanation"),
            ("grounding",),
            False,
            1,
        ),
        "render_report": ExecutorMetadata(
            "Render the report",
            "The comparison and the grounded narrative as an email-safe HTML report and its text twin.",
            ("comparison_result", "explanation", "title"),
            ("report", "headline"),
            False,
            1,
        ),
        "send_report": ExecutorMetadata(
            "Send the report",
            "The HTML report to the recipients through the mailbox's SMTP; composed and held under dry run.",
            ("report", "recipient"),
            ("delivery",),
            False,
            5,
        ),
    }
)
