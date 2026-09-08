"""``build_agent_os`` -- pure construction, importable without assembling anything.

Split out of ``main.py`` so importing this function does not also run ``main.py``'s module-level
``settings = RetailSettings()`` / ``agent_os = build_agent_os(settings)`` / ``app = ...`` --
which construct ``PostgresDb``, the agent, the registry and the FastAPI app as an import side
effect. A test (or any other caller) that only wants ``build_agent_os`` should not need a real
environment just to import it, and should not fail to collect if one happens to be present but
incomplete (``EGPT_RETAIL_ROLE=pipelines`` with no token raises from here, not from an import).
"""

from __future__ import annotations

from egpt.os import EgptOS

from egpt_retail.app.components import build_agents, build_registry, build_workflows
from egpt_retail.app.db import build_control_plane_db
from egpt_retail.app.settings import RetailSettings

__all__ = ["OS_ID", "build_agent_os"]

OS_ID = "egpt-retail"


def build_agent_os(settings: RetailSettings) -> EgptOS:
    """Assemble the OS from settings. Pure construction; no async twin."""
    if settings.scheduler_enabled and not settings.retail_internal_service_token:
        raise RuntimeError(
            "EGPT_RETAIL_ROLE=pipelines requires EGPT_RETAIL_INTERNAL_SERVICE_TOKEN. With the scheduler on and "
            "no token, EgptOS mints one per process, and two pipelines pods behind one Service reject each "
            "other's callbacks. Inject a shared token."
        )
    db = build_control_plane_db(settings)
    return EgptOS(
        id=OS_ID,
        name="Enterprise GPT",
        description="Retail intelligence on EgptOS. Phase zero: one registered agent, no workflows.",
        db=db,
        agents=build_agents(settings, db),
        workflows=build_workflows(settings, db),
        registry=build_registry(),
        scheduler=settings.scheduler_enabled,
        scheduler_base_url=settings.retail_scheduler_base_url,
        internal_service_token=settings.retail_internal_service_token,
    )
