"""Fusion strategies for combining the dense and PPR ranked lists.

**Ported deliberately from SM-R8** (`benchmarks/memory_retrieval/fusion.py`) rather than
reimplemented, so that SM-R9's dense+PPR result is measured with the *same* fusion code
whose 2-leg behaviour SM-R8 ratified. SM-R8 chose **weighted / relative-score fusion** for
the 2-leg case and explicitly declined to adopt RRF automatically once a graph leg joins;
this module lets SM-R9 measure that question directly instead of assuming an answer
(PR #15 review).

- ``RRFFusion`` — Reciprocal Rank Fusion. Uses only each leg's *rank*, so it is immune to
  incomparable score scales, but it also throws away margin information: a leg that is
  confidently right and a leg that is barely right cast the same vote.
- ``WeightedFusion`` — min-max normalize each leg's scores to [0,1], then weighted sum.
  Keeps margin information at the cost of needing comparable *shapes* after normalization.

The scale mismatch is stark in SM-R9 and is the whole reason this matters: the dense leg
emits cosine similarities (here ~0.15–0.87, fairly flat) while the PPR leg emits stationary
probabilities (tiny, extremely skewed — the top node can carry orders of magnitude more mass
than the fifth). Rank-only fusion erases that difference; min-max fusion preserves it.
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
    """Reciprocal Rank Fusion — SM-R9's original (and prototype-default) fuse."""

    def __init__(self, k: int = 60) -> None:
        self.k = k
        self.name = f"rrf(k={k})"

    def fuse(self, ranked_lists: List[List[Scored]], top_k: int) -> List[Scored]:
        acc: Dict[str, float] = {}
        for lst in ranked_lists:
            for rank, (doc_id, _score) in enumerate(lst, start=1):
                acc[doc_id] = acc.get(doc_id, 0.0) + 1.0 / (self.k + rank)
        return sorted(acc.items(), key=lambda kv: kv[1], reverse=True)[:top_k]


class WeightedFusion(Fusion):
    """Min-max normalize each list, then weighted sum — SM-R8's ratified 2-leg method.

    ``weights[i]`` applies to ``ranked_lists[i]``. For the 2-leg case pass
    ``vector_weight`` and the second leg receives ``1 - vector_weight``, mirroring
    pgvector's ``vector_score_weight``.
    """

    def __init__(self, weights: Optional[List[float]] = None, vector_weight: float = 0.5) -> None:
        self.weights = weights
        self.vector_weight = vector_weight
        self.name = f"weighted(w={weights if weights is not None else vector_weight})"

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
        return sorted(acc.items(), key=lambda kv: kv[1], reverse=True)[:top_k]


def build_fusion(kind: str, vector_weight: float = 0.5, rrf_k: int = 60) -> Fusion:
    if kind == "rrf":
        return RRFFusion(k=rrf_k)
    if kind == "weighted":
        return WeightedFusion(vector_weight=vector_weight)
    raise ValueError(f"unknown fusion {kind!r} (expected rrf|weighted)")
