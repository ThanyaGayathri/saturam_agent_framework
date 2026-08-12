"""Semantic validation of component configs on the write path (EGPT-2.2).

The validator itself lives in two places. `egpt.registry.schema` is core: it owns the schema
and every check it can answer from the discovery seam and the `Registry`, without naming
`Agent` / `Team` / `Workflow`. `egpt.components.spec` is orchestration: it hands core the
component contract (`ComponentSpec`) that closes the dry-run hydration check.

What is left here is the api-layer half -- rendering a failure as an HTTP response. The two
validation functions are re-exported so the router (and its tests) keep importing them from
this module; they moved out in EGPT-2.8 because the definition importer needs them too, and
importing anything under `egpt.os` drags in FastAPI.
"""

from typing import Any, Dict, List, Optional  # noqa: F401  (kept for the re-exported signatures)

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from egpt.components.spec import component_spec_for, validate_component_config
from egpt.os.schema import ConfigFieldError, ConfigValidationErrorResponse
from egpt.registry.schema import SCHEMA_VERSION, ConfigValidationError

__all__ = ["component_spec_for", "config_validation_response", "validate_component_config"]

# Paths coming out of the validator are config-relative ("model.provider"); the API reports them
# relative to the request body, so they read the same way as FastAPI's own `loc` entries.
_CONFIG_PATH_PREFIX = "config"


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
