"""R11's real router: saved prompts, chat filters, answer-format preferences, pinned
reports, and the citation panel (``GET /runs/{run_id}/provenance``).

The first real (non-stub) FastAPI router in this product -- everything the stub
(``api/stub/server.py``) serves for these paths today is a canned example. Mounted onto
``base_app`` in ``app/factory.py``, ahead of ``EgptOS`` construction, the same spot
``FixedTenantMiddleware`` is added and for the same reason: it needs to exist on the app
``AuthMiddleware`` wraps.

Wire conversion uses the generated Pydantic models under the ``wire`` alias
(``api/models/__init__.py``) throughout, to keep the name ``SavedPrompt`` -- and several
others -- from colliding with its identically-named domain dataclass in
``contracts.playground``.
"""

from __future__ import annotations

from typing import Iterator

from egpt.answer_provenance import aread_provenance
from fastapi import APIRouter, Depends, HTTPException, Request

from egpt_retail.api import models as wire
from egpt_retail.app.db import build_control_plane_engine, pooled_control_plane_connection
from egpt_retail.app.scopes import require_scopes
from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.playground import (
    ChatFilters,
    ChatMode,
    Period,
    PlaygroundPreferences,
    Report,
    ReportItem,
    SavedPrompt,
)
from egpt_retail.playground.store import PlaygroundStore

__all__ = ["router"]

router = APIRouter()


# --- store dependency --------------------------------------------------------------------


def _playground_store() -> Iterator[PlaygroundStore]:
    """One connection per request, acquired from the process-wide pool
    (``app/db.py::pooled_control_plane_connection``) and returned on close, not a fresh
    ``psycopg.connect()`` per call. Schema provisioning happens once at startup
    (``app/factory.py``'s lifespan), not on every call either."""
    settings = RetailSettings()
    connection = pooled_control_plane_connection(settings)
    try:
        yield PlaygroundStore(connection)
    finally:
        connection.close()


def _owner(request: Request) -> str:
    """The caller's identity, scoping every playground resource -- this deployment is
    single-tenant (D3), so ownership is per-user, not per-tenant. With authorization off,
    ``"local"`` stands in for the one local caller. With authorization on, ``user_id`` must
    be present -- ``AuthMiddleware`` sets it to ``None`` *explicitly* (not merely omits it)
    for a token that decodes and passes scope checks but carries no ``sub`` claim, and
    collapsing that onto a shared ``"local"`` would let every such caller read and mutate
    every other's saved prompts, filters and reports. Refuse instead: there is no other
    identity in the token this router can safely key ownership on.
    """
    if not getattr(request.state, "authorization_enabled", False):
        return "local"
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail=(
                "This token carries no subject claim; the playground is scoped to a "
                "caller identity and cannot be owned by an anonymous one."
            ),
        )
    return user_id


# --- wire conversions --------------------------------------------------------------------


def _prompt_out(prompt: SavedPrompt) -> wire.SavedPrompt:
    return wire.SavedPrompt(
        id=prompt.id,
        text=prompt.text,
        label=prompt.label,
        saved_at=prompt.saved_at,
        last_used_at=prompt.last_used_at,
        usage_count=prompt.usage_count,
    )


def _filters_out(filters: ChatFilters) -> wire.ChatFilters:
    return wire.ChatFilters(
        period=wire.Period(filters.period.value),
        from_date=filters.from_date,
        to_date=filters.to_date,
    )


def _preferences_out(preferences: PlaygroundPreferences) -> wire.PlaygroundPreferences:
    return wire.PlaygroundPreferences(mode=wire.ChatMode(preferences.mode.value))


def _item_out(item: ReportItem) -> wire.ReportItem:
    return wire.ReportItem(
        id=item.id,
        block=wire.AnswerBlock(**item.block),
        source_question=item.source_question,
        run_id=item.run_id,
        use_case_id=item.use_case_id,
        pinned_at=item.pinned_at,
    )


def _report_out(report: Report) -> wire.Report:
    return wire.Report(
        id=report.id,
        title=report.title,
        items=[_item_out(item) for item in report.items],
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


# --- saved prompts -------------------------------------------------------------------------


@router.get("/playground/prompts", response_model=list[wire.SavedPrompt])
def list_saved_prompts(
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("playground:write")),
):
    return [_prompt_out(p) for p in store.list_prompts(_owner(request))]


@router.post("/playground/prompts", response_model=wire.SavedPrompt, status_code=201)
def create_saved_prompt(
    body: wire.SavedPromptWrite,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("playground:write")),
):
    prompt = store.save_prompt(_owner(request), body.text, body.label or "")
    return _prompt_out(prompt)


@router.patch("/playground/prompts/{prompt_id}", response_model=wire.SavedPrompt)
def update_saved_prompt(
    prompt_id: str,
    body: wire.SavedPromptWrite,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("playground:write")),
):
    prompt = store.update_prompt(
        _owner(request), prompt_id, text=body.text, label=body.label or ""
    )
    if prompt is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return _prompt_out(prompt)


