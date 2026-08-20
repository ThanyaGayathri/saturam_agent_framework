"""Episodic-tier scoring, graph-link resolution, and segmentation (SM-E3 #110, SM-E4 #111,
SM-E5 #112, SM-E6 #113, SM-E7 #114, SM-E10 #117).

Re-exports the tier's scorers -- SM-E4's recency-decay module (:mod:`egpt.memory.episodic.decay`),
SM-E5's salience scorer (:mod:`egpt.memory.episodic.importance`), SM-E6's relevance module
(:mod:`egpt.memory.episodic.relevance`), SM-E7's composite module
(:mod:`egpt.memory.episodic.score`) -- SM-E10's :mod:`egpt.memory.episodic.link`, which
resolves an episode's derived graph link back against a live ``GraphSubstrate``, and SM-E3's
SM-R6 v1 inline segmenter (:mod:`egpt.memory.episodic.segment`).
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
from egpt.memory.episodic.score import (
    DEFAULT_EPISODIC_SCORE_WEIGHTS,
    EpisodicScoreWeights,
    composite_episodic_score,
)
from egpt.memory.episodic.segment import (
    DEFAULT_MAX_TURNS_PER_EPISODE,
    DEFAULT_MIN_TURNS_PER_EPISODE,
    DEFAULT_TIME_GAP_S,
    TIME_GAP_METHOD,
    TURN_CAP_METHOD,
    BoundaryDecision,
    SegmentState,
    segment_events,
    segmenter_version,
    stamp_boundaries,
)

__all__ = [
    "DEFAULT_BASE_HALF_LIFE_S",
    "DEFAULT_EPISODIC_SCORE_WEIGHTS",
    "DEFAULT_IMP_FACTOR",
    "DEFAULT_MAX_TURNS_PER_EPISODE",
    "DEFAULT_MIN_TURNS_PER_EPISODE",
    "DEFAULT_TIME_GAP_S",
    "TIME_GAP_METHOD",
    "TURN_CAP_METHOD",
    "BoundaryDecision",
    "EmbedderLike",
    "EpisodeLink",
    "EpisodicScoreWeights",
    "ImportanceScore",
    "ImportanceScorer",
    "SegmentState",
    "aepisode_relevance",
    "aresolve_episode_link",
    "composite_episodic_score",
    "cosine_relevance",
    "episode_decay_score",
    "episode_link_drift",
    "episode_relevance",
    "half_life_s",
    "recency_decay",
    "reinforce",
    "resolve_episode_link",
    "segment_events",
    "segmenter_version",
    "stamp_boundaries",
]
