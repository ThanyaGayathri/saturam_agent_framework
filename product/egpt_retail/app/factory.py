"""``build_agent_os`` -- pure construction, importable without assembling anything.

Split out of ``main.py`` so importing this function does not also run ``main.py``'s module-level
``settings = RetailSettings()`` / ``agent_os = build_agent_os(settings)`` / ``app = ...`` --
which construct ``PostgresDb``, the agent, the registry and the FastAPI app as an import side
effect. A test (or any other caller) that only wants ``build_agent_os`` should not need a real
environment just to import it, and should not fail to collect if one happens to be present but
incomplete (``EGPT_RETAIL_ROLE=pipelines`` with no token raises from here, not from an import).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, List

from egpt.memory.startup import assert_memory_wired
from egpt.answer_provenance import apply_provenance_ddl
from egpt.os import EgptOS
from egpt.os.config import AuthorizationConfig
from egpt.os.middleware.fixed_tenant import FixedTenantMiddleware
from egpt.utils.log import log_error
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from httpx import HTTPStatusError
from starlette.middleware.cors import CORSMiddleware

from egpt_retail.api.routers.business_areas import router as business_areas_router
from egpt_retail.api.routers.dashboards import router as dashboards_router
from egpt_retail.api.routers.extraction_templates import (
    router as extraction_templates_router,
)
from egpt_retail.api.routers.kpis import router as kpis_router
from egpt_retail.api.routers.monitoring import router as monitoring_router
from egpt_retail.api.routers.playground import router as playground_router
from egpt_retail.api.routers.semantic import router as semantic_router
from egpt_retail.api.routers.targets import router as targets_router
from egpt_retail.api.routers.use_cases import router as use_cases_router
from egpt_retail.app.components import (
    abuild_memory_provider,
    build_agents,
    build_registry,
    build_workflows,
)
from egpt_retail.app.db import (
    build_control_plane_db,
    build_control_plane_engine,
    psycopg_dsn,
)
from egpt_retail.app.settings import RetailSettings
from egpt_retail.delivery import apply_delivery_ddl
from egpt_retail.documents.rollback import apply_rollback_ddl
from egpt_retail.usecases.ddl import apply_use_case_ddl
from egpt_retail.dashboards.store import DashboardStore
from egpt_retail.kpi.store import KpiRegistryStore
from egpt_retail.playground.store import PlaygroundStore
from egpt_retail.semantic.proposals import ProposalStore
from egpt_retail.suggestions.runs import SuggestionRunStore

__all__ = ["OS_ID", "build_agent_os"]

OS_ID = "egpt-retail"


def memory_lifespan_for(settings: RetailSettings, agents: List[Any]) -> Any:
    """The startup that wires memory, and refuses to serve if it cannot (G1).

    Deliberately a *lifespan* and not part of ``build_agent_os``. ``build_memory_provider_for``
    opens the pgvector engine and provisions its schema on construction, and ``app/main.py``
    calls the factory at module import -- so building the provider in the factory made
    *importing the app* open a database. That broke every test that constructs the OS without
    Postgres, and it is not what G1 asks for. G1 asks that the deployment fail **at startup**
    rather than serve memoryless. A lifespan is startup: the server boots, wires the provider
    into every agent, checks every agent can use it, and refuses to come up if not. Import
    stays pure, the same way Lane R's consolidator builds its provider lazily.
    """

    @asynccontextmanager
    async def _lifespan(app: Any, agent_os: Any):
        # Awaited, not called: construction opens the pgvector engine and provisions its
        # schema, and this coroutine is the event loop's own startup hook -- the sync call
        # blocked the loop for a database round trip on every boot.
        provider = await abuild_memory_provider(settings)
        for agent in agents:
            if getattr(agent, "memory_provider", None) is None:
                agent.memory_provider = provider
        try:
            # Every fault at once, so a misconfigured deployment is fixed in one pass.
            assert_memory_wired(agents, tenant_id=settings.retail_customer_slug)
            yield
        finally:
            if provider is not None:
                await provider.aclose()

    return _lifespan


def _start_use_case_poller(settings: RetailSettings) -> Any:
    """K16's in-process poller, only when the setting says so (see ``usecases/poller.py``)."""
    if not settings.retail_use_case_poller:
        return None
    from egpt_retail.api.routers.use_cases import use_case_service
    from egpt_retail.usecases.poller import UseCasePoller

    poller = UseCasePoller(use_case_service())
    poller.start()
    return poller


