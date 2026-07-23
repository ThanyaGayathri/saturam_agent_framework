"""Metrics for the decay simulation.

Two families:
    - Retrieval quality (per query, then averaged): does the strategy surface the
      *currently-valid* fact and suppress *stale* superseded ones?
    - Pruning behaviour (when pruning is enabled): does it drop truly-dead
      memories without forgetting facts a later query still needs?
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class QueryMetrics:
    """Aggregated retrieval-quality metrics across all queries."""

    n_queries: int = 0
    precision_at_k: float = 0.0
    recall_at_k: float = 0.0
    ndcg_at_k: float = 0.0
    mrr: float = 0.0
    stale_hit_rate: float = 0.0  # fraction of top-k that are same-topic but stale
    # stale_hit restricted to versioned-topic queries (the only ones that can
    # structurally produce a same-topic stale hit); the headline scale otherwise
    # dilutes over durable/ephemeral queries that never can.
    stale_hit_rate_versioned: float = 0.0
    # Recall bucketed by how long ago the current fact became valid (fact calendar
    # age). Misleading for decay: a repeatedly re-accessed durable fact is old by
    # this clock but fresh to the decay signal. Kept for transparency only.
    recall_by_age_bucket: Dict[str, float] = field(default_factory=dict)
    # Recall bucketed by time since the fact was last surfaced (age-since-access) —
    # the clock decay actually acts on. This is the decay-resistance metric.
    recall_by_idle_bucket: Dict[str, float] = field(default_factory=dict)


@dataclass
class PruneMetrics:
    """Pruning behaviour metrics (only meaningful when pruning is enabled)."""

    enabled: bool = False
    n_pruned: int = 0
    prune_precision: float = 0.0  # of pruned, fraction that were truly dead
    prune_recall: float = 0.0  # of truly-dead, fraction pruned
    false_forget: int = 0  # pruned but was ground truth for a later query
    retained_final: int = 0  # store size at horizon
    retained_curve: List[float] = field(
        default_factory=list
    )  # retained count over ticks


@dataclass
class RunResult:
    strategy: str
    quality: QueryMetrics
    pruning: PruneMetrics
    k: int
    params: Dict[str, float] = field(default_factory=dict)
    # Per-query metric values keyed by query_id, for paired significance testing
    # (two strategies scored on the SAME queries -> paired bootstrap).
    per_query_recall: Dict[str, float] = field(default_factory=dict)
    per_query_ndcg: Dict[str, float] = field(default_factory=dict)


def dcg(hits: List[int]) -> float:
    """Binary-gain DCG for a ranked list of 0/1 hit flags."""
    total = 0.0
    for i, h in enumerate(hits):
        if h:
            total += 1.0 / math.log2(i + 2)
    return total


def ndcg_at_k(ranked_hit_flags: List[int], n_relevant: int, k: int) -> float:
    """nDCG@k with binary relevance."""
    if n_relevant == 0:
        return 0.0
    actual = dcg(ranked_hit_flags[:k])
    ideal = dcg([1] * min(n_relevant, k))
    if ideal == 0.0:
        return 0.0
    return actual / ideal


def age_bucket(hours: float) -> str:
    """Bucket a query's fact-age (hours since the current fact became valid)."""
    days = hours / 24.0
    if days < 1.0:
        return "0-1d"
    if days < 7.0:
        return "1-7d"
    if days < 30.0:
        return "7-30d"
    return "30d+"
