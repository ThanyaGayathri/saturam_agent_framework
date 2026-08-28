import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Optional, Sequence, Union
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    WebSocket,
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from egpt.db.base import BaseDb
from egpt.exceptions import InputCheckError, OutputCheckError
from egpt.factory import FactoryContextRequired
from egpt.os.auth import (
    get_auth_token_from_request,
    get_authentication_dependency,
    require_resource_access,
)
from egpt.os.managers import event_buffer, websocket_manager
from egpt.os.middleware.user_scope import (
    SESSION_ID_REQUIRED,
    SESSION_ID_REQUIRED_RECONNECT,
    WORKFLOW_ID_REQUIRED_RECONNECT,
    assert_session_matches_component,
    get_scoped_user_id,
    run_matches_component,
    verify_run_in_session,
    verify_run_in_session_via_db,
)
from egpt.os.routers.workflows.schema import WorkflowResponse
from egpt.os.schema import (
    BadRequestResponse,
    InternalServerErrorResponse,
    NotFoundResponse,
    UnauthenticatedResponse,
    ValidationErrorResponse,
    WorkflowSummaryResponse,
)
from egpt.os.settings import EgptServingSettings
from egpt.os.tenancy import tenancy_enabled
from egpt.os.utils import (
    buffered_run_tenant_check,
    find_factory_by_id,
    format_sse_event,
    get_request_kwargs,
    get_workflow_by_id,
    get_workflow_by_id_async,
    register_or_refuse_run_tenant,
    resolve_tenant_id,
    resolve_workflow,
    tenant_for_direct_read,
    tenant_for_ws_hydration,
)
from egpt.registry.policy import PolicyViolationError
from egpt.run.base import RunStatus
from egpt.run.workflow import WorkflowErrorEvent
from egpt.utils.log import log_debug, log_warning, logger
from egpt.utils.serialize import json_serializer
from egpt.workflow.factory import WorkflowFactory
from egpt.workflow.remote import RemoteWorkflow
from egpt.workflow.workflow import Workflow

if TYPE_CHECKING:
    from egpt.os.app import EgptOS


def _match_workflow_in_memory(
    workflows: Optional[Sequence[Union[Workflow, RemoteWorkflow, WorkflowFactory]]], workflow_id: str
) -> Optional[Union[Workflow, RemoteWorkflow, WorkflowFactory]]:
    """The in-memory workflows-list entry matching workflow_id, or None (aicoe-tracker#174).

    One pass, shared by every WS hydration call site: whether it exists at all is `in_memory`
    for tenant_for_ws_hydration (code-defined components are exempt from the fail-closed gate),
    and whether the match is a WorkflowFactory decides the start-workflow dispatch branch.

    First match wins, deliberately. The `any()` scans this replaced looked at every entry, so
    with duplicate ids a plain workflow listed before a factory used to dispatch as the factory;
    now the earliest entry is the match. Duplicate ids are a misconfiguration, and the only
    consequence of the difference is which branch dispatches -- the non-factory branch, which
    is the tenant-gated one.
    """
    if not workflows:
        return None
    for workflow in workflows:
        if getattr(workflow, "id", None) == workflow_id:
            return workflow
    return None


async def handle_workflow_via_websocket(
    websocket: WebSocket,
    message: dict,
    os: "EgptOS",
    ws_user_context: Optional[Dict[str, Any]] = None,
    ws_auth: Optional["WebSocketAuthContext"] = None,
):
    """Handle workflow execution directly via WebSocket.

    `ws_user_context` carries the verified identity (user_id, scopes, raw payload) the factory
    branch forwards as trusted context. `ws_auth` is the typed per-connection auth state the
    dispatcher builds alongside it; the hydration tenant is read off *that*, as reconnect and
    continue-workflow do, so the fail-closed gate never depends on a string key into the dict.
    """
    try:
        workflow_id = message.get("workflow_id")
        session_id = message.get("session_id")
        user_message = message.get("message", "")
        user_id = message.get("user_id")
        factory_input = message.get("factory_input")

        # Defense-in-depth: if the caller authenticated via JWT, ensure user_id
        # matches the JWT sub for non-admin callers. The WS dispatcher in
        # router.py already forces this, but the handler should not trust a
        # client-supplied user_id if called from any other code path.
        if ws_user_context:
            jwt_user_id = ws_user_context.get("user_id")
            if jwt_user_id:
                from egpt.os.scopes import AgentOSScope

                scopes = ws_user_context.get("scopes", [])
                admin_scope = AgentOSScope.ADMIN.value
                is_admin = admin_scope in scopes
                if is_admin:
                    user_id = user_id or jwt_user_id
                else:
                    user_id = jwt_user_id

        if not workflow_id:
            await websocket.send_text(json.dumps({"event": "error", "error": "workflow_id is required"}))
            return

        # Get workflow from OS — supports both static and factory components
        matched_workflow = _match_workflow_in_memory(os.workflows, workflow_id)
        in_memory = matched_workflow is not None
        is_factory = isinstance(matched_workflow, WorkflowFactory)
        # The tenant that actually scopes the DB read below (aicoe-tracker#174). Resolved once,
        # ahead of both branches, via the same fail-closed contract HTTP hydration uses --
        # deliberately not resolve_tenant_id, whose user_id fallback exists for ModelPolicy
        # selection (the ctx.tenant_id built in the factory branch below) and must never widen
        # or misdirect a component read.
        try:
            hydration_tenant_id = tenant_for_ws_hydration(
                ws_auth.tenant_id if ws_auth else None,
                tenancy_enabled_override=ws_auth.tenancy_enabled if ws_auth else False,
                in_memory=in_memory,
                db=os.db,
            )
        except HTTPException as e:
            await websocket.send_text(json.dumps({"event": "error", "error": str(e.detail)}))
            return
        if is_factory:
            from egpt.factory import RequestContext, TrustedContext

            # Build trusted context from JWT claims if available (via websocket auth)
            trusted = TrustedContext()
            if ws_user_context:
                claims = ws_user_context.get("payload", {})
                scopes = ws_user_context.get("scopes", frozenset())
                if isinstance(scopes, (list, set)):
                    scopes = frozenset(scopes)
                trusted = TrustedContext(claims=claims, scopes=scopes)

            ctx = RequestContext(
                user_id=user_id,
                session_id=session_id,
                input=factory_input,
                trusted=trusted,
                # ModelPolicy/budget tenant only -- see tenant_for_ws_hydration for why this
                # must not be reused to scope the DB read below.
                tenant_id=resolve_tenant_id(
                    trusted.claims,
                    user_id,
                    verified_user_id=ws_user_context.get("user_id") if ws_user_context else None,
                ),
            )
            try:
                workflow = await get_workflow_by_id_async(
                    workflow_id=workflow_id,
                    workflows=os.workflows,
                    db=os.db,
                    registry=os.registry,
                    create_fresh=True,
                    ctx=ctx,
                    tenant_id=hydration_tenant_id,
                )
            except Exception as e:
                await websocket.send_text(json.dumps({"event": "error", "error": f"Factory error: {e}"}))
                return
        else:
            try:
                workflow = get_workflow_by_id(
                    workflow_id=workflow_id,
                    workflows=os.workflows,
                    db=os.db,
                    registry=os.registry,
                    create_fresh=True,
                    tenant_id=hydration_tenant_id,
                )
            except Exception as e:
                await websocket.send_text(json.dumps({"event": "error", "error": f"Error resolving workflow: {e}"}))
                return
        if not workflow:
            await websocket.send_text(json.dumps({"event": "error", "error": f"Workflow {workflow_id} not found"}))
            return

        if isinstance(workflow, RemoteWorkflow):
            await websocket.send_text(
                json.dumps({"event": "error", "error": "Remote workflows are not supported via WebSocket"})
            )
            return

        # Generate session_id if not provided
        # Use workflow's default session_id if not provided in message
        if not session_id:
            if workflow.session_id:
                session_id = workflow.session_id
            else:
                session_id = str(uuid4())

        # Execute workflow in background with streaming via WebSocket
        started_run = await workflow.arun(  # type: ignore
            input=user_message,
            session_id=session_id,
            user_id=user_id,
            stream=True,
            stream_events=True,
            background=True,
            websocket=websocket,
            enable_websocket=True,
        )

        # Record the tenant this run started under (aicoe-tracker#196), so a later WS
        # reconnect that finds it in the event buffer can reconcile against the same tenant
        # instead of skipping the gate entirely. Registered synchronously right after arun
        # returns -- background execution is a freshly created asyncio task that cannot run
        # (and therefore cannot add the run's first event to the buffer) until this coroutine
        # yields, so there is no window for a reconnect to observe the run as buffered but
        # unregistered.
        started_run_id = getattr(started_run, "run_id", None)
        if started_run_id:
            event_buffer.register_run_tenant(started_run_id, hydration_tenant_id)
        else:
            log_warning(
                f"start-workflow for '{workflow_id}' returned no run_id -- its tenant was not "
                "registered, so a later WS reconnect to it will be refused as unknown once "
                "tenancy is enabled."
            )

        # NOTE: Don't register the original websocket in the manager
        # It's already handled by the WebSocketHandler passed to the workflow
        # The manager is ONLY for reconnected clients (see handle_workflow_subscription)

    except (InputCheckError, OutputCheckError) as e:
        await websocket.send_text(
            json.dumps(
                {
                    "event": "error",
                    "error": str(e),
                    "error_type": e.type,
                    "error_id": e.error_id,
                    "additional_data": e.additional_data,
                }
            )
        )
    except Exception as e:
        logger.exception("Error executing workflow via WebSocket")
        error_payload = {
            "event": "error",
            "error": str(e),
            "error_type": e.type if hasattr(e, "type") else None,
            "error_id": e.error_id if hasattr(e, "error_id") else None,
        }
        error_payload = {k: v for k, v in error_payload.items() if v is not None}
        await websocket.send_text(json.dumps(error_payload))


