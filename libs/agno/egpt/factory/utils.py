"""Request context and exceptions for factory invocations."""

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Union

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FactoryError(Exception):
    """Base exception for factory errors. Maps to HTTP 500."""

    pass


class FactoryValidationError(FactoryError):
    """factory_input failed validation against input_schema. Maps to HTTP 400."""

    pass


class FactoryPermissionError(FactoryError):
    """Factory decided the caller is not authorized. Maps to HTTP 403."""

    pass


class FactoryContextRequired(FactoryError):
    """A factory was encountered but no RequestContext was provided."""

    pass


# ---------------------------------------------------------------------------
# Request context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustedContext:
    """Context populated by verified middleware only (e.g. JWT claims).

    Nothing the client can set directly lands here. The factory must use
    this for authorization decisions (e.g. which tools to grant).
    """

    claims: Mapping[str, Any] = field(default_factory=dict)
    scopes: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RequestContext:
    """The single object threaded into every factory call.

    Attributes:
        user_id: From form field or request.state (same precedence as today).
        session_id: From form field or request.state.
        request: Raw FastAPI Request — escape hatch for anything not plumbed through.
        input: Validated factory_input (pydantic model if input_schema was set, else dict).
        trusted: Populated by verified middleware only (request.state.*).
        tenant_id: Which tenant this request belongs to (EGPT-3.2), read by the model-provider
            seam to pick a mode and model per identity. Populated from a verified JWT claim
            when the IdP supplies one, falling back to ``user_id``. **The fallback is what
            actually runs today**: this tree has no tenant concept, so ``user_id`` is the
            de-facto tenant axis and this delivers per-user resolution wearing a
            tenant-shaped interface. It becomes true per-tenant resolution when the tenant
            column lands, with no change to any provider written against the contract.

            Threaded explicitly rather than kept in a ``ContextVar`` on purpose -- see the
            rationale in ``egpt/os/middleware/user_scope.py``. ``Agent`` dispatches tool calls
            onto a ``ThreadPoolExecutor``, and an ambient value on a tenancy boundary that
            survives an executor handoff is a cross-tenant leak.
    """

    user_id: Optional[str] = None
    session_id: Optional[str] = None
    request: Any = None  # fastapi.Request — typed as Any to avoid hard dependency at import time
    input: Optional[Union[BaseModel, Dict[str, Any]]] = None
    trusted: TrustedContext = field(default_factory=TrustedContext)
    # Trailing, with a default: every existing construction site keeps working untouched.
    tenant_id: Optional[str] = None
