"""A per-route scope-check dependency for the product's own routers (R11, R13).

The framework has no generic ``Depends(require_scopes(...))`` for a product-defined route
prefix today: ``egpt.os.auth.require_route_scopes`` only resolves against the framework's
own static ``get_default_scope_mappings()`` table and raises for a prefix that table does
not carry (``/playground``, ``/dashboards``), and ``AuthorizationConfig`` has no
``scope_mappings`` extension point to add one. This module is a thin, local dependency built
on the framework's own primitives -- ``request.state.scopes``/``admin_scope`` (populated by
``AuthMiddleware`` for every request, whether or not a token was presented) and
``has_required_scopes`` -- **not** the stub server's hand-rolled HS256 check
(``api/stub/server.py``), which exists only to serve the phase-zero stub and is not wired to
the real ``AuthMiddleware`` at all.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from egpt.os.scopes import has_required_scopes
from fastapi import HTTPException, Request

__all__ = ["caller_scopes", "require_scopes"]


def caller_scopes(request: Request) -> Tuple[List[str], Optional[str]]:
    """``(scopes, admin_scope)`` for the caller of ``request``, straight off
    ``AuthMiddleware``'s per-request state -- the one place that reads
    ``request.state.scopes``/``admin_scope``, so ``require_scopes`` and any other scope gate
    a product router defines (e.g. ``kpis.py``'s ``_may_read_sql``) can't drift apart on the
    ``getattr`` defaults.
    """
    user_scopes = getattr(request.state, "scopes", None) or []
    admin_scope = getattr(request.state, "admin_scope", None)
    return list(user_scopes), admin_scope


def require_scopes(*scopes: str) -> Callable[[Request], None]:
    """A FastAPI dependency factory: ``Depends(require_scopes("dashboards:write"))`` in a
    route signature, or ``dependencies=[Depends(require_scopes(...))]`` on an ``APIRouter``.

    No-ops when authorization is off (``request.state.authorization_enabled`` false, or
    absent because ``AuthMiddleware`` was never installed -- a deployment with no JWKS
    configured) -- the same posture ``RetailSettings.authorization_enabled`` already
    documents for the rest of the product: unset means "not turned on", not "deny
    everything".
    """

    def _dependency(request: Request) -> None:
        if not getattr(request.state, "authorization_enabled", False):
            return
        user_scopes, admin_scope = caller_scopes(request)
        if not has_required_scopes(user_scopes, list(scopes), admin_scope=admin_scope):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions. Required scopes: {', '.join(scopes)}.",
            )

    return _dependency
