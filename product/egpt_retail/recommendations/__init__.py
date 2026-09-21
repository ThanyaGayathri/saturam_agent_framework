"""Recommendations (K26): what an analytics use case hands to Decision Intelligence.

A recommendation is the outcome of the at-risk kind -- one open quote, its risk, the drivers
behind it, and the sales play the catalogue chose for it; an alert is the same record with no
play. They live in ``retail_recommendations`` on the control plane, written by the
``recommend`` executor and read by the API, so nothing is pushed between use cases: the
consuming side pulls the open ones and writes back what it did.
"""

from egpt_retail.recommendations.catalogue import (
    PLAYS,
    Play,
    play_for,
    play_of,
)
from egpt_retail.recommendations.ddl import (
    RECOMMENDATIONS_TABLE,
    aapply_recommendations_ddl,
    apply_recommendations_ddl,
)
from egpt_retail.recommendations.store import RecommendationStore, new_recommendation_id

__all__ = [
    "PLAYS",
    "Play",
    "play_for",
    "play_of",
    "RECOMMENDATIONS_TABLE",
    "aapply_recommendations_ddl",
    "apply_recommendations_ddl",
    "RecommendationStore",
    "new_recommendation_id",
]
