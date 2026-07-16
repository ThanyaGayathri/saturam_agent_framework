"""Candidate decay strategies.

All five candidates live *inside the harness* (per the SM-R5 working model);
only the chosen one is promoted to ``egpt/memory`` later in SM-C*. Each strategy
implements the same interface so the runner can treat them uniformly:

    score(memory, relevance, now)  -> float   ranking score (higher = better)
    on_access(memory, now)         -> None     reinforcement bookkeeping
    is_prunable(memory, now)       -> bool      pruning eligibility

Every public method has a sync and an ``a``-prefixed async variant, per the repo
rule that all public methods provide both.

State model (mirrors the proposed new ``UserMemory`` fields):
    - ``last_accessed_at``: sim-hours of last access; initialised to created_at.
    - ``strength``: retention parameter that grows on access (Ebbinghaus ``S``).

"Access" is defined as *injection into retrieval context* (i.e. the memory was
returned in the top-k for a query). See DECISION.md for the rationale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from harness.episode import Episode


@dataclass
class MemState:
    last_accessed_at: float
    strength: float


@dataclass
class DecayStrategy:
    """Base class: relevance-only, no decay, no pruning."""

    name: str = "base"
    # Prune when the decay factor drops below this. None => ranking-only.
    prune_threshold: Optional[float] = None
    state: Dict[str, MemState] = field(default_factory=dict)

    def reset(self) -> None:
        """Clear per-run state. Call once before each simulation run."""
        self.state = {}

    def _mem_state(self, memory: Episode) -> MemState:
        st = self.state.get(memory.memory_id)
        if st is None:
            st = MemState(
                last_accessed_at=memory.created_at, strength=self._initial_strength()
            )
            self.state[memory.memory_id] = st
        return st

    def _initial_strength(self) -> float:
        return 1.0

    def _decay_factor(self, memory: Episode, now: float) -> float:
        """Recency/retention factor in [0, 1]. Base class: no decay."""
        return 1.0

    # -- public: scoring ---------------------------------------------------
    def score(self, memory: Episode, relevance: float, now: float) -> float:
        return relevance

    async def ascore(self, memory: Episode, relevance: float, now: float) -> float:
        return self.score(memory=memory, relevance=relevance, now=now)

    # -- public: reinforcement --------------------------------------------
    def on_access(self, memory: Episode, now: float) -> None:
        return None

    async def aon_access(self, memory: Episode, now: float) -> None:
        self.on_access(memory=memory, now=now)

    # -- public: pruning ---------------------------------------------------
    def is_prunable(self, memory: Episode, now: float) -> bool:
        if self.prune_threshold is None:
            return False
        return self._decay_factor(memory=memory, now=now) < self.prune_threshold

    async def ais_prunable(self, memory: Episode, now: float) -> bool:
        return self.is_prunable(memory=memory, now=now)


@dataclass
class NoDecay(DecayStrategy):
    """Baseline: conflict-driven only. Rank by relevance; never time-prune."""

    name: str = "no_decay"


@dataclass
class ExpRecency(DecayStrategy):
    """Generative Agents: score = alpha*rel + beta*recency + gamma*importance.

    recency = base ** hours_since_last_access, reset on access.
    """

    name: str = "exp_recency"
    alpha: float = 1.0
    beta: float = 1.0
    gamma: float = 0.5
    base: float = 0.995

    def _decay_factor(self, memory: Episode, now: float) -> float:
        st = self._mem_state(memory)
        hours = max(0.0, now - st.last_accessed_at)
        return float(self.base**hours)

    def score(self, memory: Episode, relevance: float, now: float) -> float:
        recency = self._decay_factor(memory=memory, now=now)
        return (
            self.alpha * relevance
            + self.beta * recency
            + self.gamma * memory.importance
        )

    def on_access(self, memory: Episode, now: float) -> None:
        self._mem_state(memory).last_accessed_at = now


@dataclass
class TtlHalflife(DecayStrategy):
    """Fixed half-life decay with an optional hard TTL.

    recency = 0.5 ** (age_since_access / half_life). If ``ttl_hours`` is set, a
    memory older than the TTL (since access) is decayed to ~0 and prune-eligible.
    """

    name: str = "ttl_halflife"
    alpha: float = 1.0
    beta: float = 1.0
    half_life_hours: float = 24.0 * 7.0
    ttl_hours: Optional[float] = None

    def _decay_factor(self, memory: Episode, now: float) -> float:
        st = self._mem_state(memory)
        age = max(0.0, now - st.last_accessed_at)
        if self.ttl_hours is not None and age > self.ttl_hours:
            return 0.0
        return float(0.5 ** (age / self.half_life_hours))

    def score(self, memory: Episode, relevance: float, now: float) -> float:
        return self.alpha * relevance + self.beta * self._decay_factor(
            memory=memory, now=now
        )

    def on_access(self, memory: Episode, now: float) -> None:
        self._mem_state(memory).last_accessed_at = now

    def is_prunable(self, memory: Episode, now: float) -> bool:
        st = self._mem_state(memory)
        age = max(0.0, now - st.last_accessed_at)
        if self.ttl_hours is not None and age > self.ttl_hours:
            return True
        return super().is_prunable(memory=memory, now=now)


@dataclass
class ImportanceWeighted(DecayStrategy):
    """Importance-modulated half-life: salient memories decay slower.

    half_life = base_half_life * (1 + imp_factor * importance)
    recency   = 0.5 ** (age_since_access / half_life)
    """

    name: str = "importance_weighted"
    alpha: float = 1.0
    beta: float = 1.0
    base_half_life_hours: float = 24.0 * 5.0
    imp_factor: float = 4.0

    def _half_life(self, memory: Episode) -> float:
        return self.base_half_life_hours * (1.0 + self.imp_factor * memory.importance)

    def _decay_factor(self, memory: Episode, now: float) -> float:
        st = self._mem_state(memory)
        age = max(0.0, now - st.last_accessed_at)
        return float(0.5 ** (age / self._half_life(memory)))

    def score(self, memory: Episode, relevance: float, now: float) -> float:
        return self.alpha * relevance + self.beta * self._decay_factor(
            memory=memory, now=now
        )

    def on_access(self, memory: Episode, now: float) -> None:
        self._mem_state(memory).last_accessed_at = now


@dataclass
class Ebbinghaus(DecayStrategy):
    """MemoryBank: retention R = exp(-age / strength), strength grows on access.

    score = alpha*rel + beta*R + gamma*importance.
    on_access: strength += reinforce_gain and last_accessed_at reset. Repeated
    access flattens the forgetting curve (spaced-repetition style).
    """

    name: str = "ebbinghaus"
    alpha: float = 1.0
    beta: float = 1.0
    gamma: float = 0.5
    s0_hours: float = 24.0 * 5.0
    reinforce_gain_hours: float = 24.0 * 5.0

    def _initial_strength(self) -> float:
        return self.s0_hours

    def _decay_factor(self, memory: Episode, now: float) -> float:
        st = self._mem_state(memory)
        age = max(0.0, now - st.last_accessed_at)
        return float(math.exp(-age / max(st.strength, 1e-9)))

    def score(self, memory: Episode, relevance: float, now: float) -> float:
        retention = self._decay_factor(memory=memory, now=now)
        return (
            self.alpha * relevance
            + self.beta * retention
            + self.gamma * memory.importance
        )

    def on_access(self, memory: Episode, now: float) -> None:
        st = self._mem_state(memory)
        st.strength += self.reinforce_gain_hours
        st.last_accessed_at = now


def default_strategies(prune: bool = False) -> List[DecayStrategy]:
    """The five candidates with provisional default parameters.

    Args:
        prune: If True, enable a decay-based prune threshold on each decay
            strategy so pruning behaviour can be measured. ``no_decay`` never
            prunes (it is the ranking-only, conflict-driven baseline).
    """
    threshold = 0.05 if prune else None
    return [
        NoDecay(),
        ExpRecency(prune_threshold=threshold),
        TtlHalflife(
            prune_threshold=threshold, ttl_hours=(24.0 * 30.0 if prune else None)
        ),
        ImportanceWeighted(prune_threshold=threshold),
        Ebbinghaus(prune_threshold=threshold),
    ]
