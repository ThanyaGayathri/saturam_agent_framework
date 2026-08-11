"""Reading the acting principal off a request (EGPT-2.6).

``AuthMiddleware`` writes ``request.state.user_id``; this module is the only thing the
component lifecycle reads it through, so "who made this change" is derived once rather than
re-derived at each call site. The sibling of :mod:`egpt.os.tenancy`, which answers *which
tenant's rows* a caller may reach; this one answers *whose name goes on the change*.

What the values are
-------------------
======================  ==========================================================
Value                   Principal
======================  ==========================================================
a JWT ``sub``           a human, from the configured ``user_id_claim``
``sa:<name>``           a service account (``ServiceAccount.principal``)
``__scheduler__``       the platform scheduler
``None``                no authenticated principal on this request
======================  ==========================================================

The first two namespaces are trustworthy because ``is_reserved_principal``
(``egpt.os.middleware.jwt``) already rejects a JWT whose subject claims one of them -- without
that, any token holder could sign their changes as a service account.

What ``None`` means
-------------------
No principal, and therefore an unattributed change. That is a legitimate steady state, not a
failure: an open instance runs without authentication at all, and the writers that bypass HTTP
entirely (``Agent.save``, the studio toolkit, a script) never carry a principal to begin with.
The change is still recorded; only the name on it is absent.

This is the one place :mod:`egpt.os.tenancy` and this module deliberately differ. Tenancy has
:func:`~egpt.os.tenancy.require_tenant_id`, because there ``None`` means *unscoped* and
unscoped is the widest possible access -- a caller lacking a tenant is more powerful than one
carrying it, so it has to be refused. An actor of ``None`` grants nothing, so there is no
equivalent gate here and adding one would only lock out deployments that never enabled
authentication.
"""

from typing import Optional

from fastapi import Request

__all__ = ["current_actor", "get_actor"]


def current_actor(request: Optional[Request]) -> Optional[str]:
    """The principal this request authenticated as, or ``None`` when it carries none.

    Non-string and empty values resolve to ``None``, mirroring
    :func:`egpt.os.tenancy.current_tenant_id`: the attribute genuinely does not exist on an
    unauthenticated request, so this is a ``getattr`` with a default rather than an attribute
    access.
    """
    if request is None:
        return None
    value = getattr(request.state, "user_id", None)
    return value if isinstance(value, str) and value else None


async def get_actor(request: Request) -> Optional[str]:
    """FastAPI dependency returning the caller's principal, or ``None``.

    Handlers declare ``actor: Optional[str] = Depends(get_actor)`` rather than taking a
    ``Request`` for this one attribute -- keeping ``Request`` out of the signature is what lets
    the components router's unit tests exercise a handler without building a request object.
    """
    return current_actor(request)
