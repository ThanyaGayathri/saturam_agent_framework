"""Episodic-tier scoring, graph-link resolution, and segmentation (SM-E3 #110, SM-E4 #111,
SM-E5 #112, SM-E6 #113, SM-E7 #114, SM-E8 #115, SM-E9 #116, SM-E10 #117).

Re-exports the tier's scorers -- SM-E4's recency-decay module (:mod:`egpt.memory.episodic.decay`),
SM-E5's salience scorer (:mod:`egpt.memory.episodic.importance`), SM-E6's relevance module
(:mod:`egpt.memory.episodic.relevance`), SM-E7's composite module
(:mod:`egpt.memory.episodic.score`), SM-E8's temporal-contiguity module
(:mod:`egpt.memory.episodic.contiguity`), SM-E9's forgetting/pruning policy
(:mod:`egpt.memory.episodic.forget`) -- SM-E10's :mod:`egpt.memory.episodic.link`, which
resolves an episode's derived graph link back against a live ``GraphSubstrate``, and SM-E3's
SM-R6 v1 inline segmenter (:mod:`egpt.memory.episodic.segment`).

``egpt.memory.episodic.forget.forget`` (the single-episode archive function) is deliberately
**not** re-exported here: importing it under the bare name ``forget`` would bind
``episodic.forget`` to the function, shadowing the submodule of the same name in this package's
own namespace -- silently breaking anything addressing the module by dotted path (e.g. a test's
``monkeypatch.setattr("egpt.memory.episodic.forget.now_epoch_s", ...)``). Import it from its
submodule directly: ``from egpt.memory.episodic.forget import forget``.
"""

from egpt.memory.episodic.contiguity import (
    DEFAULT_CONTIGUITY_BUFFER_CAPACITY,
    DEFAULT_CONTIGUITY_RADIUS,
    ContiguityBuffer,
    ContiguityNeighbor,
    temporal_neighbors,
    temporal_order_key,
)
from egpt.memory.episodic.decay import (
    DEFAULT_BASE_HALF_LIFE_S,
    DEFAULT_IMP_FACTOR,
    episode_decay_score,
    half_life_s,
    recency_decay,
    reinforce,
)
from egpt.memory.episodic.forget import (
    DEFAULT_PRUNE_THRESHOLD,
    is_prune_eligible,
    select_for_forgetting,
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
    DEFAULT_CONTIGUITY_DECAY,
    DEFAULT_CONTIGUITY_WEIGHT,
    DEFAULT_EPISODIC_SCORE_WEIGHTS,
    EpisodicScoreWeights,
    blend_contiguity,
    composite_episodic_score,
    contiguity_bonus,
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
    "DEFAULT_CONTIGUITY_BUFFER_CAPACITY",
    "DEFAULT_CONTIGUITY_DECAY",
    "DEFAULT_CONTIGUITY_RADIUS",
    "DEFAULT_CONTIGUITY_WEIGHT",
    "DEFAULT_EPISODIC_SCORE_WEIGHTS",
    "DEFAULT_IMP_FACTOR",
    "DEFAULT_MAX_TURNS_PER_EPISODE",
    "DEFAULT_MIN_TURNS_PER_EPISODE",
    "DEFAULT_PRUNE_THRESHOLD",
    "DEFAULT_TIME_GAP_S",
    "TIME_GAP_METHOD",
    "TURN_CAP_METHOD",
    "BoundaryDecision",
    "ContiguityBuffer",
    "ContiguityNeighbor",
    "EmbedderLike",
    "EpisodeLink",
    "EpisodicScoreWeights",
    "ImportanceScore",
    "ImportanceScorer",
    "SegmentState",
    "aepisode_relevance",
    "aresolve_episode_link",
    "blend_contiguity",
    "composite_episodic_score",
    "contiguity_bonus",
    "cosine_relevance",
    "episode_decay_score",
    "episode_link_drift",
    "episode_relevance",
    "half_life_s",
    "is_prune_eligible",
    "recency_decay",
    "reinforce",
    "resolve_episode_link",
    "segment_events",
    "segmenter_version",
    "select_for_forgetting",
    "stamp_boundaries",
    "temporal_neighbors",
    "temporal_order_key",
]
