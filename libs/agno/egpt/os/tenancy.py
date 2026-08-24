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
router-level gate rather than a middleware change on purpose: making it global would lock out every
existing untenanted caller on every other router.

Two layers of one control (EGPT-2.7)
------------------------------------
This gate only covers HTTP. ``Agent.save``, the studio toolkit and any script hold the same
``BaseDb`` and reach the same rows without passing through a FastAPI dependency, so the same
refusal exists one layer down: ``egpt.db.component_scope`` makes ``tenant_id`` a required argument
on every component method, and ``BaseDb.require_tenant_id`` refuses an explicitly unscoped call.
``EgptOS`` turns that flag on for every registered db whenever ``tenant_id_claim`` is configured --
the same operator opt-in that makes :func:`tenancy_enabled` true here. Treat the two as one
control: this one decides whether a *principal* may proceed, the other whether a *query* may run.
"""

from typing import Optional

from fastapi import FastAPI, HTTPException, Request

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


def tenancy_enabled_for_app(app: FastAPI) -> bool:
    """Whether this deployment has opted into tenancy -- the one and only derivation.

    Reads ``app.state.tenant_id_claim``, which ``EgptOS`` stamps eagerly when it installs the
    auth middleware -- unconditionally, not only when a JWT key is configured, because a PAT-only
    deployment (``tenant_id_claim`` set, no JWT key source) is a real tenancy deployment: its
    service accounts carry a tenant and its untenanted callers must be refused. ``AuthMiddleware.
    dispatch`` also populates it lazily on the first request carrying a validator; the eager
    write is what makes the flag true before any request and assertable without issuing one.

    Every transport must derive the flag from here. The HTTP path goes through
    :func:`tenancy_enabled`; the WebSocket dispatcher, which has no ``Request``, calls this with
    ``websocket.app``. It must *not* be derived from ``resolve_ws_jwt_config``: that helper
    deliberately skips a ``JWTMiddleware`` entry with no key source and reports
    ``tenant_id_claim=None`` for it, which is right for deciding whether JWTs are accepted and
    wrong for deciding whether tenancy is on -- the two disagreed on exactly the PAT-only shape
    (PR #121 review), the same one-flag-two-derivations bug aicoe-tracker#174 was about.
    """
    return bool(getattr(app.state, "tenant_id_claim", None))


def tenancy_enabled(request: Request) -> bool:
    """Whether this deployment has opted into tenancy, read off a ``Request``.

    Thin wrapper over :func:`tenancy_enabled_for_app` so HTTP code keeps its ``Request``-shaped
    accessor; the decision itself lives in one place.
    """
    return tenancy_enabled_for_app(request.app)


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
