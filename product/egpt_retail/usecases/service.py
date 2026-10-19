"""The use-case service (K16): what the router does, without the HTTP.

Activate compiles the stored spec through the orchestration compiler, refuses what is not
publishable, and stores the compiled workflow with a bumped version. Preflight answers
"may this run now" with checks a person can act on: a spec present, a workflow that
compiles, credentials that resolve, a model configured. Run builds the runnable workflow
from the stored config (``runtime.py``), executes it in this process as a background task,
and records the run row as it goes -- queued, running, then succeeded or failed with the
last executor's content as the run's ``result``.

Nothing here is Salesforce- or mailbox-specific: the slot values decide what the executors
connect to, and ``run_parameters`` is where a configured source becomes a connection.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
import uuid
from dataclasses import asdict
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from egpt.workflow.types import StepInput

from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.use_case import UseCaseShape, UseCaseTemplate
from egpt_retail.documents.golden import (
    GENERATED_CASE_SOURCE,
    case_to_message,
    generate_cases,
    score_case,
)
from egpt_retail.documents.guardrails import rules_from
from egpt_retail.documents.mailbox import fetch_messages, filter_from, mailbox_from
from egpt_retail.documents.mapping import mapping_from, stale_sources
from egpt_retail.documents.roles import ROLES, roles_needed
from egpt_retail.documents.samples import (
    expected_extraction,
    expected_outcome,
    sample_master_data,
    sample_messages,
)
from egpt_retail.documents.templates import template_from
from egpt_retail.orchestration.compiler import CompiledWorkflow, compile_workflow
from egpt_retail.usecases.credentials import CredentialUnavailable, resolve_credentials
from egpt_retail.usecases.runtime import (
    build_explanation_agent,
    build_extraction_agent,
    run_parameters,
    workflow_from_compiled,
)
from egpt_retail.usecases.store import UseCaseStore

__all__ = ["UseCaseService", "compiled_to_dict", "ActivationRefused", "NotFound"]

log = logging.getLogger(__name__)

SCHEDULE_SECONDS: Mapping[str, int] = {
    "every_minute": 60,
    "every_5_minutes": 300,
    "every_15_minutes": 900,
    "hourly": 3600,
    "daily": 86400,
    "weekly": 7 * 86400,
    "monthly": 30 * 86400,
}
#: The interface's poll-interval values, mapped to the contract's cadence.
_INTERVAL_ALIASES = {
    "1m": "every_minute",
    "5m": "every_5_minutes",
    "15m": "every_15_minutes",
    "hourly": "hourly",
}


class NotFound(LookupError):
    pass


class ActivationRefused(ValueError):
    """Activation (or a run) was refused; ``preflight`` is the contract's PreflightResult."""

    def __init__(self, preflight: Dict[str, Any]) -> None:
        super().__init__(
            "; ".join(c["reason"] for c in preflight["checks"] if not c["passed"])
        )
        self.preflight = preflight


def _now() -> datetime:
    return datetime.now(timezone.utc)


def compiled_to_dict(compiled: CompiledWorkflow) -> Dict[str, Any]:
    """The compiled workflow as JSON: enums to their values, plus the two derived fields."""

    def plain(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()}
        if isinstance(value, list):
            return [plain(v) for v in value]
        return value

    out = plain(asdict(compiled))
    out["publishable"] = compiled.publishable
    out["refusal_reasons"] = list(compiled.refusal_reasons)
    return out


