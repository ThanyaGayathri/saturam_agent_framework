import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
)

from egpt import __version__ as agno_version
from egpt.agent.factory import AgentFactory
from egpt.agent.protocol import AgentProtocol
from egpt.exceptions import RemoteServerUnavailableError
from egpt.os.auth import (
    get_authentication_dependency,
    get_effective_auth_mode,
    validate_websocket_token,
    verify_websocket_service_account,
)
from egpt.os.managers import websocket_manager
from egpt.os.middleware.jwt import INVALID_TENANT_CLAIM_ERROR, JWTValidator, extract_tenant_claim, is_reserved_principal
from egpt.os.middleware.user_scope import (
    INSUFFICIENT_PERMISSIONS_WS_RECONNECT,
    WORKFLOW_ID_REQUIRED_RECONNECT,
)
from egpt.os.routers.workflows.router import (
    WebSocketAuthContext,
    handle_workflow_continue_via_websocket,
    handle_workflow_subscription,
    handle_workflow_via_websocket,
)
from egpt.os.schema import (
    AgentSummaryResponse,
    BadRequestResponse,
    ConfigResponse,
    InfoResponse,
    InterfaceResponse,
    InternalServerErrorResponse,
    McpInfo,
    Model,
    NotFoundResponse,
    TeamSummaryResponse,
    UnauthenticatedResponse,
    ValidationErrorResponse,
    WorkflowSummaryResponse,
)
from egpt.os.scopes import (
    AgentOSScope,
    get_default_scope_mappings,
    get_required_scopes_for_route,
    has_required_scopes,
)
from egpt.os.service_accounts import TOKEN_PREFIX as SERVICE_ACCOUNT_TOKEN_PREFIX
from egpt.os.service_accounts import VerificationStatus
from egpt.os.settings import EgptServingSettings
from egpt.os.tenancy import tenancy_enabled_for_app
from egpt.os.utils import resolve_ws_jwt_config
from egpt.team.factory import TeamFactory
from egpt.utils.log import logger

if TYPE_CHECKING:
    from egpt.os.app import EgptOS


