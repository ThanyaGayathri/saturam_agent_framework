"""Reading the caller's tenant off a request (EGPT-2.3).

``AuthMiddleware`` writes ``request.state.tenant_id`` when an operator has configured
``AuthorizationConfig(tenant_id_claim=...)``. This module is the only thing that should read it,
so the "absent means unscoped" rule is written once rather than re-derived at each call site.

Two accessors, because the two call shapes are genuinely different: :func:`current_tenant_id`
for code that already holds a ``Request``, and :func:`get_tenant_id` as a FastAPI dependency for
a route handler that would otherwise have to take a ``Request`` purely to reach one attribute.

What ``None`` means
-------------------
``None`` is *unscoped*: no tenant claim configured, an unauthenticated request, the scheduler,
an untenanted service account, or a router mounted on a bare ``FastAPI()`` with no middleware at
all (which is how the router unit tests build their app). Downstream, ``None`` applies no tenant
predicate to a DB call and selects a capability policy's ``defaults`` block -- so a deployment
that has not opted in behaves exactly as before. Since EGPT-2.4 a service account carries the
tenant it was minted for, so it is only ``None`` for accounts minted without one.

That is also why the read is a ``getattr`` with a default rather than an attribute access: the
attribute genuinely does not exist on an unauthenticated request, and a helper that raised there
would make tenancy a hard dependency of every handler that consults it.

Failing closed (EGPT-2.4)
-------------------------
Unscoped is the *widest* tenancy, so in a deployment that has opted into tenancy a principal
resolving to ``None`` would see and edit every tenant's rows -- an escalation reached by simply
omitting the claim. :func:`require_tenant_id` refuses that on the routes it guards. It is a
router-level gate rather than a middleware change on purpose: making it global would lock out
every existing untenanted caller on every other router, which EGPT-2.7 owns sequencing.
"""

from typing import Optional

from fastapi import HTTPException, Request

__all__ = ["TENANCY_REQUIRED_DETAIL", "current_tenant_id", "get_tenant_id", "require_tenant_id", "tenancy_enabled"]

TENANCY_REQUIRED_DETAIL = "This deployment scopes components by tenant, and this credential carries no tenant identity."


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

    This is the *read*. :func:`require_tenant_id` is the *gate*, applied once at router level;
    handlers keep using this so their signatures stay ``Optional[str]`` for the untenanted
    deployments where no gate applies.
    """
    return current_tenant_id(request)


def tenancy_enabled(request: Request) -> bool:
    """Whether this deployment has opted into tenancy.

    Reads ``app.state.tenant_id_claim``, which ``EgptOS`` stamps eagerly when it installs the
    auth middleware. ``AuthMiddleware.dispatch`` also populates it lazily on the first request
    carrying a validator; the eager write is what makes the flag true before any request and
    assertable without issuing one.
    """
    return bool(getattr(request.app.state, "tenant_id_claim", None))


async def require_tenant_id(request: Request) -> Optional[str]:
    """FastAPI dependency that refuses an untenanted principal when tenancy is configured.

    403 rather than 401: the credential authenticated fine, so re-authenticating will not help.
    What it lacks is the tenant identity that authorizes it to touch tenant-scoped data.

    With no ``tenant_id_claim`` configured this is a no-op and every principal passes, which is
    exactly today's behaviour for deployments that have not opted in.
    """
    tenant_id = current_tenant_id(request)
    if tenant_id is None and tenancy_enabled(request):
        raise HTTPException(status_code=403, detail=TENANCY_REQUIRED_DETAIL)
    return tenant_id
