"""Reading the caller's tenant off a request (EGPT-2.3).

``AuthMiddleware`` writes ``request.state.tenant_id`` when an operator has configured
``AuthorizationConfig(tenant_id_claim=...)``. This module is the only thing that should read it,
so the "absent means unscoped" rule is written once rather than re-derived at each call site.

Two accessors, because the two call shapes are genuinely different: :func:`current_tenant_id`
for code that already holds a ``Request``, and :func:`get_tenant_id` as a FastAPI dependency for
a route handler that would otherwise have to take a ``Request`` purely to reach one attribute.

What ``None`` means
-------------------
``None`` is *unscoped*, and it is the value for every pre-EGPT-2.3 path: no tenant claim
configured, an unauthenticated request, a service account, the scheduler, or a router mounted on
a bare ``FastAPI()`` with no middleware at all (which is how the router unit tests build their
app). Downstream, ``None`` applies no tenant predicate to a DB call and selects a capability
policy's ``defaults`` block -- so a deployment that has not opted in behaves exactly as before.

That is also why the read is a ``getattr`` with a default rather than an attribute access: the
attribute genuinely does not exist on an unauthenticated request, and a helper that raised there
would make tenancy a hard dependency of every handler that consults it.
"""

from typing import Optional

from fastapi import Request

__all__ = ["current_tenant_id", "get_tenant_id"]


def current_tenant_id(request: Optional[Request]) -> Optional[str]:
    """The tenant this request authenticated as, or ``None`` when it is unscoped.

    Non-string and empty values resolve to ``None``. The middleware already rejects those with a
    401, so this is belt-and-braces for a request whose state was populated by something else.
    """
    if request is None:
        return None
    value = getattr(request.state, "tenant_id", None)
    return value if isinstance(value, str) and value else None


async def get_tenant_id(request: Request) -> Optional[str]:
    """FastAPI dependency returning the caller's tenant id, or ``None``.

    Handlers declare ``tenant_id: Optional[str] = Depends(get_tenant_id)`` rather than taking a
    ``Request`` for this one attribute -- keeping ``Request`` out of the signature is what lets
    the components router's unit tests exercise a handler without building a request object.
    """
    return current_tenant_id(request)
