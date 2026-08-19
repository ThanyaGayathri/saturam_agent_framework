"""Episodic-tier scoring and graph-link resolution (SM-E4 #111, SM-E5 #112, SM-E6 #113, SM-E10 #117).

Re-exports the tier's scorers -- SM-E4's recency-decay module (:mod:`egpt.memory.episodic.decay`),
SM-E5's salience scorer (:mod:`egpt.memory.episodic.importance`), SM-E6's relevance module
(:mod:`egpt.memory.episodic.relevance`) -- and SM-E10's :mod:`egpt.memory.episodic.link`, which
resolves an episode's derived graph link back against a live ``GraphSubstrate``. The module
composing the three scorers (SM-E7 #114) lands here alongside them.
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
from egpt.memory.episodic.link import (
    EpisodeLink,
    aresolve_episode_link,
    episode_link_drift,
    resolve_episode_link,
)
from egpt.memory.episodic.relevance import EmbedderLike, aepisode_relevance, cosine_relevance, episode_relevance

__all__ = [
    "DEFAULT_BASE_HALF_LIFE_S",
    "DEFAULT_IMP_FACTOR",
    "EmbedderLike",
    "EpisodeLink",
    "ImportanceScore",
    "ImportanceScorer",
    "aepisode_relevance",
    "aresolve_episode_link",
    "cosine_relevance",
    "episode_decay_score",
    "episode_link_drift",
    "episode_relevance",
    "half_life_s",
    "recency_decay",
    "reinforce",
    "resolve_episode_link",
]
