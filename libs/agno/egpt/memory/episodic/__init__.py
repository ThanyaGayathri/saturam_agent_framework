"""Episodic-tier scoring and graph-link resolution (SM-E4 #111, SM-E10 #117).

Re-exports :mod:`egpt.memory.episodic.decay`'s public API -- the SM-R5-ratified recency-decay
scorer and its reinforcement function -- and :mod:`egpt.memory.episodic.link`'s, which resolves
an episode's derived graph link back against a live ``GraphSubstrate``. Siblings scoring the same
tier (importance, SM-E5 #112; relevance, SM-E6 #113) and the module composing them (SM-E7 #114)
land here alongside them.
"""

from egpt.memory.episodic.decay import (
    DEFAULT_BASE_HALF_LIFE_S,
    DEFAULT_IMP_FACTOR,
    episode_decay_score,
    half_life_s,
    recency_decay,
    reinforce,
)
from egpt.memory.episodic.link import (
    EpisodeLink,
    aresolve_episode_link,
    episode_link_drift,
    resolve_episode_link,
)

__all__ = [
    "DEFAULT_BASE_HALF_LIFE_S",
    "DEFAULT_IMP_FACTOR",
    "EpisodeLink",
    "aresolve_episode_link",
    "episode_decay_score",
    "episode_link_drift",
    "half_life_s",
    "recency_decay",
    "reinforce",
    "resolve_episode_link",
]
