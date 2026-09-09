"""A configured tenant id for deployments that serve exactly one tenant (G1).

A single-tenant deployment has no identity provider sending a tenant claim, because there is
only ever one tenant. Nothing then populates ``request.state.tenant_id``, and the consequence
is not that tenancy is skipped -- it is that **memory silently does nothing**:
``_agent_memory_scope`` returns ``None`` when ``run_context.tenant_id`` is unset
(``agent/_managers.py:575``), so every run proceeds memoryless and no log line says so.

This middleware stamps a tenant id read from configuration, so a deployment that genuinely has
one tenant carries it on every request the same way a multi-tenant deployment carries a claimed
one.

**It never overrides a verified claim.** If the identity provider supplied a tenant, that value
wins and this middleware does nothing. A configured default that could shadow a real claim would
turn a misconfiguration into a cross-tenant read, which is a far worse failure than the one it
was added to fix. It is a floor, not an override.

**It stamps the claim as well as the attribute.** ``build_run_context_for_request`` does not read
``request.state.tenant_id``; it calls ``resolve_tenant_id`` over ``request.state.claims``, which
requires either a verified claim or a ``user_id`` matching the middleware-stamped identity.
Writing only the attribute would leave every ``RunContext`` with ``tenant_id=None`` and fix
nothing on the path that matters. Injecting the claim instead means the run path keeps using the
one security-reviewed resolver rather than gaining a second way to arrive at a tenant.

The ``user_id`` fallback in that resolver is also why a configured tenant matters beyond the
``None`` case: without one, a verified caller falls back to *their own user id* as the tenant, so
a single-customer deployment silently partitions into one tenant per person and the shared
memory the deployment was built around never accumulates.
"""

from typing import Any, Optional

from starlette.middleware.base import BaseHTTPMiddleware

__all__ = ["FixedTenantMiddleware"]


class FixedTenantMiddleware(BaseHTTPMiddleware):
    """Stamp a configured tenant id on every request that does not already carry one.

    Args:
        app: The ASGI application.
        tenant_id: The tenant every request belongs to -- the customer slug, from
            configuration. Must be a non-empty string; an empty one is a misconfiguration
            that would restore the silent-no-memory behaviour, so it is refused here rather
            than at the first run.
        tenant_id_claim: The claim name to inject under, matching what the auth middleware
            reads. Defaults to ``tenant_id``.

    Ordering matters: install this **after** the auth middleware in the chain, so a verified
    claim is already on the request when this runs and can be left alone.
    """

    def __init__(self, app: Any, tenant_id: str, tenant_id_claim: str = "tenant_id") -> None:
        super().__init__(app)
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError(
                "FixedTenantMiddleware requires a non-empty tenant_id. An unset one leaves "
                "every run without a tenant, and memory then does nothing without reporting it."
            )
        self.tenant_id = tenant_id.strip()
        self.tenant_id_claim = tenant_id_claim

    async def dispatch(self, request: Any, call_next: Any) -> Any:
        state = request.state
        existing: Optional[str] = getattr(state, "tenant_id", None)
        if not (isinstance(existing, str) and existing):
            state.tenant_id = self.tenant_id

        # The run path resolves through the claims, not the attribute. Only fill a claim that
        # is absent: a verified one is the identity provider's answer and outranks configuration.
        claims = getattr(state, "claims", None)
        if claims is None:
            claims = {}
            state.claims = claims
        if isinstance(claims, dict) and not claims.get(self.tenant_id_claim):
            claims[self.tenant_id_claim] = self.tenant_id

        return await call_next(request)
