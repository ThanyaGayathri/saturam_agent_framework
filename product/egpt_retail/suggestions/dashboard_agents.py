"""The dashboard suggestion agent, built once and routed through R8.

Mirrors :func:`egpt_retail.suggestions.agents.build_suggestion_agents`'s reasoning: constructed
with ``model=None`` and handed its provider by ``route_agents``, so it is served by the tier
table rather than a pinned model a deployment could not attribute a run to.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from egpt.agent import Agent

from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.kpi import KpiDefinition
from egpt_retail.contracts.vocabulary import VocabularySet
from egpt_retail.routing.providers import build_tier_providers, route_agents
from egpt_retail.skills import dashboard_suggestion as skill

__all__ = ["DASHBOARD_SUGGESTION_ID", "build_dashboard_suggestion_agent"]

#: The routing table's key and the skill's own identifier header -- one string, both places.
DASHBOARD_SUGGESTION_ID = "dashboard_suggestion"


def build_dashboard_suggestion_agent(
    settings: RetailSettings,
    *,
    db: Any = None,
    ready_kpis: Sequence[KpiDefinition] = (),
    vocabularies: Optional[VocabularySet] = None,
) -> Agent:
    """The agent, instructions rendered against this deployment's ready KPI catalogue, routed
    through R8. ``ready_kpis`` is the closed set it may select from -- see the skill module's
    own docstring for why that, not invention, is this agent's whole job."""
    agent = Agent(
        id=DASHBOARD_SUGGESTION_ID,
        name=DASHBOARD_SUGGESTION_ID,
        description=(
            "Proposes a dashboard configuration -- metrics and, where it applies, a "
            "business_area filter -- for a purpose written in the caller's own words. "
            "Selects only from this deployment's own ready KPIs; never invents one."
        ),
        instructions=skill.render_prompt(kpis=ready_kpis, vocabularies=vocabularies),
        output_schema=skill.DashboardSuggestionDraft,
        db=db,
    )
    route_agents([agent], build_tier_providers(settings))
    return agent
