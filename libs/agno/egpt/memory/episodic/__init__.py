"""Episodic-tier scoring (SM-E4 #111, SM-E5 #112).

Re-exports the tier's scorers: SM-E4's recency-decay module (:mod:`egpt.memory.episodic.decay`)
and SM-E5's salience scorer (:mod:`egpt.memory.episodic.importance`). Siblings scoring the same
tier (relevance, SM-E6 #113) and the module composing them (SM-E7 #114) land here alongside them.
"""

from egpt.memory.episodic.decay import (
    DEFAULT_BASE_HALF_LIFE_S,
    DEFAULT_IMP_FACTOR,
    episode_decay_score,
    half_life_s,
    recency_decay,
    reinforce,
)
from egpt.memory.episodic.importance import ImportanceScore, ImportanceScorer

__all__ = [
    "DEFAULT_BASE_HALF_LIFE_S",
    "DEFAULT_IMP_FACTOR",
    "ImportanceScore",
    "ImportanceScorer",
    "episode_decay_score",
    "half_life_s",
    "recency_decay",
    "reinforce",
]