def _ensure_control_plane_schemas(settings: RetailSettings) -> None:
    """Provisions R11's, R13's and B1's control-plane tables once, at boot -- not per request
    (``api/routers/playground.py``, ``dashboards.py`` and ``kpis.py`` open a fresh connection
    per call, but do not re-run DDL on it). Synchronous and cheap (advisory lock, ``CREATE ...
    IF NOT EXISTS``); run inside the composed lifespan below, same startup point K1's memory
    check runs at, for the same reason: a deployment that cannot provision its own tables
    should fail at boot, not on the first request that touches them.

    ``KpiRegistryStore``'s ``semantic.kpi_definition`` table is otherwise only provisioned by
    ``semantic/refresh.py``'s offline refresh job or ``entrypoints/suggest.py`` -- neither
    guaranteed to have run before the API serves traffic, which left a fresh deployment's
    first ``GET /kpis`` a raw "relation does not exist" 500 instead of an empty list.
    """
    import psycopg

    connection = psycopg.connect(psycopg_dsn(settings.retail_db_url))
    try:
        PlaygroundStore(connection).ensure_schema()
        DashboardStore(connection).ensure_schema()
        KpiRegistryStore(connection).ensure_schema()
        ProposalStore(connection).ensure_schema()
        SuggestionRunStore(connection).ensure_schema()
    finally:
        connection.close()
    # The Engine-based tables: R7's provenance and delivery records (every compiled
    # workflow ends in ``record_provenance``, so a fresh control plane without the table
    # fails every first run at its last step), K16's use cases and runs, and the rollback
    # ledger the Act executors write. All idempotent; ``egpt-retail migrate`` applies the
    # same DDL for a deployment that migrates before it serves.
    engine = build_control_plane_engine(settings)
    try:
        apply_provenance_ddl(engine)
        apply_delivery_ddl(engine)
        apply_use_case_ddl(engine)
        apply_rollback_ddl(engine)
    finally:
        engine.dispose()


def _combined_lifespan(settings: RetailSettings, agents: List[Any]) -> Any:
    """K1's memory lifespan, plus R11/R13/B1's schema provisioning. One ``EgptOS(lifespan=...)``
    slot exists, so the two are composed here rather than each trying to claim it."""
    memory_lifespan = memory_lifespan_for(settings, agents)

    @asynccontextmanager
    async def _lifespan(app: Any, agent_os: Any):
        _ensure_control_plane_schemas(settings)
        async with memory_lifespan(app, agent_os):
            poller = _start_use_case_poller(settings)
            try:
                yield
            finally:
                if poller is not None:
                    await poller.stop()

    return _lifespan


def _cors_headers_for_error(request: Request) -> dict:
    """Origin-reflecting CORS headers for a response sent from *outside* ``CORSMiddleware``.

    Starlette's ``Starlette.build_middleware_stack`` pulls the ``Exception``/500 handler out of
    ``exception_handlers`` by exact key match and hands it to ``ServerErrorMiddleware`` --
    which sits outermost, ahead of every user middleware including CORS -- while every *other*
    registered handler (``HTTPException``, ``RequestValidationError``, ``HTTPStatusError``)
    stays with ``ExceptionMiddleware``, which CORS correctly wraps. Verified live: a 404 from
    ``GET /kpis/{id}/feasibility`` and a 422 from ``POST /kpis`` both already carried
    ``access-control-allow-origin``; a genuinely unhandled exception (``GET /kpis/suggestions``
    failing on a missing model credential) did not, on the exact same origin. A browser reports
    a response with no CORS header as a CORS failure regardless of why it is missing, which
    masked the real 500 and its ``detail`` behind a misleading "CORS policy" console error and
    an "Is it running?" message on the frontend. Reads the live ``CORSMiddleware`` entry off
    ``request.app`` instead of hardcoding an origin list, so it always matches whatever
    ``EgptOS.get_app()``'s own ``update_cors_middleware`` last configured.
    """
    origin = request.headers.get("origin")
    if not origin:
        return {}
    for middleware in request.app.user_middleware:
        if middleware.cls is CORSMiddleware:
            allow_origins = middleware.kwargs.get("allow_origins", [])
            if "*" in allow_origins or origin in allow_origins:
                headers = {"access-control-allow-origin": origin, "vary": "Origin"}
                # Read off the live middleware config rather than assuming: harmless today only
                # because `update_cors_middleware` unconditionally passes `allow_credentials=True`,
                # but that's flagged there as likely to become configurable.
                if middleware.kwargs.get("allow_credentials", False):
                    headers["access-control-allow-credentials"] = "true"
                return headers
            return {}
    return {}


