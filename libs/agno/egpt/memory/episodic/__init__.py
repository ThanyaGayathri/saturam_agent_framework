"""Episodic-tier scoring and segmentation (SM-E3 #110, SM-E4 #111).

Re-exports :mod:`egpt.memory.episodic.decay`'s public API -- the SM-R5-ratified recency-decay
scorer and its reinforcement function -- and :mod:`egpt.memory.episodic.segment`'s SM-R6 v1 inline
segmenter. Siblings scoring the same tier (importance, SM-E5 #112; relevance, SM-E6 #113) and the
module composing them (SM-E7 #114) land here alongside them.
"""

from egpt.memory.episodic.decay import (
    DEFAULT_BASE_HALF_LIFE_S,
    DEFAULT_IMP_FACTOR,
    episode_decay_score,
    half_life_s,
    recency_decay,
    reinforce,
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
    "DEFAULT_IMP_FACTOR",
    "DEFAULT_MAX_TURNS_PER_EPISODE",
    "DEFAULT_MIN_TURNS_PER_EPISODE",
    "DEFAULT_TIME_GAP_S",
    "TIME_GAP_METHOD",
    "TURN_CAP_METHOD",
    "BoundaryDecision",
    "SegmentState",
    "episode_decay_score",
    "half_life_s",
    "recency_decay",
    "reinforce",
    "segment_events",
    "segmenter_version",
    "stamp_boundaries",
]
