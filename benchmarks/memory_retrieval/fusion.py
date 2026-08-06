"""Fusion abstraction: combine several retrievers' ranked lists into one.

Two adapters cover the decision:

- `RRFFusion` -- Reciprocal Rank Fusion (rank-based, k=60 default). Uses only the
  *rank* each retriever assigns, so it is immune to incomparable score scales
  (BM25 unbounded vs cosine vs, later, graph distance). This is the option the
  research (§3) recommends once a graph retriever joins.
- `WeightedFusion` -- relative-score fusion: min-max normalize each retriever's
  scores to [0,1] and take a weighted sum. This reproduces pgvector's current
  `vector_score_weight` behaviour (`hybrid_search` normalizes text rank and weights
  it against cosine) in a store-agnostic form, so option (c) is measured faithfully.

There is no RRF in egpt core today (only pgvector's weighted SQL fusion), so this
`Fusion` seam is new benchmark code -- and a concrete handoff for the implementation
task (SM-H2/H3) to fold into a `SearchType.rrf` / fusion strategy. See DECISION.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

Scored = Tuple[str, float]


class Fusion(ABC):
    name: str = "fusion"

    @abstractmethod
    def fuse(self, ranked_lists: List[List[Scored]], top_k: int) -> List[Scored]: ...


class RRFFusion(Fusion):
    def __init__(self, k: int = 60):
        self.k = k
        self.name = f"rrf(k={k})"

    def fuse(self, ranked_lists: List[List[Scored]], top_k: int) -> List[Scored]:
        acc: Dict[str, float] = {}
        for lst in ranked_lists:
            for rank, (doc_id, _score) in enumerate(lst, start=1):
                acc[doc_id] = acc.get(doc_id, 0.0) + 1.0 / (self.k + rank)
        ranked = sorted(acc.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_k]


class WeightedFusion(Fusion):
    """Min-max normalize each list, then weighted sum (pgvector-style).

    weights[i] applies to ranked_lists[i]. For the 2-leg dense+sparse case, pass
    weights=[vector_weight, 1 - vector_weight] to mirror pgvector's
    `vector_score_weight`.
    """

    def __init__(
        self, weights: Optional[List[float]] = None, vector_weight: float = 0.5
    ):
        self.weights = weights
        self.vector_weight = vector_weight
        self.name = f"weighted(w={weights or vector_weight})"

    @staticmethod
    def _minmax(lst: List[Scored]) -> Dict[str, float]:
        if not lst:
            return {}
        scores = [s for _, s in lst]
        lo, hi = min(scores), max(scores)
        span = hi - lo
        if span == 0:
            return {d: 1.0 for d, _ in lst}
        return {d: (s - lo) / span for d, s in lst}

    def fuse(self, ranked_lists: List[List[Scored]], top_k: int) -> List[Scored]:
        n = len(ranked_lists)
        if self.weights is not None:
            weights = self.weights
        elif n == 2:
            weights = [self.vector_weight, 1.0 - self.vector_weight]
        else:
            weights = [1.0 / n] * n
        acc: Dict[str, float] = {}
        for w, lst in zip(weights, ranked_lists):
            for doc_id, norm in self._minmax(lst).items():
                acc[doc_id] = acc.get(doc_id, 0.0) + w * norm
        ranked = sorted(acc.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_k]