def _register_exception_handlers(base_app: FastAPI) -> None:
    """Mirrors ``egpt.os.app.EgptOS.get_app``'s own four handlers, on retail's own ``base_app``.

    ``EgptOS.get_app`` only installs them ``if not self._app_set`` -- and passing ``base_app=``
    to ``EgptOS`` (below) sets ``_app_set = True`` at construction, before ``get_app`` ever
    runs, on the assumption that a caller supplying its own app already owns error handling for
    it. This deployment never did, which left every unhandled exception from any retail router
    (``playground``, ``dashboards``, ``business_areas``, ``kpis``) falling through to Starlette's
    default ``ServerErrorMiddleware`` response: plain text, no ``detail`` body, and -- because
    that middleware sits *outside* ``CORSMiddleware`` in the stack -- no CORS headers either.
    A browser reports that as a CORS failure regardless of origin, masking the real status and
    body from both the frontend's error handling and anyone reading devtools. Verified live:
    ``GET /kpis/suggestions`` failing on a missing model credential came back as a bare
    ``500 Internal Server Error`` with no ``access-control-allow-origin`` header, where
    ``GET /kpis`` succeeding on the same origin carried one.
    """

    @base_app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        log_error(f"Validation error (422): {exc.errors()}")
        return JSONResponse(
            status_code=422, content={"detail": jsonable_encoder(exc.errors())}
        )

    @base_app.exception_handler(HTTPException)
    async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        log_error(f"HTTP exception: {exc.status_code} {exc.detail}")
        return JSONResponse(
            status_code=exc.status_code, content={"detail": str(exc.detail)}
        )

    @base_app.exception_handler(HTTPStatusError)
    async def _http_status_error_handler(
        _: Request, exc: HTTPStatusError
    ) -> JSONResponse:
        status_code = exc.response.status_code
        detail = exc.response.text
        log_error(
            f"Downstream server returned HTTP status error: {status_code} {detail}"
        )
        return JSONResponse(status_code=status_code, content={"detail": detail})

    @base_app.exception_handler(Exception)
    async def _general_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        import traceback

        log_error(f"Unhandled exception:\n{traceback.format_exc(limit=5)}")
        # `str(exc)` is not returned here (unlike the framework default this mirrors): this is
        # retail's externally-reachable API, and its routers touch a Postgres control-plane DB,
        # JWKS/auth config and downstream LLM providers -- any of which can raise an exception
        # whose string form embeds a connection string, file path or credential-shaped value.
        # The real detail is still logged above for anyone with server access.
        # This one handler runs outside `CORSMiddleware` regardless of origin (see
        # `_cors_headers_for_error`'s docstring) -- every other handler above does not need this.
        return JSONResponse(
            status_code=getattr(exc, "status_code", 500),
            content={"detail": "Internal server error."},
            headers=_cors_headers_for_error(request),
        )


