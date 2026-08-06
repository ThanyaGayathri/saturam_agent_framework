"""Composite time-aware scoring (Generative-Agents style) -- option (e).

Re-scores fused candidates by a weighted blend of:

    score = w_rel * norm(relevance) + w_rec * recency(age) + w_imp * importance

- **relevance**: the fused retrieval score, min-max normalized within the candidate set.
- **recency**: exponential decay on the age between the document's timestamp and the
  query 'now' time -- `0.5 ** (age_hours / half_life_hours)` (Generative Agents used
  `0.995 ** age_hours`, i.e. a ~138h half-life; exposed as a parameter here).
- **importance**: a per-document salience in [0,1]. This is a **write-time** signal
  (Generative Agents score it with an LLM at ingestion; our SM-E7 episodic importance
  supplies it). It is emphatically NOT computed at query time -- doing so would put an
  LLM on the hot path, which the decision forbids. When no importance is supplied it
  defaults to uniform (0.5) and its weight should be set to 0, i.e. relevance+recency.

This is the artifact that answers "where does time-aware episodic scoring apply": it is
a post-fusion re-scoring stage that consumes precomputed timestamps + importance, keeping
the hot path LLM-free.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

Scored = Tuple[str, float]


def recency_weight(age_hours: float, half_life_hours: float = 138.0) -> float:
    if age_hours < 0:
        age_hours = 0.0
    return float(0.5 ** (age_hours / half_life_hours))


class CompositeScorer:
    def __init__(
        self,
        w_relevance: float = 1.0,
        w_recency: float = 1.0,
        w_importance: float = 0.0,
        half_life_hours: float = 138.0,
    ):
        self.w_relevance = w_relevance
        self.w_recency = w_recency
        self.w_importance = w_importance
        self.half_life_hours = half_life_hours
        self.name = f"composite(rel={w_relevance},rec={w_recency},imp={w_importance},hl={half_life_hours}h)"

    @staticmethod
    def _minmax(scores: Dict[str, float]) -> Dict[str, float]:
        if not scores:
            return {}
        vals = list(scores.values())
        lo, hi = min(vals), max(vals)
        span = hi - lo
        if span == 0:
            return {k: 1.0 for k in scores}
        return {k: (v - lo) / span for k, v in scores.items()}

    def rescore(
        self,
        candidates: List[Scored],
        top_k: int,
        doc_times: Optional[Dict[str, float]] = None,
        query_time: Optional[float] = None,
        importance: Optional[Dict[str, float]] = None,
    ) -> List[Scored]:
        rel_norm = self._minmax(dict(candidates))
        out: Dict[str, float] = {}
        for doc_id, _ in candidates:
            rel = rel_norm.get(doc_id, 0.0)
            if doc_times and query_time is not None and doc_id in doc_times:
                age_h = max(0.0, (query_time - doc_times[doc_id]) / 3600.0)
                rec = recency_weight(age_h, self.half_life_hours)
            else:
                rec = 0.0
            imp = importance.get(doc_id, 0.5) if importance else 0.5
            out[doc_id] = (
                self.w_relevance * rel + self.w_recency * rec + self.w_importance * imp
            )
        ranked = sorted(out.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_k]