def get_base_router(
    os: "EgptOS",
    settings: EgptServingSettings = EgptServingSettings(),
) -> APIRouter:
    """
    Create the base FastAPI router with comprehensive OpenAPI documentation.

    This router provides endpoints for:
    - Core system operations (health, config, models)
    - Agent management and execution
    - Team collaboration and coordination
    - Workflow automation and orchestration

    All endpoints include detailed documentation, examples, and proper error handling.
    """
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

    # -- Main Routes ---
    @router.get(
        "/config",
        response_model=ConfigResponse,
        response_model_exclude_none=True,
        tags=["Core"],
        operation_id="get_config",
        summary="Get OS Configuration",
        description=(
            "Retrieve the complete configuration of the EgptOS instance, including:\n\n"
            "- Available models and databases\n"
            "- Registered agents, teams, and workflows\n"
            "- Chat, session, memory, knowledge, and evaluation configurations\n"
            "- Available interfaces and their routes"
        ),
        responses={
            200: {
                "description": "OS configuration retrieved successfully",
                "content": {
                    "application/json": {
                        "example": {
                            "id": "demo",
                            "description": "Example EgptOS configuration",
                            "available_models": [],
                            "databases": ["9c884dc4-9066-448c-9074-ef49ec7eb73c"],
                            "session": {
                                "dbs": [
                                    {
                                        "db_id": "9c884dc4-9066-448c-9074-ef49ec7eb73c",
                                        "domain_config": {"display_name": "Sessions"},
                                    }
                                ]
                            },
                            "metrics": {
                                "dbs": [
                                    {
                                        "db_id": "9c884dc4-9066-448c-9074-ef49ec7eb73c",
                                        "domain_config": {"display_name": "Metrics"},
                                    }
                                ]
                            },
                            "memory": {
                                "dbs": [
                                    {
                                        "db_id": "9c884dc4-9066-448c-9074-ef49ec7eb73c",
                                        "domain_config": {"display_name": "Memory"},
                                    }
                                ]
                            },
                            "knowledge": {
                                "dbs": [
                                    {
                                        "db_id": "9c884dc4-9066-448c-9074-ef49ec7eb73c",
                                        "domain_config": {"display_name": "Knowledge"},
                                    }
                                ]
                            },
                            "evals": {
                                "dbs": [
                                    {
                                        "db_id": "9c884dc4-9066-448c-9074-ef49ec7eb73c",
                                        "domain_config": {"display_name": "Evals"},
                                    }
                                ]
                            },
                            "agents": [
                                {
                                    "id": "main-agent",
                                    "name": "Main Agent",
                                    "db_id": "9c884dc4-9066-448c-9074-ef49ec7eb73c",
                                }
                            ],
                            "teams": [],
                            "workflows": [],
                            "interfaces": [],
                        }
                    }
                },
            }
        },
    )
    async def config() -> ConfigResponse:
        try:
            agent_summaries = [AgentSummaryResponse.from_agent(a) for a in os.agents] if os.agents else []
            team_summaries = [TeamSummaryResponse.from_team(t) for t in os.teams] if os.teams else []
            workflow_summaries = (
                [WorkflowSummaryResponse.from_workflow(w) for w in os.workflows] if os.workflows else []
            )
        except RemoteServerUnavailableError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch config from remote EgptOS: {e}",
            )

        return ConfigResponse(
            os_id=os.id or "Unnamed OS",
            description=os.description,
            available_models=os.config.available_models if os.config else [],
            os_database=os.db.id if os.db else None,
            databases=list({db.id for db_id, dbs in os.dbs.items() for db in dbs}),
            chat=os.config.chat if os.config else None,
            manifest=os.config.manifest if os.config else None,
            session=os._get_session_config(),
            memory=os._get_memory_config(),
            learning=os._get_learning_config(),
            knowledge=os._get_knowledge_config(),
            evals=os._get_evals_config(),
            metrics=os._get_metrics_config(),
            agents=agent_summaries,
            teams=team_summaries,
            workflows=workflow_summaries,
            traces=os._get_traces_config(),
            interfaces=[
                InterfaceResponse(type=interface.type, version=interface.version, route=interface.prefix)
                for interface in os.interfaces
            ],
        )

    @router.get(
        "/models",
        response_model=List[Model],
        response_model_exclude_none=True,
        tags=["Core"],
        operation_id="get_models",
        summary="Get Available Models",
        description=(
            "Retrieve a list of all unique models currently used by agents and teams in this OS instance. "
            "This includes the model ID and provider information for each model."
        ),
        responses={
            200: {
                "description": "List of models retrieved successfully",
                "content": {
                    "application/json": {
                        "example": [
                            {"id": "gpt-4", "provider": "openai"},
                            {"id": "claude-3-sonnet", "provider": "anthropic"},
                        ]
                    }
                },
            }
        },
    )
    async def get_models() -> List[Model]:
        """Return the list of all models used by agents and teams in the contextual OS"""
        unique_models = {}

        # Collect models from local agents
        if os.agents:
            for agent in os.agents:
                if isinstance(agent, AgentFactory):
                    continue
                if isinstance(agent, AgentProtocol):
                    continue
                model = cast(Model, agent.model)
                if model and model.id is not None and model.provider is not None:
                    key = (model.id, model.provider)
                    if key not in unique_models:
                        unique_models[key] = Model(id=model.id, provider=model.provider)

        # Collect models from local teams
        if os.teams:
            for team in os.teams:
                if isinstance(team, TeamFactory):
                    continue
                model = cast(Model, team.model)
                if model and model.id is not None and model.provider is not None:
                    key = (model.id, model.provider)
                    if key not in unique_models:
                        unique_models[key] = Model(id=model.id, provider=model.provider)

        return list(unique_models.values())

    return router


def get_info_router(os: "EgptOS") -> APIRouter:
    """
    Create an unauthenticated router that returns lightweight OS metadata.
    """
    router = APIRouter(tags=["Core"])

    @router.get(
        "/info",
        operation_id="get_info",
        summary="Get OS Info",
        description="Return lightweight, unauthenticated metadata about this EgptOS instance.",
        response_model=InfoResponse,
    )
    async def get_info(request: Request) -> InfoResponse:
        mcp_enabled = bool(os.enable_mcp_server)
        return InfoResponse(
            agno_version=agno_version,
            agent_count=len(os.agents or []),
            team_count=len(os.teams or []),
            workflow_count=len(os.workflows or []),
            mcp=McpInfo(enabled=mcp_enabled, path="/mcp" if mcp_enabled else None),
            auth_mode=get_effective_auth_mode(settings=os.settings, authorization=os.authorization, app=request.app),
        )

    return router


