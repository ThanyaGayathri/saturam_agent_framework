"""Episodic-tier scoring (SM-E4 #111).

Re-exports :mod:`egpt.memory.episodic.decay`'s public API -- the SM-R5-ratified recency-decay
scorer and its reinforcement function. Siblings scoring the same tier (importance, SM-E5 #112;
relevance, SM-E6 #113) and the module composing them (SM-E7 #114) land here alongside it.
"""

from egpt.memory.episodic.decay import (
    DEFAULT_BASE_HALF_LIFE_S,
    DEFAULT_IMP_FACTOR,
    episode_decay_score,
    half_life_s,
    recency_decay,
    reinforce,
)

__all__ = [
    "DEFAULT_BASE_HALF_LIFE_S",
    "DEFAULT_IMP_FACTOR",
    "episode_decay_score",
    "half_life_s",
    "recency_decay",
    "reinforce",
]
