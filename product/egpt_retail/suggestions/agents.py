"""The two suggestion agents, built once and routed through R8.

The first cut ran both from cookbooks with a pinned ``AzureOpenAI(...)``, which ``route_agents``
refuses on purpose: a pinned model bypasses the tier table, so a deployment could not say which
tier served the run and R15 could not attribute a score to a prompt version. Here the agents
are constructed with ``model=None`` and handed their provider by ``route_agents``, which is
what makes them the same kind of thing as every other registered agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from egpt.agent import Agent

from egpt_retail.app.settings import RetailSettings
from egpt_retail.routing.providers import build_tier_providers, route_agents
from egpt_retail.skills import kpi_suggestion as k12
from egpt_retail.skills import use_case_suggestion as k13

__all__ = [
    "CATALOGUE_PATH",
    "KPI_SUGGESTION_ID",
    "USE_CASE_SUGGESTION_ID",
    "build_suggestion_agents",
    "load_catalogue",
]

#: The ids are the routing table's keys and the skills' ``identifier`` headers. One string,
#: three places, and a test asserts they agree.
KPI_SUGGESTION_ID = "kpi_suggestion"
USE_CASE_SUGGESTION_ID = "use_case_suggestion"

#: The curated retail metric catalogue K12 prefers over its own invention.
CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "kpi" / "retail_metrics.json"


def load_catalogue(path: Path = CATALOGUE_PATH) -> List[Dict[str, Any]]:
    """The catalogue's metric entries. Shipped content, so a missing file is a packaging bug."""
    return list(json.loads(path.read_text())["metrics"])


def build_suggestion_agents(
    settings: RetailSettings,
    *,
    db: Any = None,
    existing_kpis: Sequence[str] = (),
    registry_kpis: Sequence[Any] = (),
    templates: Sequence[Any] = (),
    catalogue: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[Agent, Agent]:
    """Both agents, instructions rendered against this deployment, routed through R8.

    Pure construction apart from reading two shipped files. ``existing_kpis`` and
    ``registry_kpis`` are the live registry -- the closed sets K12 must not re-propose and K13
    may name -- and a caller with no registry in hand (``build_agents`` at import) passes none,
    getting agents whose section 6 says so rather than agents that guess.
    """
    kpi_agent = Agent(
        id=KPI_SUGGESTION_ID,
        name=KPI_SUGGESTION_ID,
        description=(
            "Proposes candidate KPIs the connected data supports; feasibility is stamped by "
            "the executor, never by the model (Lane K, K12)."
        ),
        instructions=k12.render_prompt(
            catalogue=list(catalogue if catalogue is not None else load_catalogue()),
            existing=list(existing_kpis),
        ),
        output_schema=k12.KpiSuggestionDraftSet,
        db=db,
    )
    use_case_agent = Agent(
        id=USE_CASE_SUGGESTION_ID,
        name=USE_CASE_SUGGESTION_ID,
        description=(
            "Proposes use case candidates across the four shapes; shape readiness and data "
            "feasibility are set differences computed after it answers (Lane K, K13)."
        ),
        instructions=k13.render_prompt(
            kpis=list(registry_kpis), templates=list(templates)
        ),
        output_schema=k13.UseCaseSuggestionDraftSet,
        db=db,
    )
    route_agents([kpi_agent, use_case_agent], build_tier_providers(settings))
    return kpi_agent, use_case_agent
