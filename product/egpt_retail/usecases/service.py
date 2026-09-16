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
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.use_case import UseCaseTemplate
from egpt_retail.orchestration.compiler import CompiledWorkflow, compile_workflow
from egpt_retail.documents.golden import (
    GENERATED_CASE_SOURCE,
    case_to_message,
    generate_cases,
    score_case,
)
from egpt_retail.documents.guardrails import rules_from
from egpt_retail.documents.mailbox import fetch_messages, filter_from, mailbox_from
from egpt_retail.documents.samples import (
    expected_extraction,
    expected_outcome,
    sample_master_data,
    sample_messages,
)
from egpt_retail.documents.templates import template_from
from egpt_retail.usecases.credentials import CredentialUnavailable, resolve_credentials
from egpt_retail.usecases.runtime import (
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
        rows = [{"name": n, "passed": bool(p), "reason": r} for n, p, r in checks]
        return {
            "use_case_id": use_case_id,
            "allowed": all(c["passed"] for c in rows),
            "checks": rows,
            "blocked_by": [c["name"] for c in rows if not c["passed"]],
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
                    )
                )
        return self._preflight_result(use_case_id, checks)

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
        updated = self.store.update(
            use_case_id,
            status="activated",
            version=version,
            compiled=compiled_to_dict(compiled),
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

        settings = self.settings or RetailSettings()
        model = AzureOpenAI(id=settings.retail_azure_deployment)
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
        compiled = row.get("compiled") or {}
        parameters = (
            compiled.get("parameters")
            or (row.get("spec") or {}).get("parameters")
            or {}
        )
        template = template_from(
            parameters.get("extraction template") or "purchase_order"
        )
        if template.id == "purchase_order":
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
        return generate_cases(
            template, master, rules_from(parameters.get("guardrails") or "all")
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
        compiled = row.get("compiled") or {}
        parameters = (
            compiled.get("parameters")
            or (row.get("spec") or {}).get("parameters")
            or {}
        )
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
                agent_factory=lambda role, agent_id: self._agent_for(
                    role, agent_id, template_id
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