@dataclass
class WebSocketAuthContext:
    """Per-connection auth state derived once when the WebSocket opens.

    Passed to handler functions alongside the (untrusted) client message so
    handlers don't need to read internal flags out of the client payload.
    """

    jwt_enabled: bool = False
    is_admin: bool = False
    # Opt-in per-user isolation. When False (default), ownership checks added
    # by the user-scoped-DB work stay dormant — RBAC still applies but the
    # handler treats reconnect as session-id-optional.
    user_isolation_enabled: bool = False
    # Claims-derived tenant (EGPT-2.5/aicoe-tracker#174): a websocket carries no Request, so
    # the middleware-written request.state.tenant_id does not exist here. The dispatcher reads
    # this off websocket_user_context["tenant_id"], populated the same way AuthMiddleware
    # populates request.state.tenant_id -- the configured claim, verbatim, with no fallback to
    # user_id. Pass this (with tenancy_enabled below) through tenant_for_ws_hydration before
    # using it to scope a DB read; do not use it to scope one directly.
    tenant_id: Optional[str] = None
    # Whether this deployment has opted into tenancy (a tenant_id_claim is configured). The
    # dispatcher derives it from egpt.os.tenancy.tenancy_enabled_for_app(websocket.app) -- the
    # same function HTTP's tenancy_enabled(request) wraps, so both transports agree. Required by
    # tenant_for_ws_hydration to fail closed the same way HTTP hydration does when tenant_id
    # above is None.
    tenancy_enabled: bool = False


async def handle_workflow_subscription(
    websocket: WebSocket,
    message: dict,
    os: "EgptOS",
    ws_auth: Optional[WebSocketAuthContext] = None,
):
    """
    Handle subscription/reconnection to an existing workflow run.

    Allows clients to reconnect after page refresh or disconnection and catch up on missed events.
    """
    try:
        run_id = message.get("run_id")
        workflow_id = message.get("workflow_id")
        session_id = message.get("session_id")
        user_id = message.get("user_id")
        last_event_index = message.get("last_event_index")  # 0-based index of last received event
        # Auth context is set by the WS dispatcher in router.py; default to
        # "no JWT, no isolation" for callers that bypass the dispatcher.
        ctx = ws_auth or WebSocketAuthContext()
        jwt_enabled = ctx.jwt_enabled
        is_admin = ctx.is_admin
        user_isolation_enabled = ctx.user_isolation_enabled

        if not run_id:
            await websocket.send_text(json.dumps({"event": "error", "error": "run_id is required for subscription"}))
            return

        # Non-admin JWT callers must prove session ownership before any replay or
        # live-event subscription — but only when per-user isolation is enabled.
        # The buffer path is keyed solely on run_id, so without this check (when
        # isolation is on) a caller with workflows:run could read another user's
        # run events by guessing the run_id. With isolation off, RBAC alone
        # governs reconnect access.
        if jwt_enabled and user_isolation_enabled and not is_admin and user_id:
            if not session_id:
                await websocket.send_text(
                    json.dumps(
                        {
                            "event": "error",
                            "error": SESSION_ID_REQUIRED_RECONNECT,
                        }
                    )
                )
                return
            # workflow_id is required so the session/run component check has
            # something to validate against. Defense-in-depth: the WS dispatch
            # in router.py already rejects missing workflow_id at reconnect
            # for JWT callers, but the handler must not silently fall back to
            # an unconstrained check if a future caller skips that path.
            if not workflow_id:
                await websocket.send_text(
                    json.dumps(
                        {
                            "event": "error",
                            "error": WORKFLOW_ID_REQUIRED_RECONNECT,
                        }
                    )
                )
                return

            try:
                await verify_run_in_session_via_db(
                    os.db,
                    session_id,
                    run_id,
                    user_id,
                    component_type="workflows",
                    component_id=workflow_id,
                )
            except HTTPException:
                # Mask existence of another user's run.
                await websocket.send_text(json.dumps({"event": "error", "error": f"Run {run_id} not found"}))
                return

        # Check if run exists in event buffer
        buffer_status = event_buffer.get_run_status(run_id)

        if buffer_status is not None and not buffered_run_tenant_check(
            run_id, ctx.tenant_id, tenancy_enabled=ctx.tenancy_enabled
        ):
            # Fail-closed tenant gate for the buffer arm (aicoe-tracker#196): the DB arm below
            # already reaches tenant_for_ws_hydration, but a run still in the EventBuffer used
            # to skip it entirely -- the buffer is keyed on run_id alone, so any caller with
            # workflows:run could replay another tenant's run by guessing its run_id. Mask
            # existence, consistent with the other reconnect refusals above.
            await websocket.send_text(json.dumps({"event": "error", "error": f"Run {run_id} not found"}))
            return

        if buffer_status is None:
            # Run not in buffer - check database
            if workflow_id and session_id:
                in_memory = _match_workflow_in_memory(os.workflows, workflow_id) is not None
                try:
                    # Fail-closed hydration tenant (aicoe-tracker#174): ctx.tenant_id/
                    # ctx.tenancy_enabled come straight off the dispatcher's
                    # websocket_user_context, with no user_id fallback -- see
                    # tenant_for_ws_hydration.
                    hydration_tenant_id = tenant_for_ws_hydration(
                        ctx.tenant_id,
                        tenancy_enabled_override=ctx.tenancy_enabled,
                        in_memory=in_memory,
                        db=os.db,
                    )
                except HTTPException as e:
                    await websocket.send_text(json.dumps({"event": "error", "error": str(e.detail)}))
                    return
                try:
                    workflow = get_workflow_by_id(
                        workflow_id=workflow_id,
                        workflows=os.workflows,
                        db=os.db,
                        registry=os.registry,
                        create_fresh=True,
                        tenant_id=hydration_tenant_id,
                    )
                except FactoryContextRequired:
                    workflow = None
                if workflow and isinstance(workflow, Workflow):
                    # Same tenant that just scoped the workflow hydration above (aicoe-tracker#197)
                    # -- the run/session read this ADR left unscoped.
                    workflow_run = await workflow.aget_run_output(
                        run_id, session_id, user_id=user_id, tenant_id=hydration_tenant_id
                    )

                    if workflow_run:
                        # Run exists in DB - send all events from DB
                        if workflow_run.events:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "event": "replay",
                                        "run_id": run_id,
                                        "status": workflow_run.status.value if workflow_run.status else "unknown",
                                        "total_events": len(workflow_run.events),
                                        "message": "Run completed. Replaying all events from database.",
                                    }
                                )
                            )

                            # Send events one by one
                            for idx, event in enumerate(workflow_run.events):
                                # Convert event to dict and add event_index
                                event_dict = event.model_dump() if hasattr(event, "model_dump") else event.to_dict()
                                event_dict["event_index"] = idx
                                if "run_id" not in event_dict:
                                    event_dict["run_id"] = run_id

                                await websocket.send_text(json.dumps(event_dict, default=json_serializer))
                        else:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "event": "replay",
                                        "run_id": run_id,
                                        "status": workflow_run.status.value if workflow_run.status else "unknown",
                                        "total_events": 0,
                                        "message": "Run completed but no events stored.",
                                    }
                                )
                            )
                        return

            # Run not found anywhere
            await websocket.send_text(
                json.dumps({"event": "error", "error": f"Run {run_id} not found in buffer or database"})
            )
            return

        # Run is in buffer (still active or recently completed)
        if buffer_status in [RunStatus.completed, RunStatus.error, RunStatus.cancelled]:
            # Run finished - send all events from buffer
            all_events = event_buffer.get_events(run_id, last_event_index=None)

            await websocket.send_text(
                json.dumps(
                    {
                        "event": "replay",
                        "run_id": run_id,
                        "status": buffer_status.value,
                        "total_events": len(all_events),
                        "message": f"Run {buffer_status.value}. Replaying all events.",
                    }
                )
            )

            # Send all events
            for ev_index, buffered_event in all_events:
                # Convert event to dict and add event_index
                event_dict = (
                    buffered_event.model_dump() if hasattr(buffered_event, "model_dump") else buffered_event.to_dict()
                )
                event_dict["event_index"] = ev_index
                if "run_id" not in event_dict:
                    event_dict["run_id"] = run_id

                await websocket.send_text(json.dumps(event_dict))
            return

        # Run is still active - send missed events and subscribe to new ones
        missed_events = event_buffer.get_events(run_id, last_event_index)
        current_event_count = event_buffer.get_event_count(run_id)

        if missed_events:
            # Send catch-up notification
            await websocket.send_text(
                json.dumps(
                    {
                        "event": "catch_up",
                        "run_id": run_id,
                        "status": "running",
                        "missed_events": len(missed_events),
                        "current_event_count": current_event_count,
                        "message": f"Catching up on {len(missed_events)} missed events.",
                    }
                )
            )

            # Send missed events
            for ev_index, buffered_event in missed_events:
                # Convert event to dict and add event_index
                event_dict = (
                    buffered_event.model_dump() if hasattr(buffered_event, "model_dump") else buffered_event.to_dict()
                )
                event_dict["event_index"] = ev_index
                if "run_id" not in event_dict:
                    event_dict["run_id"] = run_id

                await websocket.send_text(json.dumps(event_dict))

        # Register websocket for future events
        await websocket_manager.register_websocket(run_id, websocket)

        # Send subscription confirmation
        await websocket.send_text(
            json.dumps(
                {
                    "event": "subscribed",
                    "run_id": run_id,
                    "status": "running",
                    "current_event_count": current_event_count,
                    "message": "Subscribed to workflow run. You will receive new events as they occur.",
                }
            )
        )

        log_debug(f"Client subscribed to workflow run {run_id} (last_event_index: {last_event_index})")

    except Exception as e:
        logger.exception("Error handling workflow subscription")
        await websocket.send_text(
            json.dumps(
                {
                    "event": "error",
                    "error": f"Subscription failed: {str(e)}",
                }
            )
        )


