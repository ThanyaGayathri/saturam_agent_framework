import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Response
from fastapi.responses import JSONResponse

from egpt.components.io import import_component as import_bundle
from egpt.db.base import AsyncBaseDb, BaseDb
from egpt.db.base import ComponentType as DbComponentType
from egpt.db.component_audit import COMPONENT_AUDIT_ACTIONS
from egpt.db.utils import resolve_db_reference
from egpt.os.actor import get_actor
from egpt.os.auth import get_authentication_dependency, require_route_scopes

# Aliased on import, and that is load-bearing rather than cosmetic. `attach_routes` is a closure,
# so a handler inside it named `validate_component_config` would rebind the validator for every
# other handler in the closure -- three of them call it. Binding the module-level function to a
# name no route handler would plausibly take makes that collision structurally impossible instead
# of merely discouraged, which matters because `/components/validate` carries
# `operation_id="validate_component_config"`: the dangerous spelling sits a few lines from the
# handler, inviting a future contributor to "fix the inconsistency".
from egpt.os.routers.components.validation import config_validation_response
from egpt.os.routers.components.validation import validate_component_config as _validate_config
from egpt.os.schema import (
    BadRequestResponse,
    ComponentAuditResponse,
    ComponentBundle,
    ComponentConfigResponse,
    ComponentCreate,
    ComponentImportRequest,
    ComponentImportResponse,
    ComponentResponse,
    ComponentType,
    ComponentUpdate,
    ComponentValidateRequest,
    ComponentValidateResponse,
    ConfigCreate,
    ConfigUpdate,
    ConfigValidationErrorResponse,
    ImportResultItem,
    InternalServerErrorResponse,
    NotFoundResponse,
    PaginatedResponse,
    PaginationInfo,
    UnauthenticatedResponse,
    ValidationErrorResponse,
)
from egpt.os.settings import EgptServingSettings
from egpt.os.tenancy import get_tenant_id, require_tenant_id
from egpt.registry import Registry
from egpt.registry.io import ComponentIdConflictError, ComponentIOError, ComponentSoftDeletedError
from egpt.registry.io import export_component as export_bundle
from egpt.registry.policy import CapabilityPolicy, resolve_policy
from egpt.registry.schema import SCHEMA_VERSION, ConfigValidationError
from egpt.utils.log import log_error
from egpt.utils.string import generate_id_from_name

logger = logging.getLogger(__name__)


