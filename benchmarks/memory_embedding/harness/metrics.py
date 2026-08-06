"""Retrieval-quality metrics for the memory-embedding benchmark (graded).

Operates on a per-query ranking (memory ids best-first) and graded qrels
(`{doc_id: grade}` with grade >= 1 relevant; higher = more relevant). Exposes
**per-query** values (not just means) so the stats layer can bootstrap confidence
intervals and run paired significance tests between models.
"""

from __future__ import annotations

import math
from typing import Dict, List, Mapping, Sequence


def _dcg(gains: Sequence[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def recall_at_k(ranking: Sequence[str], qrel: Mapping[str, int], k: int) -> float:
    relevant = {d for d, g in qrel.items() if g > 0}
    if not relevant:
        return 0.0
    hits = sum(1 for mid in ranking[:k] if mid in relevant)
    return hits / len(relevant)


def ndcg_at_k(ranking: Sequence[str], qrel: Mapping[str, int], k: int) -> float:
    if not qrel:
        return 0.0
    gains = [float(qrel.get(mid, 0)) for mid in ranking[:k]]
    ideal = sorted((float(g) for g in qrel.values()), reverse=True)[:k]
    idcg = _dcg(ideal)
    return _dcg(gains) / idcg if idcg > 0 else 0.0


def mrr_at_k(ranking: Sequence[str], qrel: Mapping[str, int], k: int) -> float:
    for i, mid in enumerate(ranking[:k]):
        if qrel.get(mid, 0) > 0:
            return 1.0 / (i + 1)
    return 0.0


def per_query(
    rankings: Dict[str, List[str]],
    qrels: Dict[str, Dict[str, int]],
    ks: Sequence[int] = (1, 5, 10),
) -> Dict[str, Dict[str, float]]:
    """Return {metric_name: {query_id: value}} across all queries.

    Only queries that have at least one relevant doc are scored (an unjudged or
    all-zero query carries no signal and would just dilute the mean).
    """
    out: Dict[str, Dict[str, float]] = {}

    def put(metric: str, qid: str, val: float) -> None:
        out.setdefault(metric, {})[qid] = val

    for qid, ranking in rankings.items():
        qrel = {d: g for d, g in qrels.get(qid, {}).items() if g > 0}
        if not qrel:
            continue
        for k in ks:
            put(f"recall@{k}", qid, recall_at_k(ranking, qrel, k))
        put("ndcg@10", qid, ndcg_at_k(ranking, qrel, 10))
        put("mrr@10", qid, mrr_at_k(ranking, qrel, 10))
    return out


def aggregate(
    rankings: Dict[str, List[str]],
    qrels: Dict[str, Dict[str, int]],
    ks: Sequence[int] = (1, 5, 10),
) -> Dict[str, float]:
    """Mean of each metric across scored queries."""
    pq = per_query(rankings, qrels, ks)
    return {name: (sum(v.values()) / len(v) if v else 0.0) for name, v in pq.items()}