async def handle_workflow_continue_via_websocket(
    websocket: WebSocket,
    message: dict,
    os: "EgptOS",
    ws_auth: Optional[WebSocketAuthContext] = None,
):
    """Handle continuing a paused workflow run via WebSocket"""
    try:
        workflow_id = message.get("workflow_id")
        run_id = message.get("run_id")
        session_id = message.get("session_id")
        user_id = message.get("user_id")
        step_requirements_data = message.get("step_requirements")

        if not workflow_id:
            await websocket.send_text(json.dumps({"event": "error", "error": "workflow_id is required"}))
            return
        if not run_id:
            await websocket.send_text(json.dumps({"event": "error", "error": "run_id is required"}))
            return

        # Enforce ownership for non-admin callers when user isolation is enabled.
        # Mirrors the HTTP cancel/resume routes: a non-admin caller must own
        # both the session and the run before we even fetch the paused state.
        if ws_auth and ws_auth.jwt_enabled and ws_auth.user_isolation_enabled and not ws_auth.is_admin and user_id:
            if not session_id:
                await websocket.send_text(json.dumps({"event": "error", "error": SESSION_ID_REQUIRED}))
                return
            # Prefer the factory's db when this workflow_id is a factory entry;
            # only fall back to os.db when no factory-specific db is configured.
            # This matches the pattern the HTTP cancel/resume routes use.
            factory = find_factory_by_id(workflow_id, os.workflows)
            check_db = getattr(factory, "db", None) or os.db
            try:
                await verify_run_in_session_via_db(
                    check_db,
                    session_id,
                    run_id,
                    user_id,
                    component_type="workflows",
                    component_id=workflow_id,
                )
            except HTTPException:
                await websocket.send_text(json.dumps({"event": "error", "error": f"Run {run_id} not found"}))
                return

        in_memory = _match_workflow_in_memory(os.workflows, workflow_id) is not None
        try:
            # Fail-closed hydration tenant (aicoe-tracker#174) -- see tenant_for_ws_hydration.
            hydration_tenant_id = tenant_for_ws_hydration(
                ws_auth.tenant_id if ws_auth else None,
                tenancy_enabled_override=ws_auth.tenancy_enabled if ws_auth else False,
                in_memory=in_memory,
                db=os.db,
            )
        except HTTPException as e:
            await websocket.send_text(json.dumps({"event": "error", "error": str(e.detail)}))
            return

        workflow = get_workflow_by_id(
            workflow_id=workflow_id,
            workflows=os.workflows,
            db=os.db,
            registry=os.registry,
            create_fresh=True,
            tenant_id=hydration_tenant_id,
        )
        if not workflow:
            await websocket.send_text(json.dumps({"event": "error", "error": f"Workflow {workflow_id} not found"}))
            return
        if isinstance(workflow, RemoteWorkflow):
            await websocket.send_text(
                json.dumps({"event": "error", "error": "Continue is not supported for remote workflows via WebSocket"})
            )
            return

        # Load the paused run. Same tenant that just scoped the workflow hydration above
        # (aicoe-tracker#197) -- the run/session read this ADR left unscoped.
        existing_run = await workflow.aget_run_output(
            run_id=run_id, session_id=session_id, user_id=user_id, tenant_id=hydration_tenant_id
        )
        if existing_run is None:
            await websocket.send_text(json.dumps({"event": "error", "error": f"Run {run_id} not found"}))
            return
        if not getattr(existing_run, "is_paused", False):
            status = getattr(existing_run, "status", None)
            await websocket.send_text(
                json.dumps(
                    {
                        "event": "error",
                        "error": f"Run is not paused (status={getattr(status, 'value', status)})",
                    }
                )
            )
            return

        # Apply step requirements if provided
        if step_requirements_data:
            from egpt.workflow.types import StepRequirement

            try:
                parsed_requirements = [StepRequirement.from_dict(req) for req in step_requirements_data]
                existing_run.step_requirements = parsed_requirements
            except Exception as e:
                await websocket.send_text(
                    json.dumps({"event": "error", "error": f"Invalid step_requirements: {str(e)}"})
                )
                return

        # Record/refresh the tenant this run continues under (aicoe-tracker#196) before any
        # continue event can reach the buffer, so a later reconnect reconciles against it. The
        # run_id is already known here (unlike start-workflow, continue doesn't mint a new
        # one), so this registers ahead of the call rather than off its return value.
        #
        # Unlike start, continue can target a run_id that already has a DIFFERENT tenant
        # recorded (QC round 2, finding 3): a tenant-B caller continuing tenant-A's paused run
        # -- possible today because aget_run_output/verify_run_in_session_via_db above have no
        # tenant awareness (tracked separately as aicoe-tracker#197) -- must not be allowed to
        # silently reassign the run's recorded tenant to themselves; that would both hand them
        # ongoing reconnect rights to it and lock tenant A out (masked as "not found" by the
        # very gate this fix adds). register_or_refuse_run_tenant refuses the whole continue
        # action in that case instead of overwriting.
        if not register_or_refuse_run_tenant(
            run_id, hydration_tenant_id, tenancy_enabled=ws_auth.tenancy_enabled if ws_auth else False
        ):
            await websocket.send_text(json.dumps({"event": "error", "error": f"Run {run_id} not found"}))
            return

        # Continue workflow in background with WebSocket streaming.
        # Events are broadcast via WebSocketHandler through _handle_event calls,
        # which also handles event buffering and websocket manager broadcasting.
        await workflow.acontinue_run(  # type: ignore
            run_response=existing_run,
            session_id=session_id,
            stream=True,
            stream_events=True,
            background=True,
            websocket=websocket,
            enable_websocket=True,
        )

    except (InputCheckError, OutputCheckError) as e:
        await websocket.send_text(
            json.dumps(
                {
                    "event": "error",
                    "error": str(e),
                    "error_type": e.type,
                    "error_id": e.error_id,
                    "additional_data": e.additional_data,
                }
            )
        )
    except Exception as e:
        logger.error(f"Error continuing workflow via WebSocket: {e}")
        error_payload = {
            "event": "error",
            "error": str(e),
            "error_type": e.type if hasattr(e, "type") else None,
            "error_id": e.error_id if hasattr(e, "error_id") else None,
        }
        error_payload = {k: v for k, v in error_payload.items() if v is not None}
        await websocket.send_text(json.dumps(error_payload))


