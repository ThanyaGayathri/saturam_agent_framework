"""Retrieval-quality metrics for the memory-embedding benchmark.

All metrics operate on a per-query ranking (memory ids ordered best-first) and
a set of relevant ids (binary relevance). We report the mean over queries.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Set


def _dcg(gains: Sequence[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def recall_at_k(ranking: Sequence[str], relevant: Set[str], k: int) -> float:
    if not relevant:
        return 0.0
    hits = sum(1 for mid in ranking[:k] if mid in relevant)
    return hits / len(relevant)


def ndcg_at_k(ranking: Sequence[str], relevant: Set[str], k: int) -> float:
    if not relevant:
        return 0.0
    gains = [1.0 if mid in relevant else 0.0 for mid in ranking[:k]]
    ideal = [1.0] * min(len(relevant), k)
    idcg = _dcg(ideal)
    return _dcg(gains) / idcg if idcg > 0 else 0.0


def mrr_at_k(ranking: Sequence[str], relevant: Set[str], k: int) -> float:
    for i, mid in enumerate(ranking[:k]):
        if mid in relevant:
            return 1.0 / (i + 1)
    return 0.0


def aggregate(
    rankings: Dict[str, List[str]],
    qrels: Dict[str, Dict[str, int]],
    ks: Sequence[int] = (1, 5, 10),
) -> Dict[str, float]:
    """Mean metrics across all queries.

    rankings: query_id -> ranked list of memory ids (best first)
    qrels:    query_id -> {memory_id: relevance}
    """
    out: Dict[str, List[float]] = {}
    for qid, ranking in rankings.items():
        relevant = {mid for mid, rel in qrels.get(qid, {}).items() if rel > 0}
        for k in ks:
            out.setdefault(f"recall@{k}", []).append(recall_at_k(ranking, relevant, k))
        out.setdefault("ndcg@10", []).append(ndcg_at_k(ranking, relevant, 10))
        out.setdefault("mrr@10", []).append(mrr_at_k(ranking, relevant, 10))

    return {
        name: (sum(vals) / len(vals) if vals else 0.0) for name, vals in out.items()
    }
