"""``/use-cases`` -- the catalog, the draft, the spec, preflight, activation and runs (K16).

The first real router for the operations ``paths/use-cases.yaml`` has described since 1.0.0
and the stub has served from examples. Same conventions as ``dashboards.py``: no prefix, one
scope dependency per route, string ``detail`` on refusals, and the generated wire models on
every boundary. The work is in ``egpt_retail.usecases.service``; this module maps HTTP to it.

Two things are deliberately not here. ``POST /use-cases/{id}/spec`` (the extraction agent)
and ``GET /use-cases/{id}/gaps`` (the semantic gap check) stay with the stub until their
owners wire them: the document-driven flow the interface runs today fills the spec on the
form and stores it with ``PUT`` (1.3.0). And nothing schedules: a run starts when this
router's ``POST .../runs`` is called -- by the interface's Run button, by the in-process
poller when it is switched on, or in the cloud by the orchestrator the deployment uses.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from egpt.os.scopes import has_required_scopes

from egpt_retail.api import models as wire
from egpt_retail.app.db import pooled_control_plane_engine
from egpt_retail.app.scopes import caller_scopes, require_scopes
from egpt_retail.app.settings import RetailSettings
from egpt_retail.executors.plan import UnknownPlanRef, plan_from_config
from egpt_retail.usecases.credentials import CredentialUnavailable
from egpt_retail.usecases.ddl import apply_use_case_ddl
from egpt_retail.usecases.service import ActivationRefused, NotFound, UseCaseService
from egpt_retail.usecases.store import UseCaseStore

__all__ = ["router", "use_case_service"]

router = APIRouter()
log = logging.getLogger(__name__)

#: Activating at this level or above additionally needs ``use-cases:activate`` (G11).
ACTIVATE_ADMIN_LEVEL = 3


def _executor_map() -> Dict[str, Any]:
    from egpt_retail.app.components import build_registry

    return {f.__name__: f for f in build_registry().functions}


@lru_cache(maxsize=None)
def _service_for(db_url: str) -> UseCaseService:
    settings = RetailSettings()
    engine = pooled_control_plane_engine(settings)
    apply_use_case_ddl(engine)
    store = UseCaseStore(engine)
    stale = store.fail_stale_runs()
    if stale:
        log.warning(
            "use cases: %d run(s) left open by a previous process marked failed", stale
        )
    return UseCaseService(store, executors=_executor_map(), settings=settings)


def use_case_service() -> UseCaseService:
    """One service per process: it holds the running tasks. Overridden in tests."""
    return _service_for(RetailSettings().retail_db_url)


def _not_found(use_case_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"No use case {use_case_id!r}.")


def _may_activate_level(request: Request, level: Optional[int]) -> bool:
    if level is None or level < ACTIVATE_ADMIN_LEVEL:
        return True
    if not getattr(request.state, "authorization_enabled", False):
        return True
    user_scopes, admin_scope = caller_scopes(request)
    return has_required_scopes(
        user_scopes, ["use-cases:activate"], admin_scope=admin_scope
    )


def _use_case_out(service: UseCaseService, row: Dict[str, Any]) -> wire.UseCase:
    return wire.UseCase.model_validate(service.catalog_view(row))


def _run_out(row: Dict[str, Any]) -> wire.UseCaseRun:
    return wire.UseCaseRun.model_validate(
        {
            "id": row["id"],
            "use_case_id": row["use_case_id"],
            "use_case_version": row.get("use_case_version") or 0,
            "status": row.get("status") or "queued",
            "trigger": row.get("trigger") or "manual",
            "started_at": row["started_at"],
            "finished_at": row.get("finished_at"),
            "framework_run_id": row.get("framework_run_id"),
            "output_ref": row.get("output_ref"),
            "provenance_ref": row.get("provenance_ref"),
            "summary": row.get("summary") or "",
            "blocked_reason": row.get("blocked_reason"),
            "cost": None,
            "mode": row.get("mode") or "live",
            "result": row.get("result"),
        }
    )


def _preflight_response(result: Dict[str, Any], status_code: int) -> JSONResponse:
    body = wire.PreflightResult.model_validate(result).model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=body)


# --- catalog --------------------------------------------------------------------------------


@router.get("/use-cases", response_model=List[wire.UseCase])
async def list_use_cases(
    status: Optional[wire.UseCaseStatus] = None,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:read")),
) -> List[wire.UseCase]:
    rows = await service.store.alist(status=status.value if status else None)
    return [_use_case_out(service, row) for row in rows]


@router.post("/use-cases", response_model=wire.UseCase, status_code=201)
async def create_use_case(
    body: wire.UseCaseWrite,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> wire.UseCase:
    row = await service.store.acreate(
        title=body.title,
        description=body.description,
        business_area=body.business_area,
        autonomy=body.autonomy.value if body.autonomy else "copilot",
        level=body.level or 1,
        tags=list(body.tags or []),
    )
    return _use_case_out(service, row)


@router.get("/use-cases/{use_case_id}", response_model=wire.UseCase)
async def get_use_case(
    use_case_id: str,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:read")),
) -> wire.UseCase:
    row = await service.store.aget(use_case_id)
    if row is None:
        raise _not_found(use_case_id)
    return _use_case_out(service, row)


@router.patch("/use-cases/{use_case_id}", response_model=wire.UseCase)
async def update_use_case(
    use_case_id: str,
    body: wire.UseCaseWrite,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> wire.UseCase:
    fields: Dict[str, Any] = {
        "title": body.title,
        "description": body.description,
        "business_area": body.business_area,
    }
    if body.autonomy is not None:
        fields["autonomy"] = body.autonomy.value
    if body.level is not None:
        fields["level"] = body.level
    if body.tags is not None:
        fields["tags"] = list(body.tags)
    row = await service.store.aupdate(use_case_id, **fields)
    if row is None:
        raise _not_found(use_case_id)
    return _use_case_out(service, row)


@router.delete("/use-cases/{use_case_id}", status_code=204)
async def delete_use_case(
    use_case_id: str,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> None:
    if not await service.store.adelete(use_case_id):
        raise _not_found(use_case_id)
    return None


# --- draft and spec -------------------------------------------------------------------------


@router.get("/use-cases/{use_case_id}/draft", response_model=wire.UseCaseDraft)
async def get_use_case_draft(
    use_case_id: str,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:read")),
) -> wire.UseCaseDraft:
    row = await service.store.aget(use_case_id)
    if row is None:
        raise _not_found(use_case_id)
    if not row.get("draft"):
        raise HTTPException(
            status_code=404, detail=f"No draft saved for {use_case_id!r} yet."
        )
    return wire.UseCaseDraft.model_validate(row["draft"])


@router.put("/use-cases/{use_case_id}/draft", response_model=wire.UseCaseDraft)
async def save_use_case_draft(
    use_case_id: str,
    body: wire.UseCaseDraft,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> wire.UseCaseDraft:
    row = await service.store.aupdate(
        use_case_id, draft=body.model_dump(mode="json", exclude_none=True)
    )
    if row is None:
        raise _not_found(use_case_id)
    return wire.UseCaseDraft.model_validate(row["draft"])


@router.get("/use-cases/{use_case_id}/spec", response_model=wire.Spec)
async def get_use_case_spec(
    use_case_id: str,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:read")),
) -> wire.Spec:
    row = await service.store.aget(use_case_id)
    if row is None:
        raise _not_found(use_case_id)
    if not row.get("spec"):
        raise HTTPException(
            status_code=404,
            detail=f"No spec stored for {use_case_id!r} yet. PUT one from the form.",
        )
    return wire.Spec.model_validate(row["spec"])


@router.put("/use-cases/{use_case_id}/spec", response_model=wire.Spec)
async def save_use_case_spec(
    use_case_id: str,
    body: wire.Spec,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> wire.Spec:
    spec = body.model_dump(mode="json", exclude_none=True)
    spec["use_case_id"] = use_case_id
    before = await service.store.aget(use_case_id)
    if before is None:
        raise _not_found(use_case_id)
    # A saved spec is the spec: the compiled snapshot a rehearsal left on a draft is of
    # the previous one and would keep answering the probes and the mailbox listing.
    # An activated use case keeps its activated snapshot until it is activated again.
    if before.get("status") == "activated":
        row = await service.store.aupdate(use_case_id, spec=spec)
    else:
        row = await service.store.aupdate(use_case_id, spec=spec, compiled=None)
    assert row is not None
    # Regression (K19): a change to the template, the guardrails, the mapping or the
    # thresholds re-runs every golden case in the background, so the preflight's
    # golden_cases check reflects this spec, not the last one.
    if service.spec_changed_materially(before.get("spec"), spec):
        cases = await service.store.alist_cases(use_case_id)
        if any(c.get("expected") for c in cases):
            try:
                run = await service.astart_run(
                    use_case_id,
                    mode="rehearse",
                    trigger="regression",
                    request={"source": "cases"},
                )
                service.launch(str(run["id"]))
            except ActivationRefused:
                pass  # the spec does not compile yet; preflight says so
    return wire.Spec.model_validate(row["spec"])


# --- plan, preflight, activation ------------------------------------------------------------


@router.get("/use-cases/{use_case_id}/plan", response_model=wire.Plan)
async def get_use_case_plan(
    use_case_id: str,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:read")),
) -> wire.Plan:
    row = await service.store.aget(use_case_id)
    if row is None:
        raise _not_found(use_case_id)
    compiled = row.get("compiled")
    if not compiled:
        try:
            from egpt_retail.usecases.service import compiled_to_dict

            compiled = compiled_to_dict(service.compile(row))
        except ActivationRefused as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    agent_ids = [m.get("agent_id") for m in compiled.get("model_steps") or []]
    try:
        plan = plan_from_config(
            compiled.get("config") or {},
            use_case_id=use_case_id,
            version=int(row.get("version") or 0),
            workflow_component_id=row.get("workflow_component_id"),
            executor_names=set(service.executors),
            agent_ids=[a for a in agent_ids if a],
        )
    except UnknownPlanRef as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return wire.Plan.model_validate(plan)


@router.post("/use-cases/{use_case_id}/preflight", response_model=wire.PreflightResult)
async def run_use_case_preflight(
    use_case_id: str,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> wire.PreflightResult:
    try:
        result = await service.apreflight(use_case_id)
    except NotFound:
        raise _not_found(use_case_id)
    return wire.PreflightResult.model_validate(result)


@router.post(
    "/use-cases/{use_case_id}/preflight/{probe}", response_model=wire.ProbeResult
)
async def run_use_case_preflight_probe(
    use_case_id: str,
    probe: wire.ProbeName,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> wire.ProbeResult:
    try:
        result = await service.aprobe(use_case_id, probe.value)
    except NotFound:
        raise _not_found(use_case_id)
    return wire.ProbeResult.model_validate(result)


@router.post(
    "/use-cases/{use_case_id}/activate",
    response_model=wire.UseCase,
    responses={409: {"model": wire.PreflightResult}},
)
async def activate_use_case(
    use_case_id: str,
    request: Request,
    body: Optional[wire.ActivationRequest] = None,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> Any:
    level = body.level if body else None
    if not _may_activate_level(request, level):
        raise HTTPException(
            status_code=403,
            detail=f"Activating at level {level} requires the admin role (scope use-cases:activate).",
        )
    try:
        row = await service.aactivate(
            use_case_id,
            level=level,
            autonomy=body.autonomy.value if body and body.autonomy else None,
            schedule=body.schedule.value if body and body.schedule else None,
        )
    except NotFound:
        raise _not_found(use_case_id)
    except ActivationRefused as exc:
        return _preflight_response(exc.preflight, 409)
    return _use_case_out(service, row)


@router.post("/use-cases/{use_case_id}/pause", response_model=wire.UseCase)
async def pause_use_case(
    use_case_id: str,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> wire.UseCase:
    try:
        row = await service.apause(use_case_id)
    except NotFound:
        raise _not_found(use_case_id)
    return _use_case_out(service, row)


# --- runs -----------------------------------------------------------------------------------


@router.get("/use-cases/{use_case_id}/runs", response_model=List[wire.UseCaseRun])
async def list_use_case_runs(
    use_case_id: str,
    status: Optional[wire.RunStatus] = None,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:read")),
) -> List[wire.UseCaseRun]:
    if await service.store.aget(use_case_id) is None:
        raise _not_found(use_case_id)
    rows = await service.store.alist_runs(
        use_case_id, status=status.value if status else None
    )
    return [_run_out(row) for row in rows]


@router.post(
    "/use-cases/{use_case_id}/runs",
    response_model=wire.UseCaseRun,
    status_code=202,
    responses={409: {"model": wire.PreflightResult}},
)
async def trigger_use_case_run(
    use_case_id: str,
    mode: wire.RunMode = wire.RunMode.live,
    body: Optional[wire.RunRequest] = None,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> Any:
    request = body.model_dump(mode="json", exclude_none=True) if body else None
    if request and request.get("source") is not None:
        request["source"] = wire.RehearsalSource(request["source"]).value
    try:
        run = await service.astart_run(
            use_case_id,
            mode=mode.value,
            trigger="manual",
            request=request if mode is wire.RunMode.rehearse else None,
        )
    except NotFound:
        raise _not_found(use_case_id)
    except ActivationRefused as exc:
        return _preflight_response(exc.preflight, 409)
    service.launch(str(run["id"]))
    return _run_out(run)


@router.get("/use-cases/{use_case_id}/runs/{run_id}", response_model=wire.UseCaseRun)
async def get_use_case_run(
    use_case_id: str,
    run_id: str,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:read")),
) -> wire.UseCaseRun:
    row = await service.store.aget_run(run_id)
    if row is None or row.get("use_case_id") != use_case_id:
        raise HTTPException(
            status_code=404, detail=f"No run {run_id!r} for use case {use_case_id!r}."
        )
    return _run_out(row)


# --- rehearsal sources and golden cases (K19) -------------------------------------------------


def _case_out(row: Dict[str, Any]) -> wire.GoldenCase:
    return wire.GoldenCase.model_validate(
        {k: v for k, v in row.items() if k != "document_b64"}
    )


@router.get(
    "/use-cases/{use_case_id}/mailbox/messages",
    response_model=List[wire.MailboxMessage],
)
async def list_mailbox_messages(
    use_case_id: str,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:read")),
) -> List[wire.MailboxMessage]:
    try:
        rows = await service.alist_mailbox_messages(use_case_id)
    except NotFound:
        raise _not_found(use_case_id)
    except CredentialUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # the mailbox itself refusing: unreachable, wrong password
        raise HTTPException(
            status_code=503, detail=f"The mailbox could not be read: {exc}"[:300]
        )
    return [wire.MailboxMessage.model_validate(r) for r in rows]


@router.get("/use-cases/{use_case_id}/cases", response_model=List[wire.GoldenCase])
async def list_golden_cases(
    use_case_id: str,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:read")),
) -> List[wire.GoldenCase]:
    if await service.store.aget(use_case_id) is None:
        raise _not_found(use_case_id)
    return [_case_out(c) for c in await service.store.alist_cases(use_case_id)]


@router.post(
    "/use-cases/{use_case_id}/cases", response_model=wire.GoldenCase, status_code=201
)
async def create_golden_case(
    use_case_id: str,
    body: wire.GoldenCaseWrite,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> wire.GoldenCase:
    if await service.store.aget(use_case_id) is None:
        raise _not_found(use_case_id)
    import base64

    from egpt_retail.documents.reader import PDF_MAGIC

    try:
        data = base64.b64decode(body.document_b64)
    except Exception:
        raise HTTPException(status_code=422, detail="document_b64 is not valid base64")
    if not data.startswith(PDF_MAGIC):
        raise HTTPException(
            status_code=422,
            detail=f"{body.filename or 'document.pdf'}: not a PDF (no %PDF header)",
        )
    case = await service.store.acreate_case(
        use_case_id=use_case_id,
        label=body.label,
        document_b64=body.document_b64,
        sender=body.sender or "",
        subject=body.subject or body.label,
        filename=body.filename or "document.pdf",
        source="uploaded",
        expected=body.expected.model_dump(mode="json", exclude_none=True)
        if body.expected
        else None,
    )
    return _case_out(case)


@router.patch(
    "/use-cases/{use_case_id}/cases/{case_id}", response_model=wire.GoldenCase
)
async def update_golden_case(
    use_case_id: str,
    case_id: str,
    body: wire.GoldenCaseUpdate,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> wire.GoldenCase:
    case = await service.store.aget_case(case_id)
    if case is None or case.get("use_case_id") != use_case_id:
        raise HTTPException(
            status_code=404, detail=f"No case {case_id!r} for use case {use_case_id!r}."
        )
    fields: Dict[str, Any] = {}
    for key in ("label", "sender", "subject"):
        value = getattr(body, key)
        if value is not None:
            fields[key] = value
    if "expected" in body.model_fields_set:
        fields["expected"] = (
            body.expected.model_dump(mode="json", exclude_none=True)
            if body.expected
            else None
        )
        # A changed expectation is untested until the next run says otherwise.
        fields["last_status"] = "untested"
    updated = await service.store.aupdate_case(case_id, **fields)
    assert updated is not None
    return _case_out(updated)


@router.delete("/use-cases/{use_case_id}/cases/{case_id}", status_code=204)
async def delete_golden_case(
    use_case_id: str,
    case_id: str,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> None:
    case = await service.store.aget_case(case_id)
    if case is None or case.get("use_case_id") != use_case_id:
        raise HTTPException(
            status_code=404, detail=f"No case {case_id!r} for use case {use_case_id!r}."
        )
    await service.store.adelete_case(case_id)
    return None


@router.post(
    "/use-cases/{use_case_id}/cases/{case_id}/accept",
    response_model=wire.GoldenCase,
    responses={409: {"model": wire.PreflightResult}},
)
async def accept_golden_case_output(
    use_case_id: str,
    case_id: str,
    service: UseCaseService = Depends(use_case_service),
    _: None = Depends(require_scopes("use-cases:write")),
) -> Any:
    case = await service.store.aget_case(case_id)
    if case is None or case.get("use_case_id") != use_case_id:
        raise HTTPException(
            status_code=404, detail=f"No case {case_id!r} for use case {use_case_id!r}."
        )
    try:
        updated = await service.aaccept_case_output(case_id)
    except ActivationRefused as exc:
        return _preflight_response(exc.preflight, 409)
    return _case_out(updated)