async def workflow_response_streamer(
    workflow: Union[Workflow, RemoteWorkflow],
    input: Union[str, Dict[str, Any], List[Any], BaseModel],
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    background_tasks: Optional[BackgroundTasks] = None,
    auth_token: Optional[str] = None,
    **kwargs: Any,
) -> AsyncGenerator:
    try:
        # Pass background_tasks if provided
        if background_tasks is not None:
            kwargs["background_tasks"] = background_tasks

        if "stream_events" in kwargs:
            stream_events = kwargs.pop("stream_events")
        else:
            stream_events = True

        # Pass auth_token for remote workflows
        if auth_token and isinstance(workflow, RemoteWorkflow):
            kwargs["auth_token"] = auth_token

        run_response = workflow.arun(  # type: ignore
            input=input,
            session_id=session_id,
            user_id=user_id,
            stream=True,
            stream_events=stream_events,
            **kwargs,
        )

        async for run_response_chunk in run_response:
            yield format_sse_event(run_response_chunk)  # type: ignore

        # If the workflow paused, yield WorkflowPausedEvent as the new clean
        # snapshot event. Also yield the legacy "WorkflowRunOutput" event for
        # backwards compatibility with older clients.
        if isinstance(workflow, RemoteWorkflow):
            return
        _session = await workflow.aget_session(session_id=session_id)
        if _session and _session.runs:
            _last_run = _session.runs[-1]
            if getattr(_last_run, "is_paused", False):
                from egpt.run.workflow import WorkflowPausedEvent

                paused_event = WorkflowPausedEvent(
                    run_id=_last_run.run_id or "",
                    workflow_id=_last_run.workflow_id,
                    workflow_name=_last_run.workflow_name,
                    session_id=_last_run.session_id,
                    status=_last_run.status.value if hasattr(_last_run.status, "value") else _last_run.status,
                    paused_step_index=_last_run.paused_step_index,
                    paused_step_name=_last_run.paused_step_name,
                    pause_kind=_last_run.pause_kind,
                    step_requirements=_last_run.step_requirements,
                    step_results=_last_run.step_results,
                    step_executor_runs=_last_run.step_executor_runs,
                    content=_last_run.content,
                    metadata=_last_run.metadata,
                )
                yield format_sse_event(paused_event)

                # Legacy WorkflowRunOutput event for backwards compatibility
                run_dict = _last_run.to_dict()
                run_json = json.dumps(run_dict, default=json_serializer, separators=(",", ":"))
                yield f"event: WorkflowRunOutput\ndata: {run_json}\n\n"

    except (InputCheckError, OutputCheckError) as e:
        error_response = WorkflowErrorEvent(
            error=str(e),
            error_type=e.type,
            error_id=e.error_id,
            additional_data=e.additional_data,
        )
        yield format_sse_event(error_response)

    except asyncio.CancelledError:
        return
    except Exception as e:
        import traceback

        traceback.print_exc()
        error_response = WorkflowErrorEvent(
            error=str(e),
            error_type=e.type if hasattr(e, "type") else None,
            error_id=e.error_id if hasattr(e, "error_id") else None,
        )
        yield format_sse_event(error_response)
        return


async def workflow_resumable_response_streamer(
    workflow: Union[Workflow, RemoteWorkflow],
    input: Union[str, Dict[str, Any], List[Any], BaseModel],
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    background_tasks: Optional[BackgroundTasks] = None,
    auth_token: Optional[str] = None,
    **kwargs: Any,
) -> AsyncGenerator:
    """Resumable SSE generator for background=True, stream=True.

    Delegates to workflow.arun(background=True, stream=True) which handles:
    - Persisting RUNNING status in DB
    - Running workflow in a detached asyncio.Task (survives client disconnect)
    - Buffering events for reconnection via /resume
    - Publishing to SSE subscribers for resumed clients
    - Yielding SSE-formatted strings via a queue
    """
    if background_tasks is not None:
        kwargs["background_tasks"] = background_tasks

    if "stream_events" in kwargs:
        stream_events = kwargs.pop("stream_events")
    else:
        stream_events = True

    if auth_token and isinstance(workflow, RemoteWorkflow):
        kwargs["auth_token"] = auth_token

    try:
        async for sse_data in workflow.arun(  # type: ignore
            input=input,
            session_id=session_id,
            user_id=user_id,
            stream=True,
            stream_events=stream_events,
            background=True,
            **kwargs,
        ):
            yield sse_data
    except (InputCheckError, OutputCheckError) as e:
        error_response = WorkflowErrorEvent(
            error=str(e),
            error_type=e.type,
            error_id=e.error_id,
            additional_data=e.additional_data,
        )
        yield format_sse_event(error_response)
    except asyncio.CancelledError:
        return
    except Exception as e:
        import traceback

        traceback.print_exc()
        error_response = WorkflowErrorEvent(
            error=str(e),
            error_type=e.type if hasattr(e, "type") else None,
            error_id=e.error_id if hasattr(e, "error_id") else None,
        )
        yield format_sse_event(error_response)
        return


async def workflow_continue_response_streamer(
    workflow: Workflow,
    run_id: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    step_requirements: Optional[List[Any]] = None,
    background_tasks: Optional[BackgroundTasks] = None,
    **kwargs: Any,
) -> AsyncGenerator:
    try:
        if background_tasks is not None:
            kwargs["background_tasks"] = background_tasks

        run_response = await workflow.acontinue_run(  # type: ignore
            run_id=run_id,
            session_id=session_id,
            step_requirements=step_requirements,
            stream=True,
            stream_events=True,
            **kwargs,
        )

        async for run_response_chunk in run_response:
            yield format_sse_event(run_response_chunk)  # type: ignore

        # If the workflow re-paused, yield WorkflowPausedEvent as the new clean
        # snapshot event. Also yield the legacy "WorkflowRunOutput" event for
        # backwards compatibility with older clients.
        _session = await workflow.aget_session(session_id=session_id)
        if _session and _session.runs:
            _last_run = _session.runs[-1]
            if getattr(_last_run, "is_paused", False):
                from egpt.run.workflow import WorkflowPausedEvent

                paused_event = WorkflowPausedEvent(
                    run_id=_last_run.run_id or "",
                    workflow_id=_last_run.workflow_id,
                    workflow_name=_last_run.workflow_name,
                    session_id=_last_run.session_id,
                    status=_last_run.status.value if hasattr(_last_run.status, "value") else _last_run.status,
                    paused_step_index=_last_run.paused_step_index,
                    paused_step_name=_last_run.paused_step_name,
                    pause_kind=_last_run.pause_kind,
                    step_requirements=_last_run.step_requirements,
                    step_results=_last_run.step_results,
                    step_executor_runs=_last_run.step_executor_runs,
                    content=_last_run.content,
                    metadata=_last_run.metadata,
                )
                yield format_sse_event(paused_event)

                # Legacy WorkflowRunOutput event for backwards compatibility
                run_dict = _last_run.to_dict()
                run_json = json.dumps(run_dict, default=json_serializer, separators=(",", ":"))
                yield f"event: WorkflowRunOutput\ndata: {run_json}\n\n"

    except (InputCheckError, OutputCheckError) as e:
        error_response = WorkflowErrorEvent(
            error=str(e),
            error_type=e.type,
            error_id=e.error_id,
            additional_data=e.additional_data,
        )
        yield format_sse_event(error_response)
    except asyncio.CancelledError:
        return
    except Exception as e:
        import traceback

        traceback.print_exc()
        error_response = WorkflowErrorEvent(
            error=str(e),
            error_type=e.type if hasattr(e, "type") else None,
            error_id=e.error_id if hasattr(e, "error_id") else None,
        )
        yield format_sse_event(error_response)
        return


