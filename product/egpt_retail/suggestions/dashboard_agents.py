"""The dashboard composer agent, built once per context and routed through R8.

Mirrors :func:`egpt_retail.suggestions.agents.build_suggestion_agents`'s reasoning: constructed
with ``model=None`` and handed its provider by ``route_agents``, so it is served by the tier
table rather than a pinned model a deployment could not attribute a run to.
"""

from __future__ import annotations

from typing import Any, Optional

from egpt.agent import Agent

from egpt_retail.app.settings import RetailSettings
from egpt_retail.contracts.vocabulary import VocabularySet
from egpt_retail.routing.providers import build_tier_providers, route_agents
from egpt_retail.skills import dashboard_suggestion as skill

__all__ = ["DASHBOARD_SUGGESTION_ID", "build_dashboard_suggestion_agent"]

#: The routing table's key and the skill's own identifier header -- one string, both places.
DASHBOARD_SUGGESTION_ID = "dashboard_suggestion"


def build_dashboard_suggestion_agent(
    settings: RetailSettings,
    *,
    context: skill.SuggestionContext,
    db: Any = None,
    vocabularies: Optional[VocabularySet] = None,
) -> Agent:
    """The agent, instructions rendered against one run's context -- the selected metrics,
    the tables behind them and their joins -- and routed through R8. The context is the
    closed world it composes from; see the skill module's own docstring."""
    agent = Agent(
        id=DASHBOARD_SUGGESTION_ID,
        name=DASHBOARD_SUGGESTION_ID,
        description=(
            "Composes a dashboard -- charts with their SQL or metric, descriptions, a name "
            "and, where it applies, a business_area filter -- for a purpose written in the "
            "caller's own words, from the selected metrics and the semantic layer's tables "
            "behind them. Never reads a table it was not shown."
        ),
        instructions=skill.render_prompt(context=context, vocabularies=vocabularies),
        output_schema=skill.DashboardSuggestionDraft,
        db=db,
    )
    route_agents([agent], build_tier_providers(settings))
    return agent
