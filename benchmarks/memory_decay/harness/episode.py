"""Data model for the decay simulation.

The simulation runs on a synthetic *episode stream*: a set of memories created
over a simulated clock (measured in hours), plus a set of queries issued over the
same clock. Every value here is deterministic given the generator seed so runs
are bit-reproducible.

Mapping to the real schema (``db/schemas/memory.py`` ``UserMemory``):
    - ``memory_id``  -> ``UserMemory.memory_id``
    - ``created_at`` -> ``UserMemory.created_at`` (epoch seconds; here: sim hours)
    - ``importance`` -> proposed new field (not in seed schema)
    - ``valid_until``/``superseded_by`` model consolidation (ADD/UPDATE/DELETE):
      a newer version of the same fact supersedes the older one, which then
      becomes *stale* but still physically present in the store.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

# A fact version is "valid" on the half-open interval [valid_from, valid_until).
# Durable facts never expire, represented by +inf.
FOREVER = math.inf


@dataclass
class Episode:
    """One stored memory (an episode)."""

    memory_id: str
    topic_id: int
    vector: np.ndarray  # unit vector; synthetic stand-in for an embedding
    created_at: float  # simulated hours since t=0
    importance: float  # in [0, 1]; consolidation-assigned salience
    kind: str  # "durable" | "fact_version" | "ephemeral" | "noise"
    valid_from: float
    valid_until: float = FOREVER
    superseded_by: Optional[str] = None
    text: str = ""  # natural-language form; only used by the embedding relevance mode

    def is_valid_at(self, t: float) -> bool:
        """True if this episode is the currently-correct fact at time ``t``."""
        return self.valid_from <= t < self.valid_until

    def is_stale_at(self, t: float) -> bool:
        """True if this episode existed but has been superseded/expired by ``t``."""
        return t >= self.valid_until


@dataclass
class Query:
    """One retrieval issued at a point in simulated time."""

    query_id: str
    topic_id: int
    at: float  # simulated hours
    vector: np.ndarray  # unit vector; query "embedding"
    gt_ids: List[str] = field(default_factory=list)  # currently-valid memory ids
    text: str = ""  # natural-language form; only used by the embedding relevance mode


@dataclass
class Stream:
    """A full synthetic run: episodes + queries + generation metadata."""

    episodes: List[Episode]
    queries: List[Query]
    horizon_hours: float
    dim: int
    seed: int

    def episodes_by_id(self) -> dict:
        return {e.memory_id: e for e in self.episodes}