async def _resume_stream_generator(
    workflow: Union[Workflow, RemoteWorkflow],
    run_id: str,
    last_event_index: Optional[int],
    session_id: Optional[str],
    user_id: Optional[str] = None,
    caller_tenant_id: Optional[str] = None,
    tenancy_is_enabled: bool = False,
) -> AsyncGenerator:
    """SSE generator for the /resume endpoint.

    Three reconnection paths:
    1. Run still active (in buffer): replay missed events + subscribe for live events via Queue
    2. Run completed (in buffer): replay all events since last_event_index
    3. Not in buffer: fall back to database replay

    `caller_tenant_id`/`tenancy_is_enabled` gate paths 1 and 2 (aicoe-tracker#196 QC round 2,
    finding 1): the route handler hydrates `workflow` with tenant scoping before this
    generator runs, but that only proves the caller's tenant can see the *workflow component* --
    for an in-memory/code-defined workflow that hydration is exempt for every tenant, so it
    never proves this specific *run* belongs to the caller's tenant. Path 3 doesn't need the
    same gate: it re-derives the run from `aget_run_output`, which is scoped to `session_id` +
    `user_id` (ownership, checked by the caller) rather than the tenant-blind `event_buffer`.
    """
    from egpt.os.managers import sse_subscriber_manager
    from egpt.os.utils import buffered_run_tenant_check

    buffer_status = event_buffer.get_run_status(run_id)

    if buffer_status is not None and not buffered_run_tenant_check(
        run_id, caller_tenant_id, tenancy_enabled=tenancy_is_enabled
    ):
        # Mask existence, consistent with the WS reconnect gate this mirrors.
        error = {"event": "error", "error": f"Run {run_id} not found"}
        yield f"event: error\ndata: {json.dumps(error)}\n\n"
        return

    if buffer_status is None:
        # PATH 3: Not in buffer -- fall back to database
        if session_id and not isinstance(workflow, RemoteWorkflow):
            try:
                run_output = await workflow.aget_run_output(run_id=run_id, session_id=session_id, user_id=user_id)
            except Exception as e:
                error = {"event": "error", "error": f"Failed to fetch run from database: {str(e)}"}
                yield f"event: error\ndata: {json.dumps(error)}\n\n"
                return
            if run_output and run_output.events:
                meta: dict = {
                    "event": "replay",
                    "run_id": run_id,
                    "status": run_output.status.value if run_output.status else "unknown",
                    "total_events": len(run_output.events),
                    "message": "Run completed. Replaying all events from database.",
                }
                yield f"event: replay\ndata: {json.dumps(meta)}\n\n"

                for idx, event in enumerate(run_output.events):
                    event_dict = event.to_dict()
                    event_dict["event_index"] = idx
                    if "run_id" not in event_dict:
                        event_dict["run_id"] = run_id
                    event_type = event_dict.get("event", "message")
                    yield f"event: {event_type}\ndata: {json.dumps(event_dict, separators=(',', ':'), default=json_serializer, ensure_ascii=False)}\n\n"
                return
            elif run_output:
                meta = {
                    "event": "replay",
                    "run_id": run_id,
                    "status": run_output.status.value if run_output.status else "unknown",
                    "total_events": 0,
                    "message": "Run completed but no events stored.",
                }
                yield f"event: replay\ndata: {json.dumps(meta)}\n\n"
                return

        # Run not found anywhere
        error = {"event": "error", "error": f"Run {run_id} not found in buffer or database"}
        yield f"event: error\ndata: {json.dumps(error)}\n\n"
        return

    if buffer_status in (RunStatus.completed, RunStatus.error, RunStatus.cancelled, RunStatus.paused):
        # PATH 2: Run finished -- replay missed events from buffer
        total_buffered = event_buffer.get_event_count(run_id)
        missed_events = event_buffer.get_events(run_id, last_event_index=last_event_index)
        log_debug(
            f"Workflow resume PATH 2: run_id={run_id}, status={buffer_status.value}, "
            f"last_event_index={last_event_index}, total_buffered={total_buffered}, "
            f"missed_events={len(missed_events)}"
        )

        meta = {
            "event": "replay",
            "run_id": run_id,
            "status": buffer_status.value,
            "total_events": len(missed_events),
            "total_buffered": total_buffered,
            "last_event_index_requested": last_event_index if last_event_index is not None else -1,
            "message": f"Run {buffer_status.value}. Replaying {len(missed_events)} missed events (of {total_buffered} total).",
        }
        yield f"event: replay\ndata: {json.dumps(meta)}\n\n"

        for ev_index, buffered_event in missed_events:
            event_dict = buffered_event.to_dict()
            event_dict["event_index"] = ev_index
            if "run_id" not in event_dict:
                event_dict["run_id"] = run_id
            event_type = event_dict.get("event", "message")
            yield f"event: {event_type}\ndata: {json.dumps(event_dict, separators=(',', ':'), default=json_serializer, ensure_ascii=False)}\n\n"
        return

    # PATH 1: Run still active -- subscribe FIRST (to avoid race condition), then replay missed events
    queue = sse_subscriber_manager.subscribe(run_id)

    try:
        missed_events = event_buffer.get_events(run_id, last_event_index)
        current_count = event_buffer.get_event_count(run_id)

        # Track the highest replayed event_index for dedup against queue events
        last_replayed_index = last_event_index if last_event_index is not None else -1

        if missed_events:
            meta = {
                "event": "catch_up",
                "run_id": run_id,
                "status": "running",
                "missed_events": len(missed_events),
                "current_event_count": current_count,
                "message": f"Catching up on {len(missed_events)} missed events.",
            }
            yield f"event: catch_up\ndata: {json.dumps(meta)}\n\n"

            for ev_index, buffered_event in missed_events:
                event_dict = buffered_event.to_dict()
                event_dict["event_index"] = ev_index
                if "run_id" not in event_dict:
                    event_dict["run_id"] = run_id
                event_type = event_dict.get("event", "message")
                yield f"event: {event_type}\ndata: {json.dumps(event_dict, separators=(',', ':'), default=json_serializer, ensure_ascii=False)}\n\n"
                last_replayed_index = ev_index

        # Re-check buffer status after subscribing
        updated_status = event_buffer.get_run_status(run_id)
        if updated_status is not None and updated_status != RunStatus.running:
            remaining = event_buffer.get_events(run_id, last_event_index=last_replayed_index)
            if remaining:
                for ev_index, buffered_event in remaining:
                    event_dict = buffered_event.to_dict()
                    event_dict["event_index"] = ev_index
                    if "run_id" not in event_dict:
                        event_dict["run_id"] = run_id
                    event_type = event_dict.get("event", "message")
                    yield f"event: {event_type}\ndata: {json.dumps(event_dict, separators=(',', ':'), default=json_serializer, ensure_ascii=False)}\n\n"
            return

        # Stream live events from queue (dedup by event_index)
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Check if run ended without sending sentinel
                status = event_buffer.get_run_status(run_id)
                if status is None or status != RunStatus.running:
                    # Run ended - replay any remaining events from buffer
                    remaining = event_buffer.get_events(run_id, last_event_index=last_replayed_index)
                    for ev_index, buffered_event in remaining:
                        event_dict = buffered_event.to_dict()
                        event_dict["event_index"] = ev_index
                        if "run_id" not in event_dict:
                            event_dict["run_id"] = run_id
                        event_type = event_dict.get("event", "message")
                        yield f"event: {event_type}\ndata: {json.dumps(event_dict, separators=(',', ':'), default=json_serializer, ensure_ascii=False)}\n\n"
                    break
                # Still running - send heartbeat to keep connection alive
                yield ": heartbeat\n\n"
                continue
            if item is None:
                break
            event_index, sse_data = item
            if event_index <= last_replayed_index:
                continue
            last_replayed_index = event_index
            yield sse_data

    finally:
        sse_subscriber_manager.unsubscribe(run_id, queue)


