"""The EgptOS assembly -- the uvicorn import target ``egpt_retail.app.main:app``.

Module-level ``agent_os`` and ``app``, because ``EgptOS.serve`` takes an import string and
``uvicorn --workers`` needs a module attribute, not a factory. The role is read from the
environment at import time: ``EGPT_RETAIL_ROLE=api`` serves requests, ``pipelines`` serves the
same routes and also runs the scheduler poller. That is the only difference between the two
Deployments, and it is a contention choice rather than a correctness one -- the poller claims
schedules with ``FOR UPDATE SKIP LOCKED`` and could run on every API pod -- made so nightly gold
materialisation (R5) never contends with interactive traffic.

Everything below is construction. ``serve()`` is what runs it (``entrypoints/api.py``,
``entrypoints/pipelines.py``); the lifespans it starts are the framework's own.
"""

from __future__ import annotations

from egpt.os import EgptOS
from egpt.os.config import AuthorizationConfig

from egpt_retail.app.components import build_agents, build_registry, build_workflows
from egpt_retail.app.db import build_control_plane_db
from egpt_retail.app.settings import RetailSettings

__all__ = ["OS_ID", "build_agent_os", "settings", "agent_os", "app"]

OS_ID = "egpt-retail"


def build_agent_os(settings: RetailSettings) -> EgptOS:
    """Assemble the OS from settings. Pure construction; no async twin."""
    if settings.scheduler_enabled and not settings.retail_internal_service_token:
        raise RuntimeError(
            "EGPT_RETAIL_ROLE=pipelines requires EGPT_RETAIL_INTERNAL_SERVICE_TOKEN. With the scheduler on and "
            "no token, EgptOS mints one per process, and two pipelines pods behind one Service reject each "
            "other's callbacks. Inject a shared token."
        )
    if (
        settings.authorization_enabled
        and not settings.retail_auth_audience
        and not settings.retail_auth_skip_audience_check
    ):
        raise RuntimeError(
            "EGPT_RETAIL_AUTH_JWKS_FILE is set but EGPT_RETAIL_AUTH_AUDIENCE is not. verify_audience would be "
            "silently disabled and any RS256 token signed by the configured JWKS accepted regardless of its "
            "`aud` claim -- with a shared identity provider (Entra), that is every token the tenant issues to "
            "any application. Set EGPT_RETAIL_AUTH_AUDIENCE, or EGPT_RETAIL_AUTH_SKIP_AUDIENCE_CHECK=true if "
            "this deployment genuinely has no audience to check."
        )
    db = build_control_plane_db(settings)
    # JWT authorization is on exactly when a JWKS file is configured (R2). Locally that is an
    # offline RS256 key pair mounted from a ConfigMap; on AKS it is the identity provider's keys.
    # The scopes claim is the framework's default, `scopes`, a list. /health stays open by design
    # (DEFAULT_EXCLUDED_ROUTE_PATHS); /config requires config:read. The audience check above
    # guarantees verify_audience is true here whenever authorization is on, unless the escape
    # hatch was set -- in which case bool(None) is correctly false.
    authorization_config = (
        AuthorizationConfig(
            jwks_file=settings.retail_auth_jwks_file,
            algorithm="RS256",
            verify_audience=bool(settings.retail_auth_audience),
            audience=settings.retail_auth_audience,
        )
        if settings.authorization_enabled
        else None
    )
    return EgptOS(
        id=OS_ID,
        name="Enterprise GPT",
        description="Retail intelligence on EgptOS. Phase zero: one registered agent, no workflows.",
        db=db,
        agents=build_agents(settings, db),
        workflows=build_workflows(settings, db),
        registry=build_registry(),
        authorization=settings.authorization_enabled,
        authorization_config=authorization_config,
        scheduler=settings.scheduler_enabled,
        scheduler_base_url=settings.retail_scheduler_base_url,
        internal_service_token=settings.retail_internal_service_token,
    )


settings = RetailSettings()
agent_os = build_agent_os(settings)
app = agent_os.get_app()