@router.delete("/playground/prompts/{prompt_id}", status_code=204)
def delete_saved_prompt(
    prompt_id: str,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("playground:write")),
):
    if not store.delete_prompt(_owner(request), prompt_id):
        raise HTTPException(status_code=404, detail="Not found.")


@router.post("/playground/prompts/{prompt_id}/used", response_model=wire.SavedPrompt)
def mark_saved_prompt_used(
    prompt_id: str,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("playground:write")),
):
    prompt = store.mark_prompt_used(_owner(request), prompt_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return _prompt_out(prompt)


# --- chat filters ----------------------------------------------------------------------------


@router.get("/playground/filters", response_model=wire.ChatFilters)
def get_chat_filters(
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("playground:write")),
):
    return _filters_out(store.get_filters(_owner(request)))


@router.put("/playground/filters", response_model=wire.ChatFilters)
def set_chat_filters(
    body: wire.ChatFilters,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("playground:write")),
):
    try:
        filters = ChatFilters(
            owner=_owner(request),
            period=Period(body.period.value),
            from_date=body.from_date,
            to_date=body.to_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _filters_out(store.set_filters(filters))


# --- preferences -----------------------------------------------------------------------------


@router.get("/playground/preferences", response_model=wire.PlaygroundPreferences)
def get_playground_preferences(
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("playground:write")),
):
    return _preferences_out(store.get_preferences(_owner(request)))


@router.put("/playground/preferences", response_model=wire.PlaygroundPreferences)
def set_playground_preferences(
    body: wire.PlaygroundPreferences,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("playground:write")),
):
    preferences = PlaygroundPreferences(
        owner=_owner(request), mode=ChatMode(body.mode.value)
    )
    return _preferences_out(store.set_preferences(preferences))


# --- reports ---------------------------------------------------------------------------------


@router.get("/reports", response_model=wire.ReportList)
def list_reports(
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("reports:write")),
):
    active_report_id, reports = store.list_reports(_owner(request))
    return wire.ReportList(
        active_report_id=active_report_id,
        reports=[_report_out(r) for r in reports],
    )


@router.post("/reports", response_model=wire.Report, status_code=201)
def create_report(
    body: wire.ReportWrite,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("reports:write")),
):
    report = store.create_report(
        _owner(request),
        body.title,
        make_active=body.make_active if body.make_active is not None else True,
    )
    return _report_out(report)


@router.put("/reports/active", response_model=wire.ReportList)
def set_active_report(
    body: wire.ActiveReportWrite,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("reports:write")),
):
    owner = _owner(request)
    if not store.set_active_report(owner, body.report_id):
        raise HTTPException(status_code=404, detail="Not found.")
    active_report_id, reports = store.list_reports(owner)
    return wire.ReportList(
        active_report_id=active_report_id,
        reports=[_report_out(r) for r in reports],
    )


@router.get("/reports/{report_id}", response_model=wire.Report)
def get_report(
    report_id: str,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("reports:write")),
):
    report = store.get_report(_owner(request), report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return _report_out(report)


@router.patch("/reports/{report_id}", response_model=wire.Report)
def rename_report(
    report_id: str,
    body: wire.ReportWrite,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("reports:write")),
):
    report = store.rename_report(_owner(request), report_id, body.title)
    if report is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return _report_out(report)


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(
    report_id: str,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("reports:write")),
):
    if not store.delete_report(_owner(request), report_id):
        raise HTTPException(status_code=404, detail="Not found.")


@router.post(
    "/reports/{report_id}/blocks", response_model=wire.ReportItem, status_code=201
)
def pin_block_to_report(
    report_id: str,
    body: wire.ReportBlockWrite,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("reports:write")),
):
    item = store.pin_block(
        _owner(request),
        report_id,
        block=body.block.model_dump(mode="json"),
        source_question=body.source_question or "",
        run_id=body.run_id,
        use_case_id=body.use_case_id,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return _item_out(item)


@router.delete("/reports/{report_id}/blocks/{block_id}", status_code=204)
def unpin_block_from_report(
    report_id: str,
    block_id: str,
    request: Request,
    store: PlaygroundStore = Depends(_playground_store),
    _: None = Depends(require_scopes("reports:write")),
):
    if not store.unpin_block(_owner(request), report_id, block_id):
        raise HTTPException(status_code=404, detail="Not found.")


# --- the citation panel: R7's provenance record, R11's router ------------------------------


@router.get("/runs/{run_id}/provenance", response_model=wire.Provenance)
async def get_run_provenance(
    run_id: str,
    _: None = Depends(require_scopes("sessions:read")),
):
    settings = RetailSettings()
    engine = build_control_plane_engine(settings)
    try:
        record = await aread_provenance(engine, run_id)
    finally:
        engine.dispose()
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No provenance record for {run_id}. Every answer writes one; a run "
                "without one is a defect, not a missing feature."
            ),
        )
    payload = record.to_dict()
    return wire.Provenance(**payload)