def get_workflow_router(
    os: "EgptOS",
    settings: EgptServingSettings = EgptServingSettings(),
) -> APIRouter:
    """Create the workflow router with comprehensive OpenAPI documentation."""
    router = APIRouter(
        dependencies=[Depends(get_authentication_dependency(settings))],
        responses={
            400: {"description": "Bad Request", "model": BadRequestResponse},
            401: {"description": "Unauthorized", "model": UnauthenticatedResponse},
            404: {"description": "Not Found", "model": NotFoundResponse},
            422: {"description": "Validation Error", "model": ValidationErrorResponse},
            500: {"description": "Internal Server Error", "model": InternalServerErrorResponse},
        },
    )

    @router.get(
        "/workflows",
        response_model=List[WorkflowSummaryResponse],
        response_model_exclude_none=True,
        tags=["Workflows"],
        operation_id="get_workflows",
        summary="List All Workflows",
        description=(
            "Retrieve a comprehensive list of all workflows configured in this OS instance.\n\n"
            "**Return Information:**\n"
            "- Workflow metadata (ID, name, description)\n"
            "- Input schema requirements\n"
            "- Step sequence and execution flow\n"
            "- Associated agents and teams"
        ),
        responses={
            200: {
                "description": "List of workflows retrieved successfully",
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "id": "content-creation-workflow",
                                "name": "Content Creation Workflow",
                                "description": "Automated content creation from blog posts to social media",
                                "db_id": "123",
                            }
                        ]
                    }
                },
            }
        },
    )
    async def get_workflows(request: Request) -> List[WorkflowSummaryResponse]:
        # Filter workflows based on user's scopes (only if authorization is enabled)
        if getattr(request.state, "authorization_enabled", False):
            from egpt.os.auth import (
                build_insufficient_permissions_detail,
                filter_resources_by_access,
                get_accessible_resources,
            )

            # Check if user has any workflow scopes at all
            accessible_ids = get_accessible_resources(request, "workflows")
            if not accessible_ids:
                required_scopes = getattr(request.state, "required_scopes", None)
                raise HTTPException(
                    status_code=403,
                    detail=build_insufficient_permissions_detail(required_scopes),
                )

            accessible_workflows = filter_resources_by_access(request, os.workflows or [], "workflows")
        else:
            accessible_workflows = os.workflows or []

        workflows: List[WorkflowSummaryResponse] = []
        if accessible_workflows:
            for workflow in accessible_workflows:
                workflows.append(WorkflowSummaryResponse.from_workflow(workflow=workflow, is_component=False))

        if os.db and isinstance(os.db, BaseDb):
            from egpt.workflow.workflow import get_workflows

            for db_workflow in get_workflows(
                db=os.db, registry=os.registry, tenant_id=tenant_for_direct_read(request, db=os.db)
            ):
                try:
                    workflows.append(WorkflowSummaryResponse.from_workflow(workflow=db_workflow, is_component=True))
                except Exception:
                    workflow_id = getattr(db_workflow, "id", "unknown")
                    logger.exception(f"Error converting workflow {workflow_id} to response")
                    continue

        return workflows

    @router.get(
        "/workflows/{workflow_id}",
        response_model=WorkflowResponse,
        response_model_exclude_none=True,
        tags=["Workflows"],
        operation_id="get_workflow",
        summary="Get Workflow Details",
        description=("Retrieve detailed configuration and step information for a specific workflow."),
        responses={
            200: {
                "description": "Workflow details retrieved successfully",
                "content": {
                    "application/json": {
                        "example": {
                            "id": "content-creation-workflow",
                            "name": "Content Creation Workflow",
                            "description": "Automated content creation from blog posts to social media",
                            "db_id": "123",
                        }
                    }
                },
            },
            404: {"description": "Workflow not found", "model": NotFoundResponse},
        },
        dependencies=[Depends(require_resource_access("workflows", "read", "workflow_id"))],
    )
    async def get_workflow(
        workflow_id: str,
        request: Request,
        version: Optional[int] = Query(None, description="Workflow version to retrieve"),
    ) -> WorkflowResponse:
        # Factory workflows: return factory metadata directly
        factory = find_factory_by_id(workflow_id, os.workflows)
        if factory:
            return WorkflowResponse.from_factory(factory)

        try:
            workflow = get_workflow_by_id(
                workflow_id=workflow_id,
                workflows=os.workflows,
                db=os.db,
                registry=os.registry,
                create_fresh=True,
                version=version,
                tenant_id=tenant_for_direct_read(request, db=os.db, components=os.workflows, component_id=workflow_id),
            )  # type: ignore[assignment]
        except PolicyViolationError as e:
            # 403, not 500: the component exists and hydrates fine -- the caller's tenant
            # is just not permitted the capabilities it names (EGPT-2.3).
            raise HTTPException(status_code=403, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error resolving workflow '{workflow_id}': {e}")
            raise HTTPException(status_code=500, detail=f"Error resolving workflow: {e}")
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")

        if isinstance(workflow, RemoteWorkflow):
            return await workflow.get_workflow_config()
        else:
            return await WorkflowResponse.from_workflow(workflow=workflow)

    @router.post(
        "/workflows/{workflow_id}/runs",
        tags=["Workflows"],
        operation_id="create_workflow_run",
        response_model_exclude_none=True,
        summary="Execute Workflow",
        description=(
            "Execute a workflow with the provided input data. Workflows can run in streaming or batch mode.\n\n"
            "**Execution Modes:**\n"
            "- **Streaming (`stream=true`)**: Real-time step-by-step execution updates via SSE\n"
            "- **Non-Streaming (`stream=false`)**: Complete workflow execution with final result\n\n"
            "**Workflow Execution Process:**\n"
            "1. Input validation against workflow schema\n"
            "2. Sequential or parallel step execution based on workflow design\n"
            "3. Data flow between steps with transformation\n"
            "4. Error handling and automatic retries where configured\n"
            "5. Final result compilation and response\n\n"
            "**Session Management:**\n"
            "Workflows support session continuity for stateful execution across multiple runs."
        ),
        responses={
            200: {
                "description": "Workflow executed successfully",
                "content": {
                    "text/event-stream": {
                        "example": 'event: RunStarted\ndata: {"content": "Hello!", "run_id": "123..."}\n\n'
                    },
                },
            },
            400: {"description": "Invalid input data or workflow configuration", "model": BadRequestResponse},
            404: {"description": "Workflow not found", "model": NotFoundResponse},
            500: {"description": "Workflow execution error", "model": InternalServerErrorResponse},
        },
        dependencies=[Depends(require_resource_access("workflows", "run", "workflow_id"))],
    )
    async def create_workflow_run(
        workflow_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
        message: str = Form(..., description="The input message or prompt to send to the workflow"),
        stream: bool = Form(True, description="Enable streaming responses via Server-Sent Events (SSE)"),
        background: bool = Form(
            False,
            description="Run workflow in background (survives client disconnect). Requires database. Use /resume to reconnect.",
        ),
        session_id: Optional[str] = Form(
            None, description="Session ID for conversation continuity. If not provided, a new session is created"
        ),
        user_id: Optional[str] = Form(None, description="User identifier for tracking and personalization"),
        version: Optional[int] = Form(None, description="Workflow version to use for this run"),
        factory_input: Optional[str] = Form(
            None,
            description="JSON object with factory-specific parameters for dynamic workflow construction",
        ),
    ):
        kwargs = await get_request_kwargs(request, create_workflow_run)

        # Scoped non-admin callers always get their JWT sub as user_id.
        # Admins and unscoped callers fall through to middleware/form values.
        scoped_user_id = get_scoped_user_id(request)
        if scoped_user_id is not None:
            user_id = scoped_user_id
        elif hasattr(request.state, "user_id") and request.state.user_id is not None:
            if user_id and user_id != request.state.user_id:
                log_warning("User ID parameter passed in both request state and kwargs, using request state")
            user_id = request.state.user_id
        if hasattr(request.state, "session_id") and request.state.session_id is not None:
            if session_id and session_id != request.state.session_id:
                log_warning("Session ID parameter passed in both request state and kwargs, using request state")
            session_id = request.state.session_id
        if hasattr(request.state, "session_state") and request.state.session_state is not None:
            session_state = request.state.session_state
            if "session_state" in kwargs:
                log_warning("Session state parameter passed in both request state and kwargs, using request state")
            kwargs["session_state"] = session_state
        if hasattr(request.state, "dependencies") and request.state.dependencies is not None:
            dependencies = request.state.dependencies
            if "dependencies" in kwargs:
                log_warning("Dependencies parameter passed in both request state and kwargs, using request state")
            kwargs["dependencies"] = dependencies
        if hasattr(request.state, "metadata") and request.state.metadata is not None:
            metadata = request.state.metadata
            if "metadata" in kwargs:
                log_warning("Metadata parameter passed in both request state and kwargs, using request state")
            kwargs["metadata"] = metadata

        # Retrieve the workflow by ID (supports both static and factory components)
        workflow = await resolve_workflow(
            workflow_id,
            os.workflows,
            os.db,
            os.registry,
            version=version,
            request=request,
            user_id=user_id,
            session_id=session_id,
            factory_input=factory_input,
        )

        if session_id:
            logger.debug(f"Continuing session: {session_id}")
        else:
            logger.debug("Creating new session")
            session_id = str(uuid4())

        # Extract auth token for remote workflows
        auth_token = get_auth_token_from_request(request)

        # The caller's tenant (aicoe-tracker#196), recorded on this run's event buffer entry so
        # a later WS reconnect / HTTP /resume can refuse a mismatched tenant instead of
        # replaying across tenants -- same value `resolve_workflow` above used to scope
        # hydration, computed independently since resolve_workflow doesn't return it.
        caller_tenant_id = tenant_for_direct_read(request, db=os.db, components=os.workflows, component_id=workflow_id)
        # RemoteWorkflow.arun() has no tenant_id parameter -- registration is moot for it
        # anyway, since resume_workflow_run_stream already refuses remote workflows outright.
        # Only the two foreground branches below can reach a RemoteWorkflow (the background
        # branches raise on isinstance(workflow, RemoteWorkflow) first), so this has to be
        # conditional rather than a bare tenant_id=caller_tenant_id kwarg.
        tenant_kwargs = {} if isinstance(workflow, RemoteWorkflow) else {"tenant_id": caller_tenant_id}

        # Background execution
        if background:
            if isinstance(workflow, RemoteWorkflow):
                raise HTTPException(
                    status_code=400, detail="Background execution is not supported for remote workflows"
                )

            if stream:
                # background=True, stream=True: resumable SSE streaming
                # Workflow runs in a detached asyncio.Task that survives client disconnections.
                # Events are buffered for reconnection via /resume endpoint.
                return StreamingResponse(
                    workflow_resumable_response_streamer(
                        workflow,
                        input=message,
                        session_id=session_id,
                        user_id=user_id,
                        background_tasks=background_tasks,
                        auth_token=auth_token,
                        tenant_id=caller_tenant_id,
                        **kwargs,
                    ),
                    media_type="text/event-stream",
                )

            # background=True, stream=False: return 202 immediately with run metadata
            if not workflow.db:
                raise HTTPException(
                    status_code=400,
                    detail="Background execution requires a database to be configured on the workflow",
                )

            run_response = await workflow.arun(
                input=message,
                session_id=session_id,
                user_id=user_id,
                stream=False,
                background=True,
                background_tasks=background_tasks,
                tenant_id=caller_tenant_id,
                **kwargs,
            )
            return JSONResponse(
                status_code=202,
                content={
                    "run_id": run_response.run_id,
                    "session_id": run_response.session_id,
                    "status": run_response.status.value if run_response.status else "PENDING",
                },
            )

        # Return based on stream parameter
        try:
            if stream:
                return StreamingResponse(
                    workflow_response_streamer(
                        workflow,
                        input=message,
                        session_id=session_id,
                        user_id=user_id,
                        background_tasks=background_tasks,
                        auth_token=auth_token,
                        **tenant_kwargs,
                        **kwargs,
                    ),
                    media_type="text/event-stream",
                )
            else:
                if isinstance(workflow, RemoteWorkflow):
                    # Pass auth_token for remote workflows. No tenant_id: RemoteWorkflow.arun()
                    # has no such parameter, and registration is moot for it anyway -- resume
                    # already refuses remote workflows outright.
                    if auth_token:
                        kwargs["auth_token"] = auth_token
                    run_response = await workflow.arun(
                        input=message,
                        session_id=session_id,
                        user_id=user_id,
                        stream=False,
                        background_tasks=background_tasks,
                        **kwargs,
                    )
                else:
                    run_response = await workflow.arun(
                        input=message,
                        session_id=session_id,
                        user_id=user_id,
                        stream=False,
                        background_tasks=background_tasks,
                        tenant_id=caller_tenant_id,
                        **kwargs,
                    )
                return run_response.to_dict()

        except InputCheckError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            # Handle unexpected runtime errors
            raise HTTPException(status_code=500, detail=f"Error running workflow: {str(e)}")

    @router.post(
        "/workflows/{workflow_id}/runs/{run_id}/continue",
        tags=["Workflows"],
        operation_id="continue_workflow_run",
        response_model_exclude_none=True,
        summary="Continue Workflow Run",
        description=(
            "Continue a paused workflow run with resolved requirements.\n\n"
            "**Use Cases:**\n"
            "- Resume after step-level HITL (confirmation, user input, router selection)\n"
            "- Resume after executor-level HITL (agent/team tool confirmation within a step)\n\n"
            "**Requirements Parameter:**\n"
            "JSON string containing the resolved step requirements."
        ),
        responses={
            200: {
                "description": "Workflow run continued successfully",
                "content": {
                    "text/event-stream": {"example": 'event: StepCompleted\ndata: {"step_name": "step1"}\n\n'},
                },
            },
            400: {"description": "Invalid JSON in requirements field", "model": BadRequestResponse},
            404: {"description": "Workflow not found", "model": NotFoundResponse},
            409: {
                "description": "Run is not paused. Only PAUSED runs can be continued.",
            },
        },
        dependencies=[Depends(require_resource_access("workflows", "run", "workflow_id"))],
    )
    async def continue_workflow_run(
        workflow_id: str,
        run_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
        step_requirements: str = Form("", description="JSON string of step requirement objects with resolution status"),
        session_id: Optional[str] = Form(None, description="Session ID for the paused run"),
        user_id: Optional[str] = Form(None, description="User identifier for tracking and personalization"),
        stream: bool = Form(True, description="Enable streaming responses via Server-Sent Events (SSE)"),
        factory_input: Optional[str] = Form(
            None,
            description="JSON object with factory-specific parameters for dynamic workflow reconstruction",
        ),
    ):
        if hasattr(request.state, "user_id") and request.state.user_id is not None:
            user_id = request.state.user_id
        if hasattr(request.state, "session_id") and request.state.session_id is not None:
            session_id = request.state.session_id

        # Parse step requirements JSON
        try:
            step_requirements_data = json.loads(step_requirements) if step_requirements else None
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in step_requirements field")

        workflow = await resolve_workflow(
            workflow_id,
            os.workflows,
            os.db,
            os.registry,
            request=request,
            user_id=user_id,
            session_id=session_id,
            factory_input=factory_input,
        )

        if isinstance(workflow, RemoteWorkflow):
            raise HTTPException(status_code=400, detail="Continue is not supported for remote workflows")

        # Ownership check before status validation — see continue_agent_run.
        # Non-admin callers must own the session AND the run must belong to
        # this workflow (per-resource RBAC).
        scoped_user_id = get_scoped_user_id(request)
        if scoped_user_id is not None:
            if not session_id:
                raise HTTPException(status_code=400, detail=SESSION_ID_REQUIRED)
            await verify_run_in_session(
                workflow,
                session_id,
                run_id,
                scoped_user_id,
                component_type="workflows",
                component_id=workflow_id,
            )

        # Load existing run and validate it's paused
        existing_run = await workflow.aget_run_output(
            run_id=run_id, session_id=session_id, user_id=scoped_user_id or user_id
        )
        if existing_run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        if not getattr(existing_run, "is_paused", False):
            status = getattr(existing_run, "status", None)
            _status_to_detail = {
                RunStatus.running: "run is already running",
                RunStatus.completed: "run is already completed",
                RunStatus.error: "run has errored",
                RunStatus.cancelled: "run is already cancelled",
            }
            detail = _status_to_detail.get(
                status,  # type: ignore[arg-type]
                f"run is not paused (status={getattr(status, 'value', status)})",
            )
            raise HTTPException(status_code=409, detail=detail)

        # Convert step requirements dicts to StepRequirement objects
        from egpt.workflow.types import StepRequirement

        parsed_requirements: Optional[List[StepRequirement]] = None
        if step_requirements_data:
            try:
                parsed_requirements = [StepRequirement.from_dict(req) for req in step_requirements_data]
            except Exception as e:
                raise HTTPException(
                    status_code=400, detail=f"Invalid structure or content for step_requirements: {str(e)}"
                )

        # Force JWT user_id for non-admin callers so a spoofed user_id cannot
        # attribute the continued run to another user.
        effective_user_id = scoped_user_id if scoped_user_id is not None else user_id

        # Record/refresh the tenant this run continues under (aicoe-tracker#196), refusing
        # instead of silently overwriting if a different tenant is already recorded for
        # run_id -- see the WS continue-workflow handler for the full rationale (QC round 2,
        # finding 3). Applied once here, ahead of both the streaming and non-streaming branches
        # below, rather than duplicated inside workflow_continue_response_streamer.
        caller_tenant_id = tenant_for_direct_read(request, db=os.db, components=os.workflows, component_id=workflow_id)
        if not register_or_refuse_run_tenant(run_id, caller_tenant_id, tenancy_enabled=tenancy_enabled(request)):
            raise HTTPException(status_code=404, detail="Run not found")

        if stream:
            return StreamingResponse(
                workflow_continue_response_streamer(
                    workflow,
                    run_id=run_id,
                    session_id=session_id,
                    user_id=effective_user_id,
                    step_requirements=parsed_requirements,
                    background_tasks=background_tasks,
                ),
                media_type="text/event-stream",
            )
        else:
            try:
                # Ownership already verified above; acontinue_run loads the run
                # via {session_id, run_id} which we've proven the caller owns.
                run_response = await workflow.acontinue_run(  # type: ignore[call-overload]
                    run_id=run_id,
                    session_id=session_id,
                    step_requirements=parsed_requirements,
                    stream=False,
                    background_tasks=background_tasks,
                )
                return run_response.to_dict()
            except InputCheckError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error continuing workflow run: {str(e)}")

    @router.post(
        "/workflows/{workflow_id}/runs/{run_id}/cancel",
        tags=["Workflows"],
        operation_id="cancel_workflow_run",
        summary="Cancel Workflow Run",
        description=(
            "Cancel a currently executing workflow run, stopping all active steps and cleanup.\n"
            "**Note:** Complex workflows with multiple parallel steps may take time to fully cancel."
        ),
        responses={
            200: {},
            404: {"description": "Workflow or run not found", "model": NotFoundResponse},
            500: {"description": "Failed to cancel workflow run", "model": InternalServerErrorResponse},
        },
        dependencies=[Depends(require_resource_access("workflows", "run", "workflow_id"))],
    )
    async def cancel_workflow_run(
        request: Request,
        workflow_id: str,
        run_id: str,
        session_id: Optional[str] = Query(
            default=None,
            description="Session ID the run belongs to. Required for non-admin JWT users.",
        ),
    ):
        # Factory workflows: cancel is static, no workflow instance needed.
        # Non-admin callers must still prove session ownership before we apply
        # a global cancellation intent keyed solely on run_id.
        factory = find_factory_by_id(workflow_id, os.workflows)
        if factory:
            from egpt.run.cancel import acancel_run

            scoped_user_id = get_scoped_user_id(request)
            if scoped_user_id is not None:
                if not session_id:
                    raise HTTPException(status_code=400, detail=SESSION_ID_REQUIRED)
                check_db = getattr(factory, "db", None) or os.db
                await verify_run_in_session_via_db(
                    check_db,
                    session_id,
                    run_id,
                    scoped_user_id,
                    component_type="workflows",
                    component_id=workflow_id,
                )

            await acancel_run(run_id)
            return JSONResponse(content={}, status_code=200)

        try:
            workflow = get_workflow_by_id(
                workflow_id=workflow_id,
                workflows=os.workflows,
                db=os.db,
                registry=os.registry,
                create_fresh=True,
                tenant_id=tenant_for_direct_read(request, db=os.db, components=os.workflows, component_id=workflow_id),
            )  # type: ignore[assignment]
        except PolicyViolationError as e:
            # 403, not 500: the component exists and hydrates fine -- the caller's tenant
            # is just not permitted the capabilities it names (EGPT-2.3).
            raise HTTPException(status_code=403, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error resolving workflow '{workflow_id}': {e}")
            raise HTTPException(status_code=500, detail=f"Error resolving workflow: {e}")
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")

        # Ownership check: non-admin JWT callers must supply a session_id and the
        # run must live in a session they own. Admins / unauthenticated bypass.
        scoped_user_id = get_scoped_user_id(request)
        if scoped_user_id is not None:
            if not session_id:
                raise HTTPException(status_code=400, detail=SESSION_ID_REQUIRED)
            await verify_run_in_session(
                workflow,
                session_id,
                run_id,
                scoped_user_id,
                component_type="workflows",
                component_id=workflow_id,
            )

        # cancel_run always stores cancellation intent (even for not-yet-registered runs
        # in cancel-before-start scenarios), so we always return success.
        await workflow.acancel_run(run_id=run_id)
        return JSONResponse(content={}, status_code=200)

    @router.post(
        "/workflows/{workflow_id}/runs/{run_id}/resume",
        tags=["Workflows"],
        operation_id="resume_workflow_run_stream",
        summary="Resume Workflow Run Stream",
        description=(
            "Resume an SSE stream for a workflow run after disconnection.\n\n"
            "Sends missed events since `last_event_index`, then continues streaming "
            "live events if the run is still active.\n\n"
            "**Three reconnection paths:**\n"
            "1. **Run still active**: Sends catch-up events + continues live streaming\n"
            "2. **Run completed (in buffer)**: Replays missed buffered events\n"
            "3. **Run completed (in database)**: Replays events from database\n\n"
            "**Client usage:**\n"
            "Track `event_index` from each SSE event. On reconnection, pass the last "
            "received `event_index` as `last_event_index`."
        ),
        responses={
            200: {
                "description": "SSE stream of catch-up and/or live events",
                "content": {"text/event-stream": {}},
            },
            400: {"description": "Not supported for remote workflows", "model": BadRequestResponse},
            404: {"description": "Workflow not found", "model": NotFoundResponse},
        },
        dependencies=[Depends(require_resource_access("workflows", "run", "workflow_id"))],
    )
    async def resume_workflow_run_stream(
        request: Request,
        workflow_id: str,
        run_id: str,
        last_event_index: Optional[int] = Form(None, description="Index of last event received by client (0-based)"),
        session_id: Optional[str] = Form(None, description="Session ID for database fallback"),
    ):
        # Ownership check up-front (see resume_agent_run_stream for rationale).
        scoped_user_id = get_scoped_user_id(request)
        if scoped_user_id is not None:
            if not session_id:
                raise HTTPException(status_code=400, detail=SESSION_ID_REQUIRED)

        factory = find_factory_by_id(workflow_id, os.workflows)
        if factory:
            if scoped_user_id is not None:
                assert session_id is not None
                check_db = getattr(factory, "db", None) or os.db
                await verify_run_in_session_via_db(
                    check_db,
                    session_id,
                    run_id,
                    scoped_user_id,
                    component_type="workflows",
                    component_id=workflow_id,
                )
            raise HTTPException(
                status_code=400,
                detail="Stream resumption is not supported for factory workflows",
            )

        caller_tenant_id = tenant_for_direct_read(request, db=os.db, components=os.workflows, component_id=workflow_id)
        workflow = get_workflow_by_id(
            workflow_id=workflow_id,
            workflows=os.workflows,
            db=os.db,
            registry=os.registry,
            create_fresh=True,
            tenant_id=caller_tenant_id,
        )
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        if isinstance(workflow, RemoteWorkflow):
            raise HTTPException(status_code=400, detail="Stream resumption is not supported for remote workflows")

        if scoped_user_id is not None:
            assert session_id is not None
            await verify_run_in_session(
                workflow,
                session_id,
                run_id,
                scoped_user_id,
                component_type="workflows",
                component_id=workflow_id,
            )

        return StreamingResponse(
            _resume_stream_generator(
                workflow,
                run_id,
                last_event_index,
                session_id,
                user_id=scoped_user_id,
                caller_tenant_id=caller_tenant_id,
                tenancy_is_enabled=tenancy_enabled(request),
            ),
            media_type="text/event-stream",
        )

    @router.get(
        "/workflows/{workflow_id}/runs/{run_id}",
        tags=["Workflows"],
        operation_id="get_workflow_run",
        summary="Get Workflow Run",
        description=(
            "Retrieve the status and output of a workflow run. Use this to poll for run completion.\n\n"
            "Requires the `session_id` that was returned when the run was created."
        ),
        responses={
            200: {"description": "Run output retrieved successfully"},
            404: {"description": "Workflow or run not found", "model": NotFoundResponse},
        },
        dependencies=[Depends(require_resource_access("workflows", "run", "workflow_id"))],
    )
    async def get_workflow_run(
        request: Request,
        workflow_id: str,
        run_id: str,
        session_id: str = Query(..., description="Session ID for the run"),
        factory_input: Optional[str] = Query(
            None,
            description="JSON object with factory-specific parameters for dynamic workflow reconstruction",
        ),
    ):
        user_id = getattr(request.state, "user_id", None)
        if hasattr(request.state, "session_id") and request.state.session_id is not None:
            if session_id and session_id != request.state.session_id:
                log_warning("Session ID parameter passed in both request state and query params, using request state")
            session_id = request.state.session_id

        # Factory workflows: resolve to get a real workflow for session lookup
        factory = find_factory_by_id(workflow_id, os.workflows)
        if factory:
            workflow = await resolve_workflow(  # type: ignore[assignment]
                workflow_id,
                os.workflows,
                factory.db,
                request=request,
                user_id=user_id,
                session_id=session_id,
                factory_input=factory_input,
            )
        else:
            try:
                workflow = get_workflow_by_id(
                    workflow_id=workflow_id,
                    workflows=os.workflows,
                    db=os.db,
                    registry=os.registry,
                    create_fresh=True,
                    tenant_id=tenant_for_direct_read(
                        request, db=os.db, components=os.workflows, component_id=workflow_id
                    ),
                )  # type: ignore[assignment]
            except PolicyViolationError as e:
                # 403, not 500: the component exists and hydrates fine -- the caller's tenant
                # is just not permitted the capabilities it names (EGPT-2.3).
                raise HTTPException(status_code=403, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error resolving workflow '{workflow_id}': {e}")
                raise HTTPException(status_code=500, detail=f"Error resolving workflow: {e}")
            if workflow is None:
                raise HTTPException(status_code=404, detail="Workflow not found")
        if isinstance(workflow, RemoteWorkflow):
            raise HTTPException(status_code=400, detail="Run polling is not supported for remote workflows")

        user_id = get_scoped_user_id(request)

        # Verify session belongs to this workflow BEFORE loading the run.
        # See get_agent_run for the cross-component bypass this blocks.
        if hasattr(workflow, "aget_session"):
            session = await workflow.aget_session(session_id=session_id, user_id=user_id)  # type: ignore[union-attr]
            if session is None:
                raise HTTPException(status_code=404, detail="Run not found")
            assert_session_matches_component(session, "workflows", workflow_id, not_found_detail="Run not found")

        run_output = await workflow.aget_run_output(run_id=run_id, session_id=session_id, user_id=user_id)
        if run_output is None:
            raise HTTPException(status_code=404, detail="Run not found")

        # Per-resource RBAC: the run must explicitly belong to the path workflow.
        # Fail closed when workflow_id is missing.
        if not run_matches_component(run_output, "workflows", workflow_id):
            raise HTTPException(status_code=404, detail="Run not found")

        return run_output.to_dict()

    @router.get(
        "/workflows/{workflow_id}/runs",
        tags=["Workflows"],
        operation_id="list_workflow_runs",
        summary="List Workflow Runs",
        description=(
            "List runs for a workflow within a session, optionally filtered by status.\n\n"
            "Useful for monitoring background runs and viewing run history."
        ),
        responses={
            200: {"description": "List of runs retrieved successfully"},
            404: {"description": "Workflow not found", "model": NotFoundResponse},
        },
        dependencies=[Depends(require_resource_access("workflows", "run", "workflow_id"))],
    )
    async def list_workflow_runs(
        workflow_id: str,
        request: Request,
        session_id: str = Query(..., description="Session ID to list runs for"),
        status: Optional[str] = Query(
            None, description="Filter by run status (PENDING, RUNNING, COMPLETED, ERROR, PAUSED)"
        ),
        factory_input: Optional[str] = Query(
            None,
            description="JSON object with factory-specific parameters for dynamic workflow reconstruction",
        ),
    ):
        from egpt.os.schema import WorkflowRunSchema

        # Non-admin callers must only see runs from sessions they own. Admins
        # (scoped_user_id is None) bypass and can list runs for any session.
        scoped_user_id = get_scoped_user_id(request)
        user_id = scoped_user_id if scoped_user_id is not None else getattr(request.state, "user_id", None)
        if hasattr(request.state, "session_id") and request.state.session_id is not None:
            if session_id and session_id != request.state.session_id:
                log_warning("Session ID parameter passed in both request state and query params, using request state")
            session_id = request.state.session_id

        workflow = await resolve_workflow(
            workflow_id,
            os.workflows,
            os.db,
            os.registry,
            request=request,
            user_id=user_id,
            session_id=session_id,
            factory_input=factory_input,
        )
        if isinstance(workflow, RemoteWorkflow):
            raise HTTPException(status_code=400, detail="Run listing is not supported for remote workflows")

        # Read-only session lookup (no create) so we don't manufacture a session
        # for a user who shouldn't see it. For non-admins, scope by user_id so
        # mismatched ownership returns 404, not a leak.
        lookup_user_id = scoped_user_id if scoped_user_id is not None else None
        session = await workflow.aget_session(session_id=session_id, user_id=lookup_user_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        # Per-resource RBAC: the session must explicitly belong to this workflow.
        # Fail closed when workflow_id is missing — an agent/team session
        # must not be reachable through a workflow route.
        assert_session_matches_component(session, "workflows", workflow_id)

        runs = session.runs or []

        # Filter to runs that belong to this workflow. Workflow sessions can
        # carry nested member runs whose workflow_id is unset; fail closed
        # rather than leaking those.
        result = []
        for run in runs:
            if not run_matches_component(run, "workflows", workflow_id):
                continue
            run_dict = run.to_dict()
            if status and run_dict.get("status") != status:
                continue
            result.append(WorkflowRunSchema.from_dict(run_dict))

        return result

    return router
