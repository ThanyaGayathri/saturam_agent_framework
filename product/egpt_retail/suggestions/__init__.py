"""K12 and K13 as one pass: what the connected data supports, filed for a person to adopt.

The architecture review's sequence -- *"as soon as source is connected, build the semantic
layer, and immediately KPI and use cases should be suggested"* -- is three packages and one
seam. K4 to K6 build the layer; K12 and K13 propose over it; K7 holds what they propose until
somebody accepts it. This package is the seam: it gathers what the two agents need from the
graph, the registry and the domain prose, runs them through R8's routing, and files what they
produce as proposals. ``egpt-retail suggest`` and ``egpt-retail harvest --suggest`` call it.
"""

from egpt_retail.suggestions.agents import (
    KPI_SUGGESTION_ID,
    USE_CASE_SUGGESTION_ID,
    build_suggestion_agents,
)
from egpt_retail.suggestions.context import SuggestionContext, gather_context
from egpt_retail.suggestions.run import SuggestionRun, arun_suggestions, run_suggestions

__all__ = [
    "KPI_SUGGESTION_ID",
    "USE_CASE_SUGGESTION_ID",
    "SuggestionContext",
    "SuggestionRun",
    "arun_suggestions",
    "build_suggestion_agents",
    "gather_context",
    "run_suggestions",
]