def _resolve_db_in_config(
    config: Dict[str, Any],
    os_db: BaseDb,
    registry: Optional[Registry] = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Resolve a config's db reference against the OS db and the registry.

    The implementation moved to ``egpt.db.utils`` in EGPT-2.8 so the definition importer can
    run the identical resolution before validating. This wrapper stays because the handlers
    below and their tests call it by this name.
    """
    return resolve_db_reference(config, os_db=os_db, registry=registry)


def get_components_router(
    os_db: Union[BaseDb, AsyncBaseDb],
    settings: EgptServingSettings = EgptServingSettings(),
    registry: Optional[Registry] = None,
    capability_policy: Optional[CapabilityPolicy] = None,
) -> APIRouter:
    """Create components router.

    The three router-level dependencies are the EGPT-2.4 hardening, in the order they are
    resolved: authenticate, then check the caller holds the route's ``components:*`` scope,
    then refuse a caller with no tenant when the deployment scopes by tenant. Both gates are
    inert unless the deployment enabled the corresponding feature, so an open instance and the
    bare-``FastAPI()`` router unit tests are unaffected.

    Args:
        capability_policy: The tenant capability policy to enforce on write (EGPT-2.3). When
            None, it is resolved from the registry and then the environment; with nothing
            configured anywhere the resulting policy is disabled and no capability check runs.
    """
    router = APIRouter(
        dependencies=[
            Depends(get_authentication_dependency(settings)),
            Depends(require_route_scopes("/components")),
            Depends(require_tenant_id),
        ],
        tags=["Components"],
        responses={
            400: {"description": "Bad Request", "model": BadRequestResponse},
            401: {"description": "Unauthorized", "model": UnauthenticatedResponse},
            403: {"description": "Forbidden", "model": UnauthenticatedResponse},
            404: {"description": "Not Found", "model": NotFoundResponse},
            409: {"description": "Conflict", "model": BadRequestResponse},
            422: {"description": "Validation Error", "model": ValidationErrorResponse},
            500: {"description": "Internal Server Error", "model": InternalServerErrorResponse},
        },
    )
    return attach_routes(router=router, os_db=os_db, registry=registry, capability_policy=capability_policy)


def attach_routes(
    router: APIRouter,
    os_db: Union[BaseDb, AsyncBaseDb],
    registry: Optional[Registry] = None,
    capability_policy: Optional[CapabilityPolicy] = None,
) -> APIRouter:
    """Attach the component routes to an existing router.

    Note that the EGPT-2.4 authentication, scope and tenancy gates live on the router built by
    :func:`get_components_router`, not here. A caller that builds its own ``APIRouter`` and
    passes it in is responsible for adding them.
    """
    # Component routes require sync database
    if not isinstance(os_db, BaseDb):
        raise ValueError("Component routes require a sync database (BaseDb), not an async database.")
    db: BaseDb = os_db  # Type narrowed after isinstance check

    # Resolved once at wiring time rather than per request: the document is operator config, and
    # re-reading it on every write would put a file read in the request path.
    policy = resolve_policy(capability_policy, registry)

    def _evict_from_hydration_cache(component_id: str) -> None:
        """Drop the component's cached config rows after a successful mutation (EGPT-2.5).

        Still the invalidation seam: the worker that processes the mutation evicts immediately;
        other workers -- and writers that bypass this router (Agent.save, the studio toolkit) --
        converge within the cache TTL. EGPT-2.6 shipped the lifecycle audit trail and left cache
        mechanics alone, so an event-driven replacement for this remains unbuilt.
        All tenants/versions/db handles for the id are dropped: mutations are rare, and a
        too-wide eviction only costs one re-read.
        """
        from egpt.registry.hydrate import ComponentConfigCache

        cache = getattr(registry, "component_config_cache", None)
        if isinstance(cache, ComponentConfigCache):
            cache.invalidate(component_id)

    def _require_component(component_id: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """The stored component, or a 404 -- the single tenant-scoped existence check.

        Every config route runs this *unconditionally* (EGPT-2.4). It used to run only when the
        request carried a non-empty config, because its original job was to supply the
        ``component_type`` the validator needs; a config-less write skipped it and fell through
        to ``upsert_config``, whose own (tenant-scoped) probe raises ``ValueError`` and surfaced
        as a **400**. So the same foreign component answered 404 or 400 depending on whether a
        config blob was attached. Hoisting it makes absent, soft-deleted and another tenant's
        component one indistinguishable 404 on every route.
        """
        component = db.get_component(component_id, tenant_id=tenant_id)
        if component is None:
            raise HTTPException(status_code=404, detail=f"Component {component_id} not found")
        return component

    @router.get(
        "/components",
        response_model=PaginatedResponse[ComponentResponse],
        response_model_exclude_none=True,
        status_code=200,
        operation_id="list_components",
        summary="List Components",
        description="Retrieve a paginated list of components with optional filtering by type.",
    )
    async def list_components(
        component_type: Optional[ComponentType] = Query(None, description="Filter by type: agent, team, workflow"),
        page: int = Query(1, ge=1, description="Page number"),
        limit: int = Query(20, ge=1, le=100, description="Items per page"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
    ) -> PaginatedResponse[ComponentResponse]:
        try:
            start_time_ms = time.time() * 1000
            offset = (page - 1) * limit

            # Exclude components whose IDs are owned by the registry
            exclude_ids = registry.get_all_component_ids() if registry else None

            components, total_count = db.list_components(
                component_type=DbComponentType(component_type.value) if component_type else None,
                limit=limit,
                offset=offset,
                exclude_component_ids=exclude_ids or None,
                tenant_id=tenant_id,
            )

            total_pages = (total_count + limit - 1) // limit if limit > 0 else 0

            return PaginatedResponse(
                data=[ComponentResponse(**c) for c in components],
                meta=PaginationInfo(
                    page=page,
                    limit=limit,
                    total_pages=total_pages,
                    total_count=total_count,
                    search_time_ms=round(time.time() * 1000 - start_time_ms, 2),
                ),
            )
        except Exception as e:
            log_error(f"Error listing components: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.post(
        "/components",
        response_model=ComponentResponse,
        response_model_exclude_none=True,
        status_code=201,
        operation_id="create_component",
        responses={422: {"description": "Config Validation Error", "model": ConfigValidationErrorResponse}},
        summary="Create Component",
        description="Create a new component (agent, team, or workflow) with initial config.",
    )
    async def create_component(
        body: ComponentCreate,
        tenant_id: Optional[str] = Depends(get_tenant_id),
        actor: Optional[str] = Depends(get_actor),
    ) -> Union[ComponentResponse, JSONResponse]:
        try:
            component_id = body.component_id
            if component_id is None:
                component_id = generate_id_from_name(body.name)

            # TODO: Create links from config

            # Prepare config - ensure it's a dict and resolve db reference
            config = body.config or {}
            config, unresolved_db_id = _resolve_db_in_config(config, db, registry)

            # An empty config is a shell component (members/steps added later), so there is
            # nothing to validate yet. Validation runs after the db reference is resolved, so
            # it sees exactly the bytes that get persisted.
            if config:
                config = _validate_config(
                    config,
                    body.component_type.value,
                    db=db,
                    registry=registry,
                    component_id=component_id,
                    unresolved_db_id=unresolved_db_id,
                    policy=policy,
                    tenant_id=tenant_id,
                )

            component, _config = db.create_component_with_config(
                component_id=component_id,
                component_type=DbComponentType(body.component_type.value),
                name=body.name,
                description=body.description,
                metadata=body.metadata,
                config=config,
                label=body.label,
                stage=body.stage or "draft",
                notes=body.notes,
                tenant_id=tenant_id,
                actor=actor,
            )

            return ComponentResponse(**component)
        # Must precede `except ValueError`: ConfigValidationError subclasses it, and the
        # ValueError arm would flatten the field-level errors into a plain 400 string.
        except ConfigValidationError as e:
            return config_validation_response(e)
        except ValueError as e:
            # An id collision answers 409 whether the id is taken inside the caller's tenant or
            # in another one, and the two bodies are byte-identical. `component_id` is the
            # global primary key (EGPT-2.1 kept `tenant_id` out of it), so a collision is
            # always observable as "not 201" -- there is no scheme that hides it. Answering
            # 409-for-mine and 404-for-theirs would therefore not conceal the collision, it
            # would only *label* it, handing a prober a cleaner ownership oracle than the
            # undifferentiated 400 this replaces.
            # Matched exactly, not by substring: the label-uniqueness ValueError raised further
            # down the same db method also contains "already exists", and a substring test
            # would relabel a duplicate `label` as an id collision -- reporting a field the
            # caller got right and discarding the one they got wrong. The reasoning above is
            # about *id* collisions only, so everything else falls through to its real message
            # (which is what the sibling version-creating routes already do).
            if str(e) == f"Component {component_id} already exists":
                raise HTTPException(status_code=409, detail=f"Component {component_id} already exists")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log_error(f"Error creating component: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    # Declared before the `/components/{component_id}` block on purpose. Starlette scores a full
    # method+path match above a path-parameter one, so a literal registered later would still
    # win today -- but only while no `POST /components/{component_id}` route exists. Registering
    # it here makes that positional rather than incidental.
    #
    # The handler is deliberately NOT named `validate_component_config`: `attach_routes` is a
    # closure, and a local of that name would rebind the module-level validator that
    # `create_component`, `create_config` and `update_config` call. The operation id carries the
    # intended name instead.
    @router.post(
        "/components/validate",
        response_model=ComponentValidateResponse,
        response_model_exclude_none=True,
        status_code=200,
        operation_id="validate_component_config",
        responses={422: {"description": "Config Validation Error", "model": ConfigValidationErrorResponse}},
        summary="Validate Component Config",
        description=(
            "Dry-run a component config through the same validator and capability policy the write "
            "routes use. Stores nothing. Intended as the pre-flight for an authoring client, so it "
            "can show field-level errors before creating a draft."
        ),
    )
    async def validate_component_definition(
        body: ComponentValidateRequest,
        tenant_id: Optional[str] = Depends(get_tenant_id),
    ) -> Union[ComponentValidateResponse, JSONResponse]:
        try:
            # Copied before resolution: `resolve_db_reference` mutates the mapping in place, and a
            # dry run has no business editing the caller's parsed body.
            config = dict(body.config or {})
            config, unresolved_db_id = _resolve_db_in_config(config, db, registry)

            # The id is derived from `name` exactly as `create_component` derives it, because
            # `component_id` is the sole input to self-reference detection: a team that lists
            # itself is only detectable once the id is known. Passing the caller's `component_id`
            # straight through made the dry run answer 200 for the config the write route then
            # 422s with `cyclic_reference` -- and on the console's default path, where the id box
            # is optional. Any divergence between these two lines is a divergence in the verdict.
            component_id = body.component_id
            if component_id is None and body.name:
                component_id = generate_id_from_name(body.name)

            # Resolution runs first for the same reason it does on the write path: a db id nobody
            # can resolve should surface as a field error at `config.db.id`, not slip through here
            # and fail on the real write.
            #
            # `links` is forwarded because `POST /components/{id}/configs` forwards it too -- that
            # is the route the console uses for every version after the first, so this is the write
            # path the dry run has to match. `POST /components` ignores links entirely (its
            # "TODO: Create links from config" is the standing asymmetry), so a first-version dry
            # run that sends links previews slightly more than that route would apply. Harmless
            # today because the console never sends them; noted so the TODO is closed on both
            # routes together rather than one drifting from the other.
            if config:
                _validate_config(
                    config,
                    body.component_type.value,
                    db=db,
                    registry=registry,
                    component_id=component_id,
                    unresolved_db_id=unresolved_db_id,
                    links=body.links,
                    policy=policy,
                    tenant_id=tenant_id,
                )

            return ComponentValidateResponse(valid=True, schema_version=SCHEMA_VERSION)
        # Must precede `except ValueError` -- see the note on `create_component`.
        except ConfigValidationError as e:
            return config_validation_response(e)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log_error(f"Error validating component config: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.get(
        "/components/{component_id}",
        response_model=ComponentResponse,
        response_model_exclude_none=True,
        status_code=200,
        operation_id="get_component",
        summary="Get Component",
        description="Retrieve a component by ID.",
    )
    async def get_component(
        component_id: str = Path(description="Component ID"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
    ) -> ComponentResponse:
        try:
            component = db.get_component(component_id, tenant_id=tenant_id)
            if component is None:
                raise HTTPException(status_code=404, detail=f"Component {component_id} not found")
            return ComponentResponse(**component)
        except HTTPException:
            raise
        except Exception as e:
            log_error(f"Error getting component: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.patch(
        "/components/{component_id}",
        response_model=ComponentResponse,
        response_model_exclude_none=True,
        status_code=200,
        operation_id="update_component",
        summary="Update Component",
        description="Partially update a component by ID.",
    )
    async def update_component(
        component_id: str = Path(description="Component ID"),
        body: ComponentUpdate = Body(description="Component fields to update"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
        actor: Optional[str] = Depends(get_actor),
    ) -> ComponentResponse:
        try:
            existing = db.get_component(component_id, tenant_id=tenant_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"Component {component_id} not found")

            # tenant_id scopes the lookup behind the upsert; it is never *changed* by an update,
            # per the BaseDb contract -- a component does not move between tenants.
            update_kwargs: Dict[str, Any] = {"component_id": component_id, "tenant_id": tenant_id, "actor": actor}
            if body.name is not None:
                update_kwargs["name"] = body.name
            if body.description is not None:
                update_kwargs["description"] = body.description
            if body.metadata is not None:
                update_kwargs["metadata"] = body.metadata
            if body.current_version is not None:
                update_kwargs["current_version"] = body.current_version
            if body.component_type is not None:
                update_kwargs["component_type"] = DbComponentType(body.component_type)

            component = db.upsert_component(**update_kwargs)
            # current_version may have moved, which changes what a version=None hydration resolves.
            _evict_from_hydration_cache(component_id)
            return ComponentResponse(**component)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log_error(f"Error updating component: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.delete(
        "/components/{component_id}",
        status_code=204,
        operation_id="delete_component",
        summary="Delete Component",
        description="Delete a component by ID.",
    )
    async def delete_component(
        component_id: str = Path(description="Component ID"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
        actor: Optional[str] = Depends(get_actor),
    ) -> None:
        try:
            deleted = db.delete_component(component_id, tenant_id=tenant_id, actor=actor)
            if not deleted:
                raise HTTPException(status_code=404, detail=f"Component {component_id} not found")
            _evict_from_hydration_cache(component_id)
        except HTTPException:
            raise
        except Exception as e:
            log_error(f"Error deleting component: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.get(
        "/components/{component_id}/configs",
        response_model=List[ComponentConfigResponse],
        response_model_exclude_none=True,
        status_code=200,
        operation_id="list_configs",
        summary="List Configs",
        description="List all configs for a component.",
    )
    async def list_configs(
        component_id: str = Path(description="Component ID"),
        include_config: bool = Query(True, description="Include full config blob"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
    ) -> List[ComponentConfigResponse]:
        try:
            # No existence check on purpose: a foreign component, a component with no configs
            # and an absent component all return [] already, so there is nothing to distinguish
            # and an extra read would buy nothing.
            configs = db.list_configs(component_id, include_config=include_config, tenant_id=tenant_id)
            return [ComponentConfigResponse(**c) for c in configs]
        except Exception as e:
            log_error(f"Error listing configs: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.post(
        "/components/{component_id}/configs",
        response_model=ComponentConfigResponse,
        response_model_exclude_none=True,
        status_code=201,
        operation_id="create_config",
        responses={422: {"description": "Config Validation Error", "model": ConfigValidationErrorResponse}},
        summary="Create Config Version",
        description="Create a new config version for a component.",
    )
    async def create_config(
        component_id: str = Path(description="Component ID"),
        body: ConfigCreate = Body(description="Config data"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
        actor: Optional[str] = Depends(get_actor),
    ) -> Union[ComponentConfigResponse, JSONResponse]:
        try:
            # Unconditional, so a config-less write on another tenant's component is the same
            # 404 as one that carries a config. See _require_component.
            component = _require_component(component_id, tenant_id)

            # Resolve db from config if present
            config_data = body.config or {}
            config_data, unresolved_db_id = _resolve_db_in_config(config_data, db, registry)

            if config_data:
                config_data = _validate_config(
                    config_data,
                    str(component["component_type"]),
                    db=db,
                    registry=registry,
                    component_id=component_id,
                    unresolved_db_id=unresolved_db_id,
                    links=body.links,
                    policy=policy,
                    tenant_id=tenant_id,
                )

            config = db.upsert_config(
                component_id=component_id,
                version=None,  # Always create new
                config=config_data,
                label=body.label,
                stage=body.stage,
                notes=body.notes,
                links=body.links,
                tenant_id=tenant_id,
                actor=actor,
            )
            # Publishing a new version moves current_version, which a cached version=None
            # (or labeled) hydration would otherwise keep serving until the TTL.
            _evict_from_hydration_cache(component_id)
            return ComponentConfigResponse(**config)
        except HTTPException:
            raise
        # Must precede `except ValueError` -- see the note in create_component.
        except ConfigValidationError as e:
            return config_validation_response(e)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log_error(f"Error creating config: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.patch(
        "/components/{component_id}/configs/{version}",
        response_model=ComponentConfigResponse,
        response_model_exclude_none=True,
        status_code=200,
        operation_id="update_config",
        responses={422: {"description": "Config Validation Error", "model": ConfigValidationErrorResponse}},
        summary="Update Draft Config",
        description="Update an existing draft config. Cannot update published configs.",
    )
    async def update_config(
        component_id: str = Path(description="Component ID"),
        version: int = Path(description="Version number"),
        body: ConfigUpdate = Body(description="Config fields to update"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
        actor: Optional[str] = Depends(get_actor),
    ) -> Union[ComponentConfigResponse, JSONResponse]:
        try:
            # Unconditional -- see create_config.
            component = _require_component(component_id, tenant_id)

            # Resolve db from config if present
            config_data = body.config
            if config_data is not None:
                config_data, unresolved_db_id = _resolve_db_in_config(config_data, db, registry)
                if config_data:
                    config_data = _validate_config(
                        config_data,
                        str(component["component_type"]),
                        db=db,
                        registry=registry,
                        component_id=component_id,
                        unresolved_db_id=unresolved_db_id,
                        links=body.links,
                        policy=policy,
                        tenant_id=tenant_id,
                    )

            config = db.upsert_config(
                component_id=component_id,
                version=version,  # Always update existing
                config=config_data,
                label=body.label,
                stage=body.stage,
                notes=body.notes,
                links=body.links,
                tenant_id=tenant_id,
                actor=actor,
            )
            # Draft edits change the row an explicit-version hydration reads; a draft->published
            # transition also moves current_version.
            _evict_from_hydration_cache(component_id)
            return ComponentConfigResponse(**config)
        except HTTPException:
            raise
        # Must precede `except ValueError` -- see the note in create_component.
        except ConfigValidationError as e:
            return config_validation_response(e)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log_error(f"Error updating config: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.get(
        "/components/{component_id}/configs/current",
        response_model=ComponentConfigResponse,
        response_model_exclude_none=True,
        status_code=200,
        operation_id="get_current_config",
        summary="Get Current Config",
        description="Get the current config version for a component.",
    )
    async def get_current_config(
        component_id: str = Path(description="Component ID"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
    ) -> ComponentConfigResponse:
        try:
            config = db.get_config(component_id, tenant_id=tenant_id)
            if config is None:
                raise HTTPException(status_code=404, detail=f"No current config for {component_id}")
            return ComponentConfigResponse(**config)
        except HTTPException:
            raise
        except Exception as e:
            log_error(f"Error getting config: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.get(
        "/components/{component_id}/configs/{version}",
        response_model=ComponentConfigResponse,
        response_model_exclude_none=True,
        status_code=200,
        operation_id="get_config",
        summary="Get Config Version",
        description="Get a specific config version by number.",
    )
    async def get_config_version(
        component_id: str = Path(description="Component ID"),
        version: int = Path(description="Version number"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
    ) -> ComponentConfigResponse:
        try:
            config = db.get_config(component_id, version=version, tenant_id=tenant_id)

            if config is None:
                raise HTTPException(status_code=404, detail=f"Config {component_id} v{version} not found")
            return ComponentConfigResponse(**config)
        except HTTPException:
            raise
        except Exception as e:
            log_error(f"Error getting config: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.delete(
        "/components/{component_id}/configs/{version}",
        status_code=204,
        operation_id="delete_config",
        summary="Delete Config Version",
        description="Delete a specific draft config version. Cannot delete published or current configs.",
    )
    async def delete_config_version(
        component_id: str = Path(description="Component ID"),
        version: int = Path(description="Version number"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
        actor: Optional[str] = Depends(get_actor),
    ) -> None:
        try:
            # Ahead of the mutation, so another tenant's component is a 404 rather than
            # whatever the db raises for the version it cannot see.
            _require_component(component_id, tenant_id)

            # Resolve version number
            deleted = db.delete_config(component_id, version=version, tenant_id=tenant_id, actor=actor)
            if not deleted:
                raise HTTPException(status_code=404, detail=f"Config {component_id} v{version} not found")
            _evict_from_hydration_cache(component_id)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log_error(f"Error deleting config: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.post(
        "/components/{component_id}/configs/{version}/set-current",
        response_model=ComponentResponse,
        response_model_exclude_none=True,
        status_code=200,
        operation_id="set_current_config",
        summary="Set Current Config Version",
        description="Set a published config version as current (for rollback).",
    )
    async def set_current_config(
        component_id: str = Path(description="Component ID"),
        version: int = Path(description="Version number"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
        actor: Optional[str] = Depends(get_actor),
    ) -> ComponentResponse:
        try:
            # Ahead of the mutation -- see delete_config_version.
            _require_component(component_id, tenant_id)

            success = db.set_current_version(component_id, version=version, tenant_id=tenant_id, actor=actor)
            if not success:
                raise HTTPException(
                    status_code=404, detail=f"Component {component_id} or config version {version} not found"
                )
            # Rollback repoints current_version -- exactly the row a version=None hydration caches.
            _evict_from_hydration_cache(component_id)

            # Fetch and return updated component
            component = db.get_component(component_id, tenant_id=tenant_id)
            if component is None:
                raise HTTPException(status_code=404, detail=f"Component {component_id} not found")

            return ComponentResponse(**component)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log_error(f"Error setting current config: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.get(
        "/components/{component_id}/audit",
        response_model=PaginatedResponse[ComponentAuditResponse],
        response_model_exclude_none=True,
        status_code=200,
        operation_id="list_component_audit",
        summary="List Component Audit History",
        description="Retrieve the append-only lifecycle audit history for a component, newest first.",
    )
    async def list_component_audit(
        component_id: str = Path(description="Component ID"),
        action: Optional[str] = Query(None, description="Filter by action, e.g. config.publish"),
        version: Optional[int] = Query(None, description="Filter by config version"),
        page: int = Query(1, ge=1, description="Page number"),
        limit: int = Query(20, ge=1, le=100, description="Items per page"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
    ) -> PaginatedResponse[ComponentAuditResponse]:
        try:
            start_time_ms = time.time() * 1000

            # The same tenant-scoped 404 gate as every other route here. Note the consequence,
            # which applies to a *soft* delete as much as a hard one: _require_component 404s
            # for both, so from the moment a component is deleted this route stops answering
            # for it -- including the "who deleted this, and when" query -- even though the rows
            # are still in the table (they deliberately have no FK to components). That is an
            # accepted trade rather than an oversight: history follows the component's lifetime
            # over HTTP, and stays reachable at the db layer via db.list_component_audit(...).
            # Forking the existence rule for this one route would be worse than the limit;
            # tracker #167 covers adding include_deleted to get_component instead.
            _require_component(component_id, tenant_id)

            if action is not None and action not in COMPONENT_AUDIT_ACTIONS:
                # Rejected rather than answered with an empty page, so a typo'd filter is not
                # mistaken for a component that nothing ever happened to.
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown action {action!r}. Valid actions: {sorted(COMPONENT_AUDIT_ACTIONS)}",
                )

            records, total_count = db.list_component_audit(
                component_id,
                version=version,
                action=action,
                limit=limit,
                offset=(page - 1) * limit,
                tenant_id=tenant_id,
            )

            total_pages = (total_count + limit - 1) // limit if limit > 0 else 0

            return PaginatedResponse(
                data=[ComponentAuditResponse(**r) for r in records],
                meta=PaginationInfo(
                    page=page,
                    limit=limit,
                    total_pages=total_pages,
                    total_count=total_count,
                    search_time_ms=round(time.time() * 1000 - start_time_ms, 2),
                ),
            )
        except HTTPException:
            raise
        except Exception as e:
            log_error(f"Error listing component audit: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.get(
        "/components/{component_id}/export",
        response_model=ComponentBundle,
        response_model_exclude_none=True,
        status_code=200,
        operation_id="export_component",
        summary="Export Component",
        description=(
            "Export a component definition as a portable JSON bundle, with the components it "
            "depends on. Connection secrets are always removed; see the `redacted` field."
        ),
    )
    async def export_component(
        response: Response,
        component_id: str = Path(description="Component ID"),
        version: Optional[int] = Query(None, description="Specific version. Defaults to the current one."),
        label: Optional[str] = Query(None, description="Labelled version. Ignored when version is given."),
        include_dependencies: bool = Query(True, description="Carry the members/steps this component needs"),
        tenant_id: Optional[str] = Depends(get_tenant_id),
    ) -> ComponentBundle:
        """Export a definition.

        There is deliberately no ``include_secrets`` here, though the library and the seed CLI
        both offer one. An operator running the CLI already holds the DSN they would be asking
        for; an HTTP caller holding `components:read` does not, and a read scope should not be
        a way to extract the database password out of a stored config.
        """
        try:
            _require_component(component_id, tenant_id)
            document = export_bundle(
                db,
                component_id,
                version=version,
                label=label,
                tenant_id=tenant_id,
                include_dependencies=include_dependencies,
            )
            response.headers["Content-Disposition"] = f'attachment; filename="{component_id}.egpt.json"'
            return ComponentBundle(**document)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log_error(f"Error exporting component {component_id}: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.post(
        "/components/import",
        response_model=ComponentImportResponse,
        response_model_exclude_none=True,
        status_code=201,
        operation_id="import_components",
        responses={422: {"description": "Config Validation Error", "model": ConfigValidationErrorResponse}},
        summary="Import Components",
        description=(
            "Import a portable definition bundle into the caller's tenant. Every config is "
            "validated before anything is written, so a bundle that fails validation leaves "
            "nothing behind."
        ),
    )
    async def import_components(
        body: ComponentImportRequest,
        tenant_id: Optional[str] = Depends(get_tenant_id),
        actor: Optional[str] = Depends(get_actor),
    ) -> Union[ComponentImportResponse, JSONResponse]:
        try:
            results = import_bundle(
                db,
                body.document,
                tenant_id=tenant_id,
                registry=registry,
                policy=policy,
                on_conflict=body.on_conflict,
                id_map=body.id_map,
                id_prefix=body.id_prefix,
                new_component_id=body.new_component_id,
                db_id=body.db_id,
                drop_db=body.drop_db,
                stage=body.stage,
                actor=actor,
            )

            # Every id the import touched, not only the ones it wrote: publishing moves
            # current_version, and a cached version=None row would keep serving the old config
            # until the TTL. A too-wide eviction only costs a re-read.
            for result in results:
                _evict_from_hydration_cache(result.component_id)

            actions = [result.action for result in results]
            return ComponentImportResponse(
                results=[ImportResultItem(**vars(result)) for result in results],
                created=actions.count("created"),
                versioned=actions.count("new_version"),
                published=actions.count("published"),
                unchanged=actions.count("unchanged") + actions.count("metadata_updated"),
                skipped=actions.count("skipped"),
            )
        # Must precede `except ValueError`, exactly as on the write routes above:
        # ConfigValidationError subclasses it and the field-level detail would flatten into a
        # plain 400 string. The two ComponentIOError subclasses below must precede their own
        # base class for the same reason -- they are the 409s.
        except ConfigValidationError as e:
            return config_validation_response(e)
        except (ComponentIdConflictError, ComponentSoftDeletedError) as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ComponentIOError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log_error(f"Error importing components: {str(e)}")
            raise HTTPException(status_code=500, detail="Internal server error")

    return router
