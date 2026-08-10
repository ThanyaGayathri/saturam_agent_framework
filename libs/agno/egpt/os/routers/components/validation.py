"""Semantic validation of component configs on the write path (EGPT-2.2).

`egpt.registry.schema` owns the schema and every check it can answer from the discovery seam
and the `Registry`, but it lives in core and so cannot name `Agent` / `Team` / `Workflow`. This
module is the other half: it is in the api layer, where importing orchestration is allowed, and
it hands core the component contract (`ComponentSpec`) that closes the dry-run hydration check.

The router calls exactly two things from here: `validate_component_config` before it persists,
and `config_validation_response` to turn a failure into a 422.
"""

from typing import Any, Dict, List, Optional

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from egpt.agent import Agent
from egpt.db.base import BaseDb
from egpt.os.schema import ConfigFieldError, ConfigValidationErrorResponse
from egpt.registry import Registry
from egpt.registry.policy import CapabilityPolicy
from egpt.registry.schema import SCHEMA_VERSION, ComponentSpec, ConfigValidationError, validate_config
from egpt.team import Team
from egpt.workflow import Workflow

# Paths coming out of the validator are config-relative ("model.provider"); the API reports them
# relative to the request body, so they read the same way as FastAPI's own `loc` entries.
_CONFIG_PATH_PREFIX = "config"


def component_spec_for(
    component_type: str,
    *,
    registry: Optional[Registry] = None,
    db: Optional[BaseDb] = None,
    links: Optional[List[Dict[str, Any]]] = None,
) -> ComponentSpec:
    """The `ComponentSpec` for a component type, built by the class that owns the contract."""
    if component_type == "agent":
        return Agent.component_spec(registry=registry)
    if component_type == "team":
        return Team.component_spec(registry=registry, db=db)
    if component_type == "workflow":
        return Workflow.component_spec(registry=registry, db=db, links=links)
    raise ValueError(f"Unknown component type '{component_type}'.")


def validate_component_config(
    config: Dict[str, Any],
    component_type: str,
    *,
    db: Optional[BaseDb] = None,
    registry: Optional[Registry] = None,
    component_id: Optional[str] = None,
    unresolved_db_id: Optional[str] = None,
    links: Optional[List[Dict[str, Any]]] = None,
    policy: Optional[CapabilityPolicy] = None,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a config and return the version-stamped dict to persist.

    `policy` and `tenant_id` add the EGPT-2.3 capability check on top of the EGPT-2.2 schema
    check. Both default to None, which skips it -- a deployment that has configured no policy
    validates exactly as it did before.

    Raises:
        ConfigValidationError: the config is not a valid definition, or names a capability this
            tenant may not use. Callers must catch this **above** any `except ValueError` arm --
            it subclasses `ValueError`, so a handler in the wrong order flattens the field-level
            errors into a plain 400 string.
    """
    spec = component_spec_for(component_type, registry=registry, db=db, links=links)
    return validate_config(
        config,
        component_type,
        registry=registry,
        spec=spec,
        unresolved_db_id=unresolved_db_id,
        self_component_id=component_id,
        policy=policy,
        tenant_id=tenant_id,
    )


def config_validation_response(error: ConfigValidationError) -> JSONResponse:
    """Render a validation failure as a 422 with one entry per offending field.

    Returned rather than raised as an `HTTPException`: that would nest the payload under
    `detail`, and an app-level exception handler would not fire for a router mounted on a bare
    `FastAPI()` (which is how the router is tested).
    """
    body = ConfigValidationErrorResponse(
        detail=f"Invalid {error.component_type} configuration: {len(error.errors)} "
        f"error{'s' if len(error.errors) != 1 else ''}.",
        error_code="CONFIG_VALIDATION_ERROR",
        schema_version=SCHEMA_VERSION,
        errors=[
            ConfigFieldError(
                path=f"{_CONFIG_PATH_PREFIX}.{item.path}" if item.path else _CONFIG_PATH_PREFIX,
                message=item.message,
                code=item.code,
            )
            for item in error.errors
        ],
    )
    return JSONResponse(status_code=422, content=jsonable_encoder(body))