def get_websocket_router(
    os: "EgptOS",
    settings: EgptServingSettings = EgptServingSettings(),
) -> APIRouter:
    """
    Create WebSocket router without HTTP authentication dependencies.
    WebSocket endpoints handle authentication internally via message-based auth.

    Supports both JWT and legacy (os_security_key) authentication.
    When JWT is configured (via authorization=True on EgptOS), tokens are
    validated using the JWTValidator stored on app.state. Scopes from the
    JWT are enforced before workflow execution.
    """
    ws_router = APIRouter()

    @ws_router.websocket(
        "/workflows/ws",
        name="workflow_websocket",
    )
    async def workflow_websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for receiving real-time workflow events"""
        # Check if JWT validator is configured (set by EgptOS when authorization=True
        # or, for the manual app.add_middleware(JWTMiddleware, ...) path, resolved
        # lazily from app.user_middleware so the FIRST WebSocket connection cannot
        # see requires_auth=False before any HTTP request has been handled).
        ws_jwt_config = resolve_ws_jwt_config(websocket.app)
        jwt_validator: Optional[JWTValidator] = ws_jwt_config.get("validator")
        ws_verify_audience: bool = ws_jwt_config.get("verify_audience", False)
        ws_audience = ws_jwt_config.get("audience")
        ws_admin_scope: str = ws_jwt_config.get("admin_scope") or AgentOSScope.ADMIN.value
        ws_user_isolation_enabled: bool = bool(ws_jwt_config.get("user_isolation", False))
        # Without this the capability policy runs untenanted on the WebSocket surface, which
        # resolves to the document's `defaults` block -- the permissive direction (PR #53 review).
        ws_tenant_id_claim: Optional[str] = ws_jwt_config.get("tenant_id_claim")
        # Derive the scope required to run a workflow from the shared scope-mapping table
        # (same source REST and MCP use) instead of hardcoding "workflows:run" here, so a
        # change to the mapping applies to the WebSocket surface automatically.
        ws_workflow_run_scopes: List[str] = get_required_scopes_for_route(
            get_default_scope_mappings(), "POST", "/workflows/_/runs"
        )
        jwt_auth_enabled = jwt_validator is not None
        # auth_required is True when JWTMiddleware is configured, even if the
        # validator could not be constructed (e.g. bad JWKS path). This prevents
        # silently falling through to unauthenticated mode on misconfiguration.
        jwt_auth_required = bool(ws_jwt_config.get("auth_required", False))

        # Determine auth requirements - JWT takes precedence over legacy
        requires_auth = jwt_auth_enabled or jwt_auth_required or bool(settings.os_security_key)

        await websocket_manager.connect(websocket, requires_auth=requires_auth)

        # Store user context from the authenticated identity (JWT or service account).
        # tenancy_enabled comes from the same helper the HTTP path's tenancy_enabled(request)
        # wraps -- one derivation, not a mirror. It must not be read off ws_jwt_config above:
        # resolve_ws_jwt_config skips a JWTMiddleware entry with no key source, so on a PAT-only
        # tenancy deployment it reports tenant_id_claim=None while HTTP's gate is live (PR #121
        # review). tenant_for_ws_hydration (egpt.os.utils) takes this as an explicit override
        # because it has no Request to derive it from. Set once, independent of which auth
        # branch below fires.
        websocket_user_context: Dict[str, Any] = {"tenancy_enabled": tenancy_enabled_for_app(websocket.app)}

        def scope_enforcement_active() -> bool:
            # JWT deployments always enforce scopes. Service-account identities do
            # too -- their scopes are first-party ACL data, enforced in every
            # deployment mode (same rule as REST and MCP). Security-key auth
            # attaches no scopes and retains full access.
            return jwt_auth_enabled or "scopes" in websocket_user_context

        def drop_identity() -> None:
            # Reset the connection to "unauthenticated, no principal" ahead of every
            # authenticate attempt. A Request carries no identity between requests, so an HTTP
            # 401 is self-contained; a socket does carry one, so a failed re-authenticate must
            # behave like that 401 -- nothing from the previous principal (user_id, scopes,
            # claims, tenant) survives, and the authenticated flag is cleared until a credential
            # verifies again. This is also what keeps a stale tenant_id from satisfying the
            # fail-closed tenant_for_ws_hydration gate after re-auth as an untenanted identity.
            # tenancy_enabled is deployment state, not identity, and stays.
            for key in ("user_id", "scopes", "payload", "tenant_id"):
                websocket_user_context.pop(key, None)
            websocket_manager.deauthenticate_websocket(websocket)

        def build_ws_auth(is_admin: bool) -> WebSocketAuthContext:
            # Auth context handed to the workflow handlers out-of-band, so they never read
            # internal flags out of the client message. tenant_id/tenancy_enabled come straight
            # off websocket_user_context (aicoe-tracker#174) -- not resolve_tenant_id, whose
            # user_id fallback must never scope a component DB read. The handlers run these
            # fail-closed through tenant_for_ws_hydration before using them. Built in one place
            # so the three hydrating actions cannot drift on which fields they carry.
            return WebSocketAuthContext(
                jwt_enabled=scope_enforcement_active(),
                is_admin=is_admin,
                user_isolation_enabled=ws_user_isolation_enabled,
                tenant_id=websocket_user_context.get("tenant_id"),
                tenancy_enabled=bool(websocket_user_context.get("tenancy_enabled")),
            )

        try:
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)
                action = message.get("action")

                # Handle authentication first
                if action == "authenticate":
                    # Every attempt, successful or not, starts from no identity: see drop_identity.
                    drop_identity()
                    token = message.get("token")
                    if not token:
                        await websocket.send_text(json.dumps({"event": "auth_error", "error": "Token is required"}))
                        continue

                    if token.startswith(SERVICE_ACCOUNT_TOKEN_PREFIX):
                        # Service-account PATs are opaque first-party credentials --
                        # never decoded as JWTs (mirroring the REST middleware's
                        # prefix dispatch) and verified fail-closed in every
                        # deployment mode.
                        client_key = websocket.client.host if websocket.client else None
                        verification = await verify_websocket_service_account(
                            token, websocket.app, client_key=client_key
                        )
                        account = verification.account if verification is not None and verification.ok else None
                        if account is not None:
                            # Attach the account identity so the same RBAC and
                            # attribution gates that police JWTs apply to PATs.
                            websocket_user_context["user_id"] = account.principal
                            websocket_user_context["scopes"] = list(account.scopes)
                            # Same as the HTTP path (egpt/os/service_accounts.py sets
                            # request.state.tenant_id = account.tenant_id): the account's minted
                            # tenant, verbatim, with no fallback -- None stays None rather than
                            # becoming the principal. drop_identity() above already cleared any
                            # tenant from a prior authenticate on this connection, so an
                            # untenanted account cannot inherit one.
                            if account.tenant_id:
                                websocket_user_context["tenant_id"] = account.tenant_id
                            await websocket_manager.authenticate_websocket(websocket)
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "event": "authenticated",
                                        "message": "Service account authentication successful.",
                                        "user_id": account.principal,
                                    }
                                )
                            )
                        else:
                            error_msg = "Invalid or expired service account token"
                            if verification is not None and verification.status == VerificationStatus.THROTTLED:
                                error_msg = "Too many failed authentication attempts"
                            elif verification is not None and verification.status == VerificationStatus.UNAVAILABLE:
                                error_msg = "Authentication is temporarily unavailable"
                            await websocket.send_text(json.dumps({"event": "auth_error", "error": error_msg}))
                        continue

                    if jwt_auth_required and not jwt_auth_enabled:
                        # JWTMiddleware is configured but the validator could
                        # not be constructed (e.g. bad JWKS path). Reject
                        # rather than silently falling through to legacy auth.
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "event": "auth_error",
                                    "error": "JWT authentication is misconfigured on the server",
                                    "error_type": "server_error",
                                }
                            )
                        )
                        continue
                    elif jwt_auth_enabled and jwt_validator:
                        # Use JWT validator for token validation. Honour the
                        # configured audience so verify_audience=True applies to
                        # WebSocket tokens, not just HTTP requests.
                        try:
                            expected_audience = None
                            if ws_verify_audience:
                                expected_audience = ws_audience or getattr(websocket.app.state, "agent_os_id", None)
                            payload = jwt_validator.validate_token(token, expected_audience)
                            claims = jwt_validator.extract_claims(payload)

                            # A JWT must not claim a reserved principal (a service account's
                            # sa:... or the scheduler) as its subject; mirrors the HTTP
                            # middleware so WS run attribution/ownership cannot be spoofed.
                            if is_reserved_principal(claims.get("user_id")):
                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            "event": "auth_error",
                                            "error": "Invalid token subject",
                                            "error_type": "invalid_token",
                                        }
                                    )
                                )
                                continue

                            # Same rule as the HTTP middleware, via the same function
                            # (extract_tenant_claim): an absent claim is unscoped, a present
                            # but malformed one is rejected outright -- "unscoped" is the widest
                            # tenancy, so degrading to it would turn a bad claim into an
                            # escalation. Resolved before authenticate_websocket so a rejected
                            # token never authenticates the socket, exactly as HTTP writes
                            # nothing to request.state on its 401.
                            try:
                                tenant_id = extract_tenant_claim(payload, ws_tenant_id_claim)
                            except ValueError as tenant_error:
                                logger.warning(f"Rejected WebSocket JWT with an unusable tenant claim: {tenant_error}")
                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            "event": "auth_error",
                                            "error": INVALID_TENANT_CLAIM_ERROR,
                                            "error_type": "invalid_token",
                                        }
                                    )
                                )
                                continue

                            await websocket_manager.authenticate_websocket(websocket)

                            # Store user context from JWT
                            websocket_user_context["user_id"] = claims["user_id"]
                            websocket_user_context["scopes"] = claims["scopes"]
                            websocket_user_context["payload"] = payload
                            # drop_identity() already cleared any prior tenant; only a claim on
                            # *this* token sets one.
                            if tenant_id is not None:
                                websocket_user_context["tenant_id"] = tenant_id

                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "event": "authenticated",
                                        "message": "JWT authentication successful.",
                                        "user_id": claims["user_id"],
                                    }
                                )
                            )
                        except Exception as e:
                            error_msg = str(e) if str(e) else "Invalid token"
                            error_type = "expired" if "expired" in error_msg.lower() else "invalid_token"
                            await websocket.send_text(
                                json.dumps({"event": "auth_error", "error": error_msg, "error_type": error_type})
                            )
                        continue
                    elif validate_websocket_token(token, settings):
                        # Legacy os_security_key authentication
                        await websocket_manager.authenticate_websocket(websocket)
                    else:
                        await websocket.send_text(json.dumps({"event": "auth_error", "error": "Invalid token"}))
                    continue

                # Check authentication for all other actions (only when required)
                elif requires_auth and not websocket_manager.is_authenticated(websocket):
                    auth_type = "JWT" if (jwt_auth_enabled or jwt_auth_required) else "bearer token"
                    await websocket.send_text(
                        json.dumps(
                            {
                                "event": "auth_required",
                                "error": f"Authentication required. Send authenticate action with valid {auth_type}.",
                            }
                        )
                    )
                    continue

                # Handle authenticated actions
                elif action == "ping":
                    await websocket.send_text(json.dumps({"event": "pong"}))

                elif action == "start-workflow":
                    # Enforce workflow-level RBAC whenever scope enforcement is
                    # active (JWT auth, or a service-account identity).
                    # Check RBAC unconditionally — do not skip when workflow_id
                    # is absent, otherwise an unauthenticated-scope caller can
                    # bypass the permission gate by omitting workflow_id and
                    # letting the downstream handler reject it *after* any
                    # side-effects.
                    workflow_id = message.get("workflow_id")
                    if scope_enforcement_active():
                        user_scopes = websocket_user_context.get("scopes", [])
                        if not has_required_scopes(
                            user_scopes,
                            ws_workflow_run_scopes,
                            resource_type="workflows",
                            resource_id=workflow_id,
                            admin_scope=ws_admin_scope,
                        ):
                            await websocket.send_text(
                                json.dumps({"event": "error", "error": "Insufficient permissions to run this workflow"})
                            )
                            continue

                    # Force user_id from the authenticated identity (JWT sub or
                    # service-account principal) for non-admin callers so the
                    # client cannot attribute a run to another user by spoofing
                    # the field.
                    auth_user_id = websocket_user_context.get("user_id")
                    is_admin = False
                    if auth_user_id:
                        is_admin = ws_admin_scope in websocket_user_context.get("scopes", [])
                        if is_admin:
                            message.setdefault("user_id", auth_user_id)
                        else:
                            message["user_id"] = auth_user_id
                    await handle_workflow_via_websocket(
                        websocket,
                        message,
                        os,
                        ws_user_context=websocket_user_context,
                        ws_auth=build_ws_auth(is_admin),
                    )

                elif action == "reconnect":
                    # Force user_id from the authenticated identity for non-admins
                    # so reconnecting cannot read another user's run events by
                    # swapping user_id.
                    auth_user_id = websocket_user_context.get("user_id")
                    is_admin = False
                    if auth_user_id:
                        is_admin = ws_admin_scope in websocket_user_context.get("scopes", [])
                        if is_admin:
                            message.setdefault("user_id", auth_user_id)
                        else:
                            message["user_id"] = auth_user_id

                    # Enforce workflow-level RBAC at reconnect just like
                    # start-workflow does. RBAC fires whenever JWT auth is on
                    # (independent of user isolation) so a token with no
                    # workflows:run can't subscribe to a buffered run by
                    # guessing its run_id. The workflow_id requirement, by
                    # contrast, only matters when user isolation is enabled —
                    # that's when the downstream session/component check
                    # actually uses it.
                    workflow_id_for_reconnect = message.get("workflow_id")
                    if scope_enforcement_active() and not is_admin:
                        if ws_user_isolation_enabled and not workflow_id_for_reconnect:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "event": "error",
                                        "error": WORKFLOW_ID_REQUIRED_RECONNECT,
                                    }
                                )
                            )
                            continue

                        user_scopes = websocket_user_context.get("scopes", [])
                        if not has_required_scopes(
                            user_scopes,
                            ws_workflow_run_scopes,
                            resource_type="workflows",
                            resource_id=workflow_id_for_reconnect,
                            admin_scope=ws_admin_scope,
                        ):
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "event": "error",
                                        "error": INSUFFICIENT_PERMISSIONS_WS_RECONNECT,
                                    }
                                )
                            )
                            continue

                    await handle_workflow_subscription(websocket, message, os, ws_auth=build_ws_auth(is_admin))

                elif action == "continue-workflow":
                    # Enforce workflow-level RBAC, mirroring start-workflow.
                    workflow_id = message.get("workflow_id")
                    if scope_enforcement_active():
                        user_scopes = websocket_user_context.get("scopes", [])
                        if not has_required_scopes(
                            user_scopes,
                            ws_workflow_run_scopes,
                            resource_type="workflows",
                            resource_id=workflow_id,
                            admin_scope=ws_admin_scope,
                        ):
                            await websocket.send_text(
                                json.dumps(
                                    {"event": "error", "error": "Insufficient permissions to continue this workflow"}
                                )
                            )
                            continue

                    # Force user_id from the authenticated identity for non-admin
                    # callers so the client cannot continue another user's paused
                    # run by spoofing the field.
                    auth_user_id = websocket_user_context.get("user_id")
                    is_admin = False
                    if auth_user_id:
                        is_admin = ws_admin_scope in websocket_user_context.get("scopes", [])
                        if is_admin:
                            message.setdefault("user_id", auth_user_id)
                        else:
                            message["user_id"] = auth_user_id

                    await handle_workflow_continue_via_websocket(
                        websocket, message, os, ws_auth=build_ws_auth(is_admin)
                    )

                else:
                    await websocket.send_text(json.dumps({"event": "error", "error": f"Unknown action: {action}"}))

        except Exception as e:
            if "1012" not in str(e) and "1001" not in str(e):
                logger.exception("WebSocket error")
        finally:
            # Clean up the websocket connection
            await websocket_manager.disconnect_websocket(websocket)

    return ws_router