def build_agent_os(settings: RetailSettings) -> EgptOS:
    """Assemble the OS from settings. Pure construction; no async twin.

    Pure means no database is touched here. Memory is wired in ``memory_lifespan_for``, which
    runs when the server starts, not when this module is imported.
    """
    if settings.scheduler_enabled and not settings.retail_internal_service_token:
        raise RuntimeError(
            "EGPT_RETAIL_ROLE=pipelines requires EGPT_RETAIL_INTERNAL_SERVICE_TOKEN. With the scheduler on and "
            "no token, EgptOS mints one per process, and two pipelines pods behind one Service reject each "
            "other's callbacks. Inject a shared token."
        )
    if (
        settings.authorization_enabled
        and not settings.retail_auth_audience
        and not settings.retail_auth_skip_audience_check
    ):
        raise RuntimeError(
            "EGPT_RETAIL_AUTH_JWKS_FILE is set but EGPT_RETAIL_AUTH_AUDIENCE is not. verify_audience would be "
            "silently disabled and any RS256 token signed by the configured JWKS accepted regardless of its "
            "`aud` claim -- with a shared identity provider (Entra), that is every token the tenant issues to "
            "any application. Set EGPT_RETAIL_AUTH_AUDIENCE, or EGPT_RETAIL_AUTH_SKIP_AUDIENCE_CHECK=true if "
            "this deployment genuinely has no audience to check."
        )
    # G1, both halves. A provider with no tenant is as silent as no provider: memory hooks
    # take an early return when `run_context.tenant_id` is None, and nothing reports it.
    db = build_control_plane_db(settings)
    # JWT authorization is on exactly when a JWKS file is configured (R2). Locally that is an
    # offline RS256 key pair mounted from a ConfigMap; on AKS it is the identity provider's keys.
    # The scopes claim is the framework's default, `scopes`, a list. /health stays open by design
    # (DEFAULT_EXCLUDED_ROUTE_PATHS); /config requires config:read. The audience check above
    # guarantees verify_audience is true here whenever authorization is on, unless the escape
    # hatch was set -- in which case bool(None) is correctly false.
    authorization_config = (
        AuthorizationConfig(
            jwks_file=settings.retail_auth_jwks_file,
            algorithm="RS256",
            verify_audience=bool(settings.retail_auth_audience),
            audience=settings.retail_auth_audience,
        )
        if settings.authorization_enabled
        else None
    )
    agents = build_agents(settings, db)

    # The tenant middleware goes on a *base* app that EgptOS then builds on, and the reason
    # is ordering. Starlette runs the last-added middleware first, and AuthMiddleware
    # *replaces* request.state.claims with the decoded JWT (os/middleware/jwt.py:1111). Added
    # after get_app(), this middleware would be outermost: it would inject the tenant claim,
    # then auth would run and overwrite the whole claims dict, and every authenticated request
    # -- the Entra deployment shape, whose JWTs carry no tenant claim -- would lose the tenant
    # again. On the base app it is *inner*: auth runs first, then this fills the claim auth
    # left absent. That is the only order in which it works, and a unit test pins it.
    #
    # Installed only when a slug is configured. str(None) is the string "None", and a tenant
    # called "None" is a partition nobody meant to create; the lifespan refuses boot instead.
    base_app = FastAPI()
    slug = (settings.retail_customer_slug or "").strip()
    if slug:
        base_app.add_middleware(FixedTenantMiddleware, tenant_id=slug)

    # R11's and R13's real routers, and the business hierarchy (K9 over HTTP) -- the
    # non-stub, non-framework routes in the product. Mounted on the base app, same as the
    # middleware above and for the same reason: they need to exist on the app
    # AuthMiddleware wraps, which EgptOS builds on top of this one.
    base_app.include_router(playground_router)
    base_app.include_router(dashboards_router)
    base_app.include_router(business_areas_router)
    base_app.include_router(kpis_router)
    base_app.include_router(semantic_router)
    base_app.include_router(use_cases_router)
    base_app.include_router(extraction_templates_router)
    base_app.include_router(targets_router)
    base_app.include_router(monitoring_router)
    _register_exception_handlers(base_app)

    os_app = EgptOS(
        id=OS_ID,
        base_app=base_app,
        name="Enterprise GPT",
        description="Retail intelligence on EgptOS. Phase zero: one registered agent, no workflows.",
        db=db,
        agents=agents,
        workflows=build_workflows(settings, db, agents),
        registry=build_registry(),
        authorization=settings.authorization_enabled,
        authorization_config=authorization_config,
        scheduler=settings.scheduler_enabled,
        scheduler_base_url=settings.retail_scheduler_base_url,
        internal_service_token=settings.retail_internal_service_token,
        lifespan=_combined_lifespan(settings, agents),
    )

    return os_app