def normalise_schedule(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    return _INTERVAL_ALIASES.get(text, text)


#: The sensitivity slot in the words the form uses: how sure before naming a cause.
_SURE_WORDS = {"clear": "very sure", "usual": "balanced", "more": "show more"}


class UseCaseService:
    def __init__(
        self,
        store: UseCaseStore,
        *,
        executors: Mapping[str, Callable[..., Any]],
        settings: Optional[RetailSettings] = None,
        agent_factory: Optional[Callable[[str, str, Any], Any]] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.store = store
        self.executors = executors
        self.settings = settings
        self._agent_factory = agent_factory
        self._environ = environ
        self._tasks: Dict[str, asyncio.Task] = {}

    # -- catalog -------------------------------------------------------------------------

    def catalog_view(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        """The contract's ``UseCase`` from a stored row."""
        last = self.store.last_run(str(row["id"]))
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row.get("description") or "",
            "business_area": row.get("business_area") or "sales",
            "subject_area": "",
            "tier": None,
            "status": row.get("status") or "draft",
            "source": row.get("source") or "user",
            "level": int(row.get("level") or 1),
            "autonomy": row.get("autonomy") or "copilot",
            "tags": list(row.get("tags") or []),
            "sample_question": (row.get("spec") or {}).get("question", "")
            if row.get("spec")
            else "",
            "supporting_questions": [],
            "process_id": None,
            "linked_kpi_ids": list(
                (row.get("spec") or {}).get("kpi_identifiers") or []
            ),
            "workflow_component_id": row.get("workflow_component_id"),
            "version": int(row.get("version") or 0),
            "gallery_order": None,
            "last_run": (
                {
                    "id": last["id"],
                    "status": last["status"],
                    "started_at": last["started_at"],
                    "finished_at": last.get("finished_at"),
                }
                if last
                else None
            ),
            "updated_at": row.get("updated_at") or _now(),
        }

    async def acatalog_view(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        return await asyncio.to_thread(self.catalog_view, row)

    # -- compile / preflight / activate ----------------------------------------------------

    def compile(self, row: Mapping[str, Any]) -> CompiledWorkflow:
        spec = row.get("spec")
        if not spec:
            raise ActivationRefused(
                self._preflight_result(
                    row["id"],
                    [
                        (
                            "spec_present",
                            False,
                            "No spec stored yet; save the form first.",
                        )
                    ],
                )
            )
        return compile_workflow(
            spec,
            registered_executors=set(self.executors),
            shape=spec.get("shape"),
            template=spec.get("template"),
            autonomy=row.get("autonomy"),
            draft=row.get("draft") or {},
            use_case_id=str(row["id"]),
        )

    def _preflight_result(
        self, use_case_id: str, checks: List[tuple]
    ) -> Dict[str, Any]:
        # A check is (name, passed, reason) or, advisory, (name, passed, reason, True):
        # an advisory check is reported and never blocks -- the golden cases, whose
        # verdict is the person's to weigh, not a gate the platform closes.
        rows = [
            {
                "name": c[0],
                "passed": bool(c[1]),
                "reason": c[2],
                "advisory": bool(c[3]) if len(c) > 3 else False,
            }
            for c in checks
        ]
        blocking = [c for c in rows if not c["passed"] and not c["advisory"]]
        return {
            "use_case_id": use_case_id,
            "allowed": not blocking,
            "checks": rows,
            "blocked_by": [c["name"] for c in blocking],
            "checked_at": _now(),
        }

    def preflight(self, use_case_id: str, *, mode: str = "live") -> Dict[str, Any]:
        row = self.store.get(use_case_id)
        if row is None:
            raise NotFound(use_case_id)
        checks: List[tuple] = []
        spec = row.get("spec") or {}
        if not spec:
            checks.append(
                ("spec_present", False, "No spec stored yet; save the form first.")
            )
            return self._preflight_result(use_case_id, checks)
        checks.append(
            (
                "spec_present",
                True,
                f"Shape {spec.get('shape')}, template {spec.get('template') or 'shape sequence'}.",
            )
        )
        try:
            compiled = self.compile(row)
        except Exception as exc:  # a compile error is a check result, not a 500
            checks.append(("workflow_compiles", False, f"The compiler refused: {exc}"))
            return self._preflight_result(use_case_id, checks)
        if compiled.publishable:
            checks.append(
                (
                    "workflow_compiles",
                    True,
                    f"{len(compiled.config.get('steps') or [])} steps, every slot filled.",
                )
            )
        else:
            checks.append(
                (
                    "workflow_compiles",
                    False,
                    "; ".join(compiled.refusal_reasons) or compiled.notes,
                )
            )

        if compiled.template is UseCaseTemplate.DOCUMENT_TO_RECORD:
            params = compiled.parameters
            for name, kind, slot in (
                ("mailbox_credentials", "mailbox", "source mailbox"),
                ("target_credentials", "salesforce", "write target"),
            ):
                value = params.get(slot)
                source = value if isinstance(value, Mapping) else {"id": value}
                try:
                    resolved = resolve_credentials(
                        source, kind=kind, environ=self._environ
                    )
                    label = (
                        resolved.get("user") or resolved.get("login_url") or "resolved"
                    )
                    checks.append((name, True, f"{slot}: {label}."))
                except CredentialUnavailable as exc:
                    checks.append(
                        (
                            name,
                            mode == "rehearse",
                            f"{slot}: {exc}"
                            + (
                                " (rehearse runs without it)"
                                if mode == "rehearse"
                                else ""
                            ),
                        )
                    )
            env = self._environ if self._environ is not None else os.environ
            has_model = bool(env.get("AZURE_OPENAI_API_KEY"))
            checks.append(
                (
                    "extraction_model",
                    has_model or mode == "rehearse",
                    "Azure OpenAI configured for the extraction step."
                    if has_model
                    else "AZURE_OPENAI_API_KEY is not set; the extraction step has no model"
                    + (
                        " (rehearse uses the sample set's expected extraction)"
                        if mode == "rehearse"
                        else "."
                    ),
                )
            )
            # The mapping's document sources against the template's fields (K24): a
            # shipped mapping under a template drafted from the customer's own sample names
            # fields it never extracts. A required target left blank is an exception on
            # every document, so it fails readiness; an optional one is named and passes.
            mapping = self._mapping_for(params)
            try:
                template = template_from(
                    params.get("extraction template") or "purchase_order"
                )
            except (KeyError, ValueError):
                template = None
            if mapping is not None and template is not None:
                stale = stale_sources(mapping, template)
                blank_required = sorted(
                    {f"{s['object']}.{s['target']}" for s in stale if s["required"]}
                )
                sources = list(dict.fromkeys(str(s["source"]) for s in stale))
                checks.append(
                    (
                        "mapping_sources",
                        not blank_required or mode == "rehearse",
                        f"Mapping {mapping.label} fits template {template.label}."
                        if not stale
                        else (
                            f"Mapping {mapping.label} reads "
                            f"{', '.join(sources)}, which template {template.label} does "
                            "not extract"
                            + (
                                f"; required {', '.join(blank_required)} would be blank."
                                if blank_required
                                else "; those fields stay blank."
                            )
                        ),
                    )
                )
            # Guardrails read a field of the template (K21); one whose field the template
            # does not extract is skipped at run time, never passed -- so a check the
            # person believes is active is not. Named here, failing live.
            if template is not None:
                known = {n for n, _ in template.header} | {n for n, _ in template.lines}
                blind = [
                    f"{r.kind} ({r.field})"
                    for r in rules_from(params.get("guardrails") or [])
                    if r.field and r.field not in known
                ]
                checks.append(
                    (
                        "guardrail_fields",
                        not blind or mode == "rehearse",
                        "Every guardrail reads a field the template extracts."
                        if not blind
                        else "Guardrails reading fields template "
                        f"{template.label} does not extract, so they never hold a "
                        f"record: {', '.join(blind)}. Re-suggest the guardrails from "
                        "this template.",
                    )
                )
            golden = self.golden_summary(use_case_id)
            if golden["with_expected"]:
                checks.append(
                    (
                        "golden_cases",
                        golden["failed"] == 0 or mode == "rehearse",
                        f"{golden['passed']} of {golden['with_expected']} golden cases pass"
                        + (
                            f", {golden['failed']} failing: "
                            + ", ".join(str(f["label"]) for f in golden["failing"][:3])
                            if golden["failed"]
                            else ""
                        )
                        + (
                            f", {golden['untested']} not run yet"
                            if golden["untested"]
                            else ""
                        )
                        + ".",
                        True,  # advisory: reported, never a gate
                    )
                )
        if compiled.shape is UseCaseShape.INFORM:
            checks += self._inform_checks(compiled.parameters, mode=mode)
            if compiled.template is UseCaseTemplate.METRIC_DIAGNOSIS:
                checks += self._diagnosis_checks(compiled.parameters, mode=mode)
            if compiled.template is UseCaseTemplate.AT_RISK:
                checks += self._at_risk_checks(compiled.parameters, mode=mode)
        return self._preflight_result(use_case_id, checks)

    def _probe_gold(self, parameters: Mapping[str, Any], result: Any) -> Dict[str, Any]:
        """Every KPI the use case names has a gold row, and how fresh it is."""
        from egpt_retail.app.db import (
            build_warehouse_engine,
            pooled_control_plane_connection,
        )
        from egpt_retail.kpi.store import KpiRegistryStore
        from egpt_retail.warehouse.gold import latest_as_of

        metrics = [str(m) for m in (parameters.get("metrics") or [])]
        if not metrics:
            return result(False, "No KPI named.")
        settings = self.settings or RetailSettings()
        connection = pooled_control_plane_connection(settings)
        try:
            store = KpiRegistryStore(connection)
            unknown = [m for m in metrics if store.get(m) is None]
        finally:
            connection.close()
        if unknown:
            return result(False, f"Not in the registry: {', '.join(unknown)}.")
        engine = build_warehouse_engine(settings)
        try:
            dates = {m: latest_as_of(engine, m) for m in metrics}
        finally:
            engine.dispose()
        missing = [m for m, d in dates.items() if d is None]
        latest = {m: d.isoformat() for m, d in dates.items() if d is not None}
        if missing:
            return result(
                False,
                f"No gold row yet for {', '.join(missing)}; compute them on the KPI screen "
                "(or the nightly run will).",
                latest=latest,
            )
        oldest = min(latest.values())
        return result(
            True,
            f"{len(metrics)} KPI(s) in gold; latest business date {max(latest.values())}"
            + (f", oldest {oldest}" if oldest != max(latest.values()) else "")
            + ".",
            latest=latest,
        )

    def _probe_dry_answer(
        self, parameters: Mapping[str, Any], result: Any
    ) -> Dict[str, Any]:
        """The numbers as they are now: read gold for the KPIs and say each value -- the
        analytics equivalent of "36 messages match the filter"."""
        from egpt_retail.executors.gold import read_gold

        metrics = [str(m) for m in (parameters.get("metrics") or [])]
        output = asyncio.run(
            read_gold(StepInput(additional_data={"kpi_identifiers": metrics}))
        )
        content = output.content if isinstance(output.content, Mapping) else {}
        values = content.get("kpis") or {}
        parts = []
        for identifier in metrics:
            entry = values.get(identifier)
            if not entry:
                parts.append(f"{identifier}: no value")
                continue
            rows = entry.get("rows") or []
            if len(rows) == 1 and set(rows[0]) <= {
                "value",
                "as_of",
                "computed_at",
                "run_id",
                "source_snapshot",
            }:
                parts.append(f"{identifier} = {rows[0].get('value')}")
            else:
                parts.append(f"{identifier}: {len(rows)} rows")
        missing = [m.get("kpi_identifier") for m in content.get("missing") or []]
        return result(
            not missing,
            "; ".join(parts) + ".",
            values={k: v.get("rows") for k, v in values.items()},
        )

    def _inform_checks(
        self, parameters: Mapping[str, Any], *, mode: str
    ) -> List[tuple]:
        """The Inform shape's preflight (K23): every KPI named is in the registry, the
        delivery has an address and a mailbox to send from, and a model is configured for
        the explanation (the report goes out without one; the check says so)."""
        checks: List[tuple] = []
        metrics = [str(m) for m in (parameters.get("metrics") or [])]
        if not metrics:
            checks.append(
                ("kpis_registered", False, "No KPI named; nothing to report.")
            )
        else:
            registered, unknown = self._registered_kpis(metrics)
            checks.append(
                (
                    "kpis_registered",
                    not unknown,
                    f"{len(registered)} KPI(s) in the registry: {', '.join(registered)}."
                    if not unknown
                    else f"Not in the registry: {', '.join(unknown)}.",
                )
            )
        recipient = str(parameters.get("recipient") or "")
        outputs = [str(o) for o in (parameters.get("outputs") or ["report"])]
        if "@" in recipient:
            try:
                resolve_credentials(
                    {"credentialRef": "env:gmail"},
                    kind="mailbox",
                    environ=self._environ,
                )
                checks.append(
                    ("delivery", True, f"Report to {recipient} through the mailbox.")
                )
            except CredentialUnavailable as exc:
                checks.append(
                    (
                        "delivery",
                        mode == "rehearse",
                        f"Report to {recipient}, but no mailbox to send from: {exc}",
                    )
                )
        else:
            checks.append(
                (
                    "delivery",
                    True,
                    "In-app only; no email address, so nothing is sent outside the app."
                    if "report" in outputs
                    else "No delivery configured.",
                )
            )
        env = self._environ if self._environ is not None else os.environ
        has_model = bool(env.get("AZURE_OPENAI_API_KEY"))
        checks.append(
            (
                "explanation_model",
                True,
                "Azure OpenAI configured for the explanation step; every figure it writes is checked against the numbers."
                if has_model
                else "AZURE_OPENAI_API_KEY is not set: the report goes out with the numbers and no narrative.",
            )
        )
        return checks

    def _at_risk_checks(
        self, parameters: Mapping[str, Any], *, mode: str
    ) -> List[tuple]:
        """The at-risk kind's preflight (K26): the model trained on the fact's closed
        history separates the quotes that convert from those that do not (holdout AUC
        at or above the floor), there are open quotes to score, and the outcome the
        person asked for is one the kind can produce."""
        from egpt_retail.executors.predict import AUC_FLOOR, risk_model_readiness
        from egpt_retail.executors.recommend import OUTCOMES

        checks: List[tuple] = []
        outcome = str(parameters.get("outcome") or "both").strip().lower()
        checks.append(
            (
                "risk_outcome",
                outcome in OUTCOMES,
                f"The run ends in {outcome.replace('both', 'a recommendation and an alert')}."
                if outcome in OUTCOMES
                else f"Outcome {outcome!r} is not one of recommend, alert, both.",
            )
        )
        try:
            readiness = risk_model_readiness()
        except Exception as exc:  # the warehouse not answering is the finding
            checks.append(("risk_model", False, f"{type(exc).__name__}: {exc}"))
            return checks
        if not readiness.get("ready"):
            checks.append(("risk_model", False, str(readiness.get("reason"))))
        else:
            checks.append(
                (
                    "risk_model",
                    True,
                    f"Trained on {readiness.get('closed_quotes')} closed quotes "
                    f"({readiness.get('training_rows')} snapshots); on the newest "
                    f"{readiness.get('holdout_rows')} held-out rows the model's AUC is "
                    f"{readiness.get('auc')} (floor {AUC_FLOOR}), Brier "
                    f"{readiness.get('brier')}, base rate {readiness.get('base_rate')}.",
                )
            )
        open_quotes = int(readiness.get("open_quotes") or 0)
        checks.append(
            (
                "risk_subjects",
                open_quotes > 0,
                f"{open_quotes} open quotes to score."
                if open_quotes
                else "No open quotes to score at this date.",
            )
        )
        return checks

    def _diagnosis_checks(
        self, parameters: Mapping[str, Any], *, mode: str
    ) -> List[tuple]:
        """The diagnostic kind's preflight (K25): one KPI whose formula the decomposition
        can read, a fact with something to search across, and enough rows in the period
        for a slice to clear the support floor at the chosen sensitivity."""
        from egpt_retail.diagnostics.decompose import support_floor
        from egpt_retail.executors.diagnostics import diagnosis_options

        metrics = [str(m) for m in (parameters.get("metrics") or [])]
        checks: List[tuple] = []
        if len(metrics) != 1:
            checks.append(
                (
                    "diagnosis_kpi",
                    False,
                    "A diagnosis explains one KPI; "
                    + (f"{len(metrics)} are named." if metrics else "none is named."),
                )
            )
            return checks
        window = parameters.get("window")
        try:
            options = diagnosis_options(
                metrics[0], window_days=int(window) if window else None
            )
        except Exception as exc:  # the warehouse not answering is the finding
            checks.append(("diagnosis_kpi", False, f"{type(exc).__name__}: {exc}"))
            return checks
        if not options.get("diagnosable"):
            checks.append(
                (
                    "diagnosis_kpi",
                    False,
                    f"{metrics[0]} cannot be decomposed: {options.get('reason')}.",
                )
            )
            return checks
        checks.append(
            (
                "diagnosis_kpi",
                True,
                f"{metrics[0]} is a {options['kind']} over {options['table']}: "
                + (
                    f"{options['numerator']} / {options['denominator']}."
                    if options.get("denominator")
                    else f"{options['numerator']}."
                ),
            )
        )
        # The slot falls back to the spec's grain ("overall") when the form set nothing;
        # only a list names columns.
        raw_dims = parameters.get("dimensions")
        wanted = [str(d) for d in raw_dims] if isinstance(raw_dims, list) else []
        offered = [d["column"] for d in options.get("dimensions") or []]
        searched = [d for d in wanted if d in offered] or offered
        unknown = [d for d in wanted if d not in offered]
        checks.append(
            (
                "diagnosis_dimensions",
                bool(searched),
                (
                    f"Searches across {', '.join(searched)}."
                    if searched
                    else "The fact carries no column a change can be searched across."
                )
                + (
                    f" Not on the fact, ignored: {', '.join(unknown)}."
                    if unknown
                    else ""
                ),
            )
        )
        sensitivity = str(parameters.get("sensitivity") or "usual")
        rows = int(options.get("rows_in_period") or 0)
        floor = support_floor(rows, sensitivity=sensitivity)
        checks.append(
            (
                "diagnosis_support",
                rows >= floor * 2 or mode == "rehearse",
                f"{rows} rows in the period, {options.get('rows_in_baseline')} in the one "
                f"before ({options.get('step_days')} days apart); a slice needs {floor} "
                f"rows to be named ({_SURE_WORDS.get(sensitivity, sensitivity)}); the norm is each "
                f"slice's last {options.get('history_periods')} periods."
                + (
                    ""
                    if rows >= floor * 2
                    else " Too few rows for any slice to clear the floor: widen the "
                    "window or choose 'show more'."
                ),
            )
        )
        return checks

    @staticmethod
    def _dashboard_url(compiled: Mapping[str, Any]) -> str:
        dashboard_id = compiled.get("dashboard_id")
        base = os.environ.get("EGPT_RETAIL_APP_URL", "http://localhost:3001").rstrip(
            "/"
        )
        return f"{base}/kpis/dashboards/{dashboard_id}" if dashboard_id else ""

    def compose_dashboard(self, row: Mapping[str, Any], compiled: Any) -> Optional[str]:
        """The dashboard an Inform use case's ``outputs`` ask for (K23): one tile per KPI
        -- a number for a KPI at grain overall, a bar breakdown otherwise -- under
        KPIs -> Dashboards, named after the use case, refreshed nightly like any other.
        Idempotent by name: a second activation reuses the dashboard it made. Returns
        the dashboard id, or None when the use case asked for no dashboard."""
        parameters = dict(
            getattr(compiled, "parameters", None) or compiled.get("parameters") or {}
        )
        outputs = [str(o) for o in (parameters.get("outputs") or ["report"])]
        if "dashboard" not in outputs:
            return None
        metrics = [str(m) for m in (parameters.get("metrics") or [])]
        if not metrics:
            return None
        from egpt_retail.app.db import pooled_control_plane_connection
        from egpt_retail.contracts.dashboards import (
            ChartKind,
            RefreshSchedule,
            Tile,
            TilePosition,
            Visibility,
        )
        from egpt_retail.dashboards.store import DashboardStore
        from egpt_retail.kpi.store import KpiRegistryStore

        settings = self.settings or RetailSettings()
        name = str(row.get("title") or row["id"])
        connection = pooled_control_plane_connection(settings)
        try:
            dashboards = DashboardStore(connection)
            dashboards.ensure_schema()
            for existing in dashboards.list_visible_to("use_case"):
                if existing.name == name:
                    return existing.id
            registry = KpiRegistryStore(connection)
            tiles: List[Tile] = []
            # A diagnosis (K25) shows its KPI itself in the "Why it changed" panel, so
            # its tiles are the drill-down: the KPI's breakdown siblings in the registry
            # (same concept, a grain, an identifier that extends the KPI's -- the
            # `..._by_brand` next to `..._closed_won_rate`). The KPI alone when there are
            # none, so the dashboard is never empty.
            raw_template = getattr(compiled, "template", None) or (
                compiled.get("template") if hasattr(compiled, "get") else None
            )
            template = str(getattr(raw_template, "value", raw_template) or "")
            query_tiles: List[Tile] = []
            if template == "metric_diagnosis" and metrics:
                base = registry.get(metrics[0])
                # One breakdown tile per dimension the diagnosis searches across: the
                # KPI's own formula grouped by that column, as a query tile the dashboard
                # refreshes like any other. Three dimensions chosen, three widgets.
                if base is not None:
                    query_tiles = self._diagnosis_breakdown_tiles(
                        base, parameters, ChartKind, Tile, TilePosition
                    )
                if base is not None and not query_tiles:
                    siblings = [
                        k.identifier
                        for k in registry.list(concept=base.concept or None)
                        if k.identifier != base.identifier
                        and k.grain not in {"overall", "time_period"}
                        and k.identifier.startswith(base.identifier)
                        and k.active
                    ]
                    metrics = siblings or metrics
                if query_tiles:
                    metrics = []
            definitions = {m: d for m in metrics if (d := registry.get(m)) is not None}
            number_row = [
                m
                for m in metrics
                if m in definitions and definitions[m].grain == "overall"
            ]
            breakdowns = [
                m for m in metrics if m in definitions and m not in number_row
            ]
            for index, identifier in enumerate(number_row):
                definition = registry.get(identifier)
                tiles.append(
                    Tile(
                        id=f"tl_{index + 1}",
                        kpi_identifier=identifier,
                        title=definition.name if definition else identifier,
                        description=(definition.description or "")
                        if definition
                        else "",
                        chart=ChartKind.NUMBER,
                        position=TilePosition(
                            x=(index % 4) * 3, y=(index // 4) * 2, w=3, h=2
                        ),
                    )
                )
            y = ((len(number_row) + 3) // 4) * 2
            for index, identifier in enumerate(breakdowns):
                definition = registry.get(identifier)
                chart = (
                    ChartKind.BAR
                    if definition and definition.grain != "time_period"
                    else ChartKind.LINE
                )
                tiles.append(
                    Tile(
                        id=f"tl_{len(number_row) + index + 1}",
                        kpi_identifier=identifier,
                        title=definition.name if definition else identifier,
                        description=(definition.description or "")
                        if definition
                        else "",
                        chart=chart,
                        position=TilePosition(
                            x=(index % 2) * 6, y=y + (index // 2) * 4, w=6, h=4
                        ),
                    )
                )
            dashboard = dashboards.create(
                name=name,
                owner="use_case",
                description=str(
                    (row.get("spec") or {}).get("question")
                    or row.get("description")
                    or ""
                ),
                tiles=query_tiles + tiles,
                refresh_schedule=RefreshSchedule.NIGHTLY,
                visibility=Visibility.TEAM,
            )
            if query_tiles:
                self._compute_query_tiles(dashboard, settings)
            return dashboard.id
        finally:
            connection.close()

    @staticmethod
    def _diagnosis_breakdown_tiles(
        base: Any,
        parameters: Mapping[str, Any],
        chart_kind: Any,
        tile_cls: Any,
        position_cls: Any,
    ) -> List[Any]:
        """The diagnosis's drill-down (K25): the KPI's formula grouped by each column the
        change is searched across -- the ones the form chose, else every candidate -- as
        bar tiles. Built from the formula's parts, so a KPI the decomposition can read is a
        KPI the dashboard can break down; ``{as_of}`` becomes the refresh's own date."""
        from egpt_retail.contracts.dashboards import ChartAxis, TileQuery, TileSource
        from egpt_retail.diagnostics.parts import FormulaShapeError, parts_of
        from egpt_retail.executors.diagnostics import diagnosis_options

        try:
            parts = parts_of(str(base.sql))
        except FormulaShapeError:
            return []
        raw_dims = parameters.get("dimensions")
        chosen = [str(d) for d in raw_dims] if isinstance(raw_dims, list) else []
        try:
            offered = [
                d["column"]
                for d in (diagnosis_options(base.identifier).get("dimensions") or [])
            ]
        except Exception:  # the warehouse not answering: no breakdowns, not a failure
            offered = []
        columns = [d for d in chosen if d in offered] or offered
        if not columns:
            return []
        # CAST, not ``::numeric``: the runner binds ``:name`` placeholders, and a double
        # colon would read as one.
        value = (
            f"ROUND(CAST({parts.numerator} AS numeric) * {parts.scale} / NULLIF({parts.denominator}, 0), {parts.precision})"
            if parts.kind == "ratio"
            else f"ROUND(CAST({parts.numerator} AS numeric), {parts.precision})"
        )
        where = parts.where.replace("{as_of}", "CURRENT_DATE")
        table = parts.table.split(".")[-1]
        tiles: List[Any] = []
        for index, column in enumerate(columns[:9]):
            sql = (
                f"SELECT {column}, {value} AS value FROM {parts.table} WHERE {where} "
                f"GROUP BY {column} ORDER BY value DESC LIMIT 12"
            )
            tiles.append(
                tile_cls(
                    id=f"t{index + 1}",
                    kpi_identifier=None,
                    chart=chart_kind.BAR,
                    position=position_cls(
                        x=(index % 3) * 4, y=(index // 3) * 4, w=4, h=4
                    ),
                    title=f"{base.name} by {column.replace('_', ' ')}",
                    source=TileSource.QUERY,
                    query=TileQuery(
                        sql=sql,
                        source_tables=frozenset({table}),
                        x=ChartAxis(column=column, label=column.replace("_", " ")),
                        y=(
                            ChartAxis(
                                column="value",
                                label=base.name,
                                unit=str(getattr(base, "unit", "") or ""),
                            ),
                        ),
                    ),
                    description=f"{base.name} in the current window, one bar per {column.replace('_', ' ')}; the slices the diagnosis ranks.",
                )
            )
        return tiles

    @staticmethod
    def _compute_query_tiles(dashboard: Any, settings: RetailSettings) -> None:
        """Run the new query tiles once so the dashboard opens with numbers rather than
        waiting for its first refresh. Best effort: a failure here leaves the tiles to the
        refresh, never the activation."""
        import asyncio

        from egpt_retail.app.db import pooled_control_plane_connection
        from egpt_retail.dashboards.queries import arefresh_query_tiles
        from egpt_retail.dashboards.query_results import DashboardQueryResultStore

        try:
            connection = pooled_control_plane_connection(settings)
            try:
                results = DashboardQueryResultStore(connection)
                results.ensure_schema()
                asyncio.run(arefresh_query_tiles(dashboard, settings, results))
            finally:
                connection.close()
        except Exception as exc:  # pragma: no cover - the refresh will do it
            log.warning("query tiles not computed at composition: %s", exc)

    def _registered_kpis(self, metrics: Sequence[str]) -> Tuple[List[str], List[str]]:
        from egpt_retail.app.db import pooled_control_plane_connection
        from egpt_retail.kpi.store import KpiRegistryStore

        settings = self.settings or RetailSettings()
        registered: List[str] = []
        unknown: List[str] = []
        try:
            connection = pooled_control_plane_connection(settings)
        except Exception:
            return [], list(metrics)
        try:
            store = KpiRegistryStore(connection)
            for identifier in metrics:
                (registered if store.get(identifier) else unknown).append(identifier)
        finally:
            connection.close()
        return registered, unknown

    def current_parameters(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        """The parameters a probe, a mailbox listing or a generated case should use: the
        current spec compiled now, so an edit on the form is what the readiness screen
        checks -- never a snapshot a rehearsal left behind. Falls back to the snapshot,
        then the raw spec, when the current spec does not compile."""
        try:
            return dict(self.compile(row).parameters)
        except Exception:
            compiled = row.get("compiled") or {}
            return dict(
                compiled.get("parameters")
                or (row.get("spec") or {}).get("parameters")
                or {}
            )

    def probe(self, use_case_id: str, check: str) -> Dict[str, Any]:
        """One live connectivity probe (K19): actually open the thing the preflight only
        confirmed credentials for, and report what was found. ``check`` is one of
        ``mailbox``, ``target``, ``master_data``, ``model``, ``golden``."""
        row = self.store.get(use_case_id)
        if row is None:
            raise NotFound(use_case_id)
        parameters = self.current_parameters(row)
        started = time.monotonic()

        def result(passed: bool, reason: str, **extra: Any) -> Dict[str, Any]:
            return {
                "name": check,
                "passed": passed,
                "reason": reason,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                **extra,
            }

        try:
            if check == "mailbox":
                options = run_parameters(parameters, environ=self._environ, strict=True)
                mailbox = mailbox_from(options.get("source mailbox"))
                messages = fetch_messages(
                    mailbox,
                    filter_from(options.get("message filter")),
                    mark_seen=False,
                    headers_only=True,
                )
                return result(
                    True,
                    f"Logged into {mailbox.user}; {len(messages)} message(s) match the filter right now.",
                    count=len(messages),
                )
            if check == "target":
                target = parameters.get("write target")
                credentials = resolve_credentials(
                    target if isinstance(target, Mapping) else {},
                    kind="salesforce",
                    environ=self._environ,
                )
                from egpt_retail.documents.salesforce import client_from

                target_object = self._mapping_for(parameters)
                target_object = target_object.object if target_object else "Order"
                client = client_from(credentials)
                try:
                    objects = client.query(f"SELECT COUNT() FROM {target_object}")
                    instance = client.instance_url
                finally:
                    client.close()
                return result(
                    True,
                    f"Token issued by {instance}; the integration can read and create "
                    f"{target_object} records.",
                    instance=instance,
                    existing_orders=len(objects),
                    existing_records=len(objects),
                    object=target_object,
                )
            if check == "master_data":
                master = self._master_rows_for(parameters)
                counts = {k: len(v) for k, v in master.items() if isinstance(v, list)}
                needed = self._roles_needed(parameters)
                missing = [
                    k
                    for k in ("customer", "contact", "ship_to", "product")
                    if k in needed and not counts.get(k)
                ]
                summary = ", ".join(
                    f"{ROLES[k].label.lower() if k in ROLES else k} {n}"
                    for k, n in counts.items()
                    if k in needed
                )
                return result(
                    not missing,
                    f"Read from the org: {summary or 'nothing this use case resolves against'}."
                    if not missing
                    else f"The org has no {', '.join(ROLES[k].label.lower() for k in missing)} rows to resolve against ({summary}).",
                    counts=counts,
                    roles=sorted(needed),
                )
            if check == "model":
                env = self._environ if self._environ is not None else os.environ
                if not env.get("AZURE_OPENAI_API_KEY"):
                    return result(
                        False,
                        "AZURE_OPENAI_API_KEY is not set; the extraction step has no model.",
                    )
                from egpt.integrations.models.azure import AzureOpenAI
                from egpt.models.message import Message

                settings = self.settings or RetailSettings()
                model = AzureOpenAI(id=settings.retail_azure_deployment)
                response = model.response(
                    messages=[
                        Message(role="user", content="Reply with the single word OK.")
                    ]
                )
                text = str(getattr(response, "content", "") or "").strip()
                return result(
                    bool(text),
                    f"{settings.retail_azure_deployment} answered in {int((time.monotonic() - started) * 1000)} ms."
                    if text
                    else f"{settings.retail_azure_deployment} returned nothing.",
                    model=settings.retail_azure_deployment,
                )
            if check == "golden":
                golden = self.golden_summary(use_case_id)
                extra = {
                    "cases": golden["cases"],
                    "with_expected": golden["with_expected"],
                    "pass_count": golden["passed"],
                    "fail_count": golden["failed"],
                    "untested": golden["untested"],
                }
                if not golden["with_expected"]:
                    return result(
                        True,
                        f"{golden['cases']} case(s), none with an expected output yet; rehearse to author them."
                        if golden["cases"]
                        else "No golden cases yet; the rehearse screen creates them.",
                        **extra,
                    )
                reason = (
                    f"{golden['passed']} of {golden['with_expected']} golden cases pass"
                )
                if golden["failed"]:
                    reason += f", {golden['failed']} failing: " + ", ".join(
                        str(f["label"]) for f in golden["failing"][:3]
                    )
                if golden["untested"]:
                    reason += f", {golden['untested']} not run yet"
                return result(golden["failed"] == 0, reason + ".", **extra)
            if check == "gold":
                return self._probe_gold(parameters, result)
            if check == "delivery":
                recipient = str(parameters.get("recipient") or "")
                if "@" not in recipient:
                    return result(True, "In-app only; nothing is emailed.")
                credentials = resolve_credentials(
                    {"credentialRef": "env:gmail"},
                    kind="mailbox",
                    environ=self._environ,
                )
                mailbox = mailbox_from(credentials)
                import smtplib

                smtp = smtplib.SMTP(mailbox.smtp_host, mailbox.smtp_port, timeout=20)
                try:
                    smtp.starttls()
                    smtp.login(mailbox.user, mailbox.password)
                finally:
                    smtp.quit()
                return result(
                    True,
                    f"SMTP login as {mailbox.user}; the report will go to {recipient}.",
                )
            if check == "dry_answer":
                return self._probe_dry_answer(parameters, result)
            if check == "diagnosis":
                rows = self._diagnosis_checks(parameters, mode="live")
                return result(
                    all(r[1] for r in rows), " ".join(str(r[2]) for r in rows)
                )
            if check == "risk_model":
                rows = self._at_risk_checks(parameters, mode="live")
                return result(
                    all(r[1] for r in rows), " ".join(str(r[2]) for r in rows)
                )
            raise NotFound(check)
        except NotFound:
            raise
        except CredentialUnavailable as exc:
            return result(False, str(exc))
        except Exception as exc:  # the probe failing is the answer, not a 500
            return result(False, f"{type(exc).__name__}: {str(exc)[:240]}")

    async def aprobe(self, use_case_id: str, check: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self.probe, use_case_id, check)

    async def apreflight(
        self, use_case_id: str, *, mode: str = "live"
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(lambda: self.preflight(use_case_id, mode=mode))

    def activate(
        self,
        use_case_id: str,
        *,
        level: Optional[int] = None,
        autonomy: Optional[str] = None,
        schedule: Optional[str] = None,
    ) -> Dict[str, Any]:
        row = self.store.get(use_case_id)
        if row is None:
            raise NotFound(use_case_id)
        fields: Dict[str, Any] = {}
        if level is not None:
            fields["level"] = int(level)
        if autonomy is not None:
            fields["autonomy"] = str(autonomy)
        if schedule is not None:
            fields["schedule"] = normalise_schedule(schedule)
        if fields:
            row = self.store.update(use_case_id, **fields) or row
        result = self.preflight(use_case_id)
        if not result["allowed"]:
            raise ActivationRefused(result)
        compiled = self.compile(row)
        version = int(row.get("version") or 0) + 1
        snapshot = compiled_to_dict(compiled)
        if compiled.shape is UseCaseShape.INFORM:
            try:
                dashboard_id = self.compose_dashboard(row, compiled)
            except Exception as exc:  # the dashboard is an output, not the activation
                log.warning("dashboard composition failed for %s: %s", use_case_id, exc)
                dashboard_id = None
            if dashboard_id:
                snapshot["dashboard_id"] = dashboard_id
        updated = self.store.update(
            use_case_id,
            status="activated",
            version=version,
            compiled=snapshot,
            workflow_component_id=f"usecase__{use_case_id}",
        )
        assert updated is not None
        return updated

    async def aactivate(self, use_case_id: str, **kwargs: Any) -> Dict[str, Any]:
        return await asyncio.to_thread(lambda: self.activate(use_case_id, **kwargs))

    def pause(self, use_case_id: str) -> Dict[str, Any]:
        row = self.store.get(use_case_id)
        if row is None:
            raise NotFound(use_case_id)
        updated = self.store.update(use_case_id, status="paused")
        assert updated is not None
        return updated

    async def apause(self, use_case_id: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self.pause, use_case_id)

    # -- runs ----------------------------------------------------------------------------

    def _agent_for(self, role: str, agent_id: str, template_id: Any) -> Any:
        if self._agent_factory is not None:
            return self._agent_factory(role, agent_id, template_id)
        env = self._environ if self._environ is not None else os.environ
        if not env.get("AZURE_OPENAI_API_KEY"):
            return None
        from egpt.integrations.models.azure import AzureOpenAI

        from egpt_retail.routing.providers import (
            AZURE_PROVIDER_KEY,
            prompt_cache_config,
        )

        settings = self.settings or RetailSettings()
        cache_config = prompt_cache_config(settings, AZURE_PROVIDER_KEY)
        if role == "explanation":
            model = AzureOpenAI(
                id=settings.retail_azure_small_deployment
                or settings.retail_azure_deployment,
                **cache_config,
            )
            return build_explanation_agent(
                model,
                agent_id=agent_id,
                diagnosis=template_id == "metric_diagnosis",
                at_risk=template_id == "at_risk",
            )
        model = AzureOpenAI(id=settings.retail_azure_deployment, **cache_config)
        return build_extraction_agent(model, agent_id=agent_id, template_id=template_id)

    def build_run_options(
        self,
        row: Mapping[str, Any],
        *,
        run_id: str,
        mode: str,
        request: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """The ``additional_data`` for one run: parameters resolved, side effects held for a
        rehearsal, the sample set injected where the template has one."""
        compiled = row.get("compiled") or {}
        parameters = dict(compiled.get("parameters") or {})
        options = run_parameters(
            parameters, environ=self._environ, strict=mode == "live"
        )
        options.update(
            {
                "run_id": run_id,
                "use_case_id": row["id"],
                "dry_run": mode == "rehearse",
                # The mailbox is read, never written: no flag, no label, no delete. A
                # message is carried once because its id is remembered across runs.
                "mark_seen": False,
                "processed_ids": (
                    self.store.processed_message_ids(str(row["id"]))
                    if mode == "live"
                    else []
                ),
            }
        )
        if compiled.get("shape") == UseCaseShape.INFORM.value:
            spec = row.get("spec") or {}
            options.update(
                {
                    "as_of": str(
                        (request or {}).get("as_of") or date.today().isoformat()
                    ),
                    "question": str(spec.get("question") or ""),
                    "use_case_title": str(row.get("title") or ""),
                    "dashboard_url": self._dashboard_url(compiled),
                    "backfill_baseline": True,
                }
            )
        if (
            mode == "rehearse"
            and compiled.get("template") == UseCaseTemplate.DOCUMENT_TO_RECORD.value
        ):
            # What a rehearsal runs over (K19). Chosen messages from the connected mailbox
            # (writes held, nothing flagged, the processed-id ledger not consulted); the use
            # case's own golden cases; or the generated set. Absent: the mailbox when one
            # resolves and a model can read it, else the generated set.
            request = dict(request or {})
            try:
                resolve_credentials(
                    options.get("source mailbox") or {},
                    kind="mailbox",
                    environ=self._environ,
                )
                mailbox_available = True
            except CredentialUnavailable:
                mailbox_available = False
            env = self._environ if self._environ is not None else os.environ
            model_available = bool(env.get("AZURE_OPENAI_API_KEY")) and not (
                self._agent_factory is not None
                and self._agent_factory("extraction", "probe", "purchase_order") is None
            )
            source = str(request.get("source") or "")
            if not source:
                source = (
                    "mailbox" if mailbox_available and model_available else "generated"
                )
            if source == "mailbox" and not mailbox_available:
                source = "generated"
            if source == "mailbox":
                options["rehearsal_source"] = "mailbox"
                if request.get("message_ids"):
                    options["message_ids"] = [str(i) for i in request["message_ids"]]
            else:
                cases = self.cases_for_rehearsal(
                    row, source=source, case_ids=request.get("case_ids")
                )
                options["messages"] = [case_to_message(c) for c in cases]
                options["rehearsal_source"] = (
                    "cases" if source == "cases" else "generated"
                )
                options["rehearsal_case_ids"] = [c["id"] for c in cases]
                if not model_available:
                    # The curated purchase-order set carries its expected extraction, so a
                    # deployment without a model still rehearses those; a case the person
                    # uploaded cannot be read without one and fails at Extract, honestly.
                    sample_ids = {
                        f"<{c['id']}@cases>": (c.get("expected") or {}).get("sample_id")
                        for c in cases
                    }

                    def stand_in(document: Mapping[str, Any]) -> Mapping[str, Any]:
                        sample_id = sample_ids.get(str(document["message"]["id"]))
                        if not sample_id:
                            raise RuntimeError(
                                "no extraction model is configured, and this case has no "
                                "expected extraction to stand in for one"
                            )
                        return expected_extraction(str(sample_id))

                    options["extractor"] = stand_in
            try:
                resolve_credentials(
                    options.get("write target") or {},
                    kind="salesforce",
                    environ=self._environ,
                )
            except CredentialUnavailable:
                options["master data"] = sample_master_data()
                options["write target"] = {"type": "salesforce"}
        return options

    def cases_for_rehearsal(
        self,
        row: Mapping[str, Any],
        *,
        source: str,
        case_ids: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """The stored cases a rehearsal runs: the ones asked for, or every one; for the
        generated source, the generated ones -- created now when the use case has none."""
        use_case_id = str(row["id"])
        cases = self.store.list_cases(use_case_id)
        if source == "generated":
            generated = [c for c in cases if c.get("source") == GENERATED_CASE_SOURCE]
            # A generated set is written in the template's field names and expects the
            # outcomes its guardrails produce; one made for an earlier template would
            # score the new one against fields it never extracts. Remade when the
            # template it was made for is not the current one.
            current = self._template_fingerprint(row)
            if generated and any(
                (c.get("expected") or {}).get("template") != current for c in generated
            ):
                for stale in generated:
                    self.store.delete_case(str(stale["id"]))
                generated = []
            if not generated:
                for raw in self.generate_cases(row):
                    generated.append(
                        self.store.create_case(
                            use_case_id=use_case_id,
                            label=raw["label"],
                            document_b64=raw["document_b64"],
                            sender=raw["sender"],
                            subject=raw["subject"],
                            filename=raw["filename"],
                            source=GENERATED_CASE_SOURCE,
                            expected=raw["expected"],
                        )
                    )
            cases = generated
        if case_ids:
            wanted = {str(i) for i in case_ids}
            cases = [c for c in cases if c["id"] in wanted]
        return cases

    def generate_cases(self, row: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """Cases the platform invents for this use case: the curated sixteen for the shipped
        purchase-order template, the field-driven generator for any other."""
        parameters = self.current_parameters(row)
        template = template_from(
            parameters.get("extraction template") or "purchase_order"
        )
        fingerprint = self._template_fingerprint(row)
        if _is_shipped_purchase_order(template):
            out = []
            for message in sample_messages():
                sample_id = message.id[1:5].upper()
                attachment = message.attachments[0]
                expected = expected_extraction(sample_id)
                out.append(
                    {
                        "label": f"{sample_id}: {message.subject}",
                        "sender": message.sender,
                        "subject": message.subject,
                        "filename": attachment.filename,
                        "document_b64": base64.b64encode(attachment.data).decode(
                            "ascii"
                        ),
                        "expected": {
                            "sample_id": sample_id,
                            "template": fingerprint,
                            "outcome": expected_outcome(sample_id),
                            "extracted": {
                                k: expected[k]
                                for k in (
                                    "po_number",
                                    "customer_name",
                                    "due_date",
                                    "order_total",
                                )
                            },
                            "line_count": len(expected["lines"]),
                        },
                    }
                )
            return out
        master = self._master_rows_for(parameters)
        out = generate_cases(
            template,
            master,
            rules_from(parameters.get("guardrails") or "all"),
            roles=self._roles_needed(parameters),
        )
        stamped: List[Dict[str, Any]] = []
        for case in out:
            raw_expected: Any = case.get("expected")
            merged: Dict[str, Any] = (
                dict(raw_expected) if isinstance(raw_expected, Mapping) else {}
            )
            merged["template"] = fingerprint
            stamped.append({**case, "expected": merged})
        return stamped

    def _template_fingerprint(self, row: Mapping[str, Any]) -> str:
        """The template a generated set was made for: its id and field names."""
        parameters = self.current_parameters(row)
        try:
            template = template_from(
                parameters.get("extraction template") or "purchase_order"
            )
        except (KeyError, ValueError):
            return ""
        return template_fingerprint(template)

    @staticmethod
    def _mapping_for(parameters: Mapping[str, Any]) -> Optional[Any]:
        objects = parameters.get("target objects") or "order_orderitem"
        try:
            return mapping_from(
                objects if isinstance(objects, Mapping) else str(objects)
            )
        except (KeyError, ValueError):
            return None

    @classmethod
    def _roles_needed(cls, parameters: Mapping[str, Any]) -> set:
        """The master-data roles this use case resolves (``documents/roles.py``)."""
        return roles_needed(
            cls._mapping_for(parameters), parameters.get("guardrails") or "all"
        )

    def _master_rows_for(self, parameters: Mapping[str, Any]) -> Dict[str, Any]:
        """Master data for generating cases: the org's when it resolves, the sample set's
        otherwise."""
        target = parameters.get("write target")
        source: Mapping[str, Any] = target if isinstance(target, Mapping) else {}
        try:
            credentials = resolve_credentials(
                source, kind="salesforce", environ=self._environ
            )
            from egpt_retail.documents.salesforce import client_from, load_master_data

            client = client_from(credentials)
            try:
                return load_master_data(client)
            finally:
                client.close()
        except (
            Exception
        ):  # no org reachable: the sample master data is honest enough to generate from
            return sample_master_data()

    def list_mailbox_messages(self, use_case_id: str) -> List[Dict[str, Any]]:
        """The messages in the connected mailbox that match the use case's filter, without
        bodies or attachment bytes: what the person picks a rehearsal from."""
        row = self.store.get(use_case_id)
        if row is None:
            raise NotFound(use_case_id)
        parameters = self.current_parameters(row)
        options = run_parameters(parameters, environ=self._environ, strict=True)
        mailbox = mailbox_from(options.get("source mailbox"))
        messages = fetch_messages(
            mailbox,
            filter_from(options.get("message filter")),
            mark_seen=False,
            headers_only=True,
        )
        processed = set(self.store.processed_message_ids(use_case_id))
        return [
            {**m.as_dict(with_data=False), "processed_before": m.id in processed}
            for m in messages
        ]

    async def alist_mailbox_messages(self, use_case_id: str) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.list_mailbox_messages, use_case_id)

    def start_run(
        self,
        use_case_id: str,
        *,
        mode: str = "live",
        trigger: str = "manual",
        request: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create the run row and return it; ``execute`` does the work. ``request`` is what a
        rehearsal runs over (K19): ``{"source": "mailbox" | "cases" | "generated",
        "message_ids": [...], "case_ids": [...]}``; absent means the mailbox when one is
        connected, else the generated set."""
        row = self.store.get(use_case_id)
        if row is None:
            raise NotFound(use_case_id)
        if mode == "rehearse":
            # A rehearsal runs before activation -- that is what it is for. Compile the
            # draft now (it must at least be publishable) and keep the preview on the row;
            # activation recompiles and bumps the version.
            result = self.preflight(use_case_id, mode="rehearse")
            if not result["allowed"]:
                raise ActivationRefused(result)
            compiled = self.compile(row)
            row = (
                self.store.update(use_case_id, compiled=compiled_to_dict(compiled))
                or row
            )
        elif row.get("status") != "activated" or not row.get("compiled"):
            raise ActivationRefused(
                self._preflight_result(
                    use_case_id,
                    [
                        (
                            "activated",
                            False,
                            f"Use case is {row.get('status')}; activate it before running.",
                        )
                    ],
                )
            )
        else:
            result = self.preflight(use_case_id, mode="live")
            if not result["allowed"]:
                raise ActivationRefused(result)
        return self.store.create_run(
            use_case_id=use_case_id,
            use_case_version=int(row.get("version") or 0),
            trigger=trigger,
            mode=mode,
            request=dict(request) if request else None,
        )

    async def astart_run(
        self,
        use_case_id: str,
        *,
        mode: str = "live",
        trigger: str = "manual",
        request: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            lambda: self.start_run(
                use_case_id, mode=mode, trigger=trigger, request=request
            )
        )

    async def execute(self, run_id: str) -> Dict[str, Any]:
        """Run one queued run to completion and record the outcome. Never raises: a failure
        is a failed run row with the error in ``blocked_reason``."""
        run = await self.store.aget_run(run_id)
        if run is None:
            raise NotFound(run_id)
        row = await self.store.aget(str(run["use_case_id"]))
        if row is None:
            raise NotFound(run["use_case_id"])
        mode = str(run.get("mode") or "live")
        await self.store.aupdate_run(run_id, status="running")
        try:
            compiled = row.get("compiled") or {}
            template_id = (compiled.get("parameters") or {}).get(
                "extraction template"
            ) or "purchase_order"
            workflow = workflow_from_compiled(
                compiled,
                executors=self.executors,
                # The extraction role reads its template; the explanation role reads
                # the use case's (the diagnostic kind has its own prompt).
                agent_factory=lambda role, agent_id: self._agent_for(
                    role,
                    agent_id,
                    compiled.get("template") if role == "explanation" else template_id,
                ),
            )
            options = self.build_run_options(
                row, run_id=run_id, mode=mode, request=run.get("request")
            )
            framework_run_id = str(uuid.uuid4())
            output = await workflow.arun(
                input=f"{row['id']} {mode}",
                run_id=framework_run_id,
                additional_data=options,
            )
            result = _result_of(output)
            if result is not None and options.get("rehearsal_source"):
                result["rehearsal_source"] = options["rehearsal_source"]
            provenance_ref = _provenance_of(output)
            summary = _summary_of(result)
            if result is not None and options.get("rehearsal_case_ids"):
                result["cases"] = await self._score_cases(
                    run_id, options["rehearsal_case_ids"], result
                )
            updated = await self.store.aupdate_run(
                run_id,
                status="succeeded",
                finished_at=_now(),
                framework_run_id=framework_run_id,
                provenance_ref=provenance_ref,
                summary=summary,
                result=result,
            )
        except Exception as exc:  # the run row is the report; nothing propagates
            log.exception("use case run %s failed", run_id)
            updated = await self.store.aupdate_run(
                run_id,
                status="failed",
                finished_at=_now(),
                blocked_reason=f"{type(exc).__name__}: {exc}"[:500],
            )
        assert updated is not None
        return updated

    async def _score_cases(
        self, run_id: str, case_ids: Sequence[str], result: Mapping[str, Any]
    ) -> List[Dict[str, Any]]:
        """Score every case the run carried and record the verdict on the case row."""
        by_message: Dict[str, Mapping[str, Any]] = {}
        for document in result.get("documents") or []:
            by_message[str((document.get("message") or {}).get("id"))] = document
        exceptions: Dict[str, Mapping[str, Any]] = {}
        for row in result.get("exceptions") or []:
            exceptions[str(row.get("message_id"))] = row
        verdicts: List[Dict[str, Any]] = []
        for case_id in case_ids:
            case = await self.store.aget_case(case_id)
            if case is None:
                continue
            message_id = f"<{case_id}@cases>"
            document = by_message.get(message_id)
            verdict = score_case(
                case, document=document, exception=exceptions.get(message_id)
            )
            # What the run observed, so "accept this output as expected" has it to copy.
            extracted = (document or {}).get("extracted") or {}
            verdict["observed"] = {
                "outcome": verdict["outcome"],
                "extracted": {
                    k: v for k, v in extracted.items() if k != "lines" and v is not None
                },
                "line_count": len(extracted.get("lines") or []),
            }
            await self.store.aupdate_case(
                case_id,
                last_run_id=run_id,
                last_status=verdict["status"],
                last_result=verdict,
            )
            verdicts.append({"case_id": case_id, "label": case.get("label"), **verdict})
        return verdicts

    def accept_case_output(self, case_id: str) -> Dict[str, Any]:
        """Turn the case's last observed output into what it expects from now on."""
        case = self.store.get_case(case_id)
        if case is None:
            raise NotFound(case_id)
        observed = (case.get("last_result") or {}).get("observed")
        if not observed:
            raise ActivationRefused(
                self._preflight_result(
                    str(case["use_case_id"]),
                    [
                        (
                            "case_run",
                            False,
                            "Run the case first; there is no output to accept yet.",
                        )
                    ],
                )
            )
        expected = {
            "outcome": observed.get("outcome"),
            "extracted": dict(observed.get("extracted") or {}),
            "line_count": observed.get("line_count"),
        }
        updated = self.store.update_case(
            case_id,
            expected=expected,
            last_status="pass",
            last_result={
                **(case.get("last_result") or {}),
                "status": "pass",
                "expected_outcome": observed.get("outcome"),
                "checks": [],
            },
        )
        assert updated is not None
        return updated

    async def aaccept_case_output(self, case_id: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self.accept_case_output, case_id)

    def spec_changed_materially(
        self, before: Optional[Mapping[str, Any]], after: Mapping[str, Any]
    ) -> bool:
        """Whether a saved spec changes what a golden case would do: the template, the
        guardrails, the mapping, the thresholds."""
        keys = (
            "extraction template",
            "guardrails",
            "target objects",
            "match thresholds",
            "created as",
        )
        old_params = (before or {}).get("parameters") or {}
        new_params = after.get("parameters") or {}
        return any(old_params.get(k) != new_params.get(k) for k in keys)

    def golden_summary(self, use_case_id: str) -> Dict[str, Any]:
        """Pass rate over the cases that carry expected values: what preflight shows."""
        cases = self.store.list_cases(use_case_id)
        expected = [c for c in cases if c.get("expected")]
        passed = [c for c in expected if c.get("last_status") == "pass"]
        failed = [c for c in expected if c.get("last_status") == "fail"]
        untested = [c for c in expected if c.get("last_status") not in {"pass", "fail"}]
        return {
            "cases": len(cases),
            "with_expected": len(expected),
            "passed": len(passed),
            "failed": len(failed),
            "untested": len(untested),
            "failing": [
                {
                    "id": c["id"],
                    "label": c.get("label"),
                    "detail": (c.get("last_result") or {}).get("checks"),
                }
                for c in failed
            ],
        }

    async def agolden_summary(self, use_case_id: str) -> Dict[str, Any]:
        return await asyncio.to_thread(self.golden_summary, use_case_id)

    def launch(self, run_id: str) -> asyncio.Task:
        """Execute in the background on the running loop; the task is kept so a caller (a
        test, a shutdown) can await it."""
        task = asyncio.create_task(self.execute(run_id))
        self._tasks[run_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(run_id, None))
        return task

    async def await_runs(self) -> None:
        pending = list(self._tasks.values())
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def _is_curated(row: Mapping[str, Any]) -> bool:
    """Whether the use case runs the shipped purchase-order template, whose generated set is
    the curated sixteen with a known expected extraction."""
    compiled = row.get("compiled") or {}
    parameters = (
        compiled.get("parameters") or (row.get("spec") or {}).get("parameters") or {}
    )
    value = parameters.get("extraction template") or "purchase_order"
    try:
        return template_from(value).id == "purchase_order"
    except (KeyError, ValueError):
        return False


def template_fingerprint(template: Any) -> str:
    """``id|header names|line names`` -- what a generated case set is authored against."""
    header = ",".join(name for name, _ in getattr(template, "header", ()) or ())
    lines = ",".join(name for name, _ in getattr(template, "lines", ()) or ())
    return f"{template.id}|{header}|{lines}"


def _is_shipped_purchase_order(template: Any) -> bool:
    """The curated sixteen are authored against the shipped purchase-order template's own
    field names; a drafted template that happens to carry the same id is not it."""
    if template.id != "purchase_order":
        return False
    try:
        shipped = template_from("purchase_order")
    except (KeyError, ValueError):
        return False
    return template_fingerprint(template) == template_fingerprint(shipped)


def _result_of(output: Any) -> Optional[Dict[str, Any]]:
    """The batch the last document-carrying executor returned, wherever it sits: the Notify
    step nests it under ``batch`` and ``record_provenance`` forwards that under
    ``explanation``, so the step results are walked from the end for the first content that
    carries ``documents`` -- directly, or one level down."""

    def batch_in(content: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(content, Mapping):
            return None
        if "documents" in content:
            return dict(content)
        for key in ("batch", "explanation"):
            nested = batch_in(content.get(key))
            if nested is not None:
                # Carry the enclosing step's own verdict (notify status, recipients).
                for extra in ("status", "to", "subject"):
                    if extra in content and extra not in nested:
                        nested[extra] = content[extra]
                return nested
        return None

    candidates = [getattr(output, "content", None)] + [
        getattr(step, "content", None)
        for step in reversed(list(getattr(output, "step_results", []) or []))
    ]
    for candidate in candidates:
        batch = batch_in(candidate)
        if batch is not None:
            batch["documents"] = [
                _light_document(d) for d in batch.get("documents") or []
            ]
            return batch
    content = getattr(output, "content", None)
    return dict(content) if isinstance(content, Mapping) else None


def _light_document(document: Mapping[str, Any]) -> Dict[str, Any]:
    """A document without the page text and attachment bytes: what the interface renders."""
    out = dict(document)
    doc = out.get("document")
    if isinstance(doc, Mapping):
        out["document"] = {k: v for k, v in doc.items() if k != "text"}
    message = out.get("message")
    if isinstance(message, Mapping):
        out["message"] = {k: v for k, v in message.items() if k not in {"body_text"}}
    return out


def _provenance_of(output: Any) -> Optional[str]:
    for step in reversed(list(getattr(output, "step_results", []) or [])):
        content = getattr(step, "content", None)
        if isinstance(content, Mapping) and content.get("provenance_ref"):
            return str(content["provenance_ref"])
    return None


def _summary_of(result: Optional[Mapping[str, Any]]) -> str:
    if not result:
        return ""
    if isinstance(result.get("report"), Mapping):
        comparison = result.get("comparison_result") or {}
        kpis = comparison.get("kpis") or []
        delivery = result.get("delivery") or {}
        status = str(delivery.get("status") or "in-app")
        grounded = (result.get("grounding") or {}).get("passed")
        if isinstance(result.get("risk"), Mapping):
            summary = (result.get("risk") or {}).get("summary") or {}
            recs = (result.get("recommendation_result") or {}).get("summary") or {}
            alerts = result.get("alerts") or {}
            what = (
                f"{summary.get('open_quotes', 0)} open quotes scored: "
                f"{summary.get('stalled', 0)} already stalled, "
                f"{summary.get('within_range', 0)} in their window of which "
                f"{summary.get('at_risk_within_range', 0)} at risk; "
                f"{recs.get('recommendations', 0)} recommendation(s), "
                f"{recs.get('alerts', 0)} of them alerted to reps "
                f"({alerts.get('status') or 'composed'})"
            )
        elif isinstance(result.get("diagnosis"), Mapping):
            what = "1 KPI diagnosed"
        elif comparison.get("comparison") and comparison.get("comparison") != "none":
            what = f"{len(kpis)} KPI(s) compared"
        else:
            what = f"{len(kpis)} KPI(s) read"
        return f"{what}, report {status}" + (
            ", narrative withheld (grounding)" if grounded is False else ""
        )
    documents = result.get("documents") or []
    exceptions = result.get("exceptions") or []
    written = sum(
        1 for d in documents if (d.get("written") or {}).get("status") == "written"
    )
    previewed = sum(
        1 for d in documents if (d.get("written") or {}).get("status") == "previewed"
    )
    parts = []
    if written:
        parts.append(f"{written} created")
    if previewed:
        parts.append(f"{previewed} previewed")
    parts.append(f"{len(exceptions)} exception(s)")
    received = (result.get("stats") or {}).get("received")
    if received is not None:
        parts.insert(0, f"{received} received")
    return ", ".join(parts)
