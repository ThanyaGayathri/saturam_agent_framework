"""RFM segmentation (K26): recency, frequency and monetary quintiles per account and the
standard segment names. The same rule the ``rfq__rfm_segments`` KPI uses in SQL, in
Python for the scoring step, so the account context on a recommendation and the KPI on
the dashboard agree.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

import numpy as np

__all__ = ["RFM_SEGMENTS", "IMPORTANCE", "importance_of", "rfm_segments", "segment_of"]

#: How much an account's segment weighs in the ranking of what to save first: a quote
#: from an account that buys a lot and has gone quiet outranks the same money from a
#: one-off buyer. Multipliers on the value at risk, around 1.0 for the middle.
IMPORTANCE = {
    "Cannot lose them": 1.5,
    "Champions": 1.4,
    "Loyal": 1.2,
    "At risk": 1.2,
    "Potential": 1.0,
    "New": 1.0,
    "Hibernating": 0.9,
    "Lost": 0.8,
}


def importance_of(segment: object) -> float:
    return IMPORTANCE.get(str(segment or ""), 1.0)


RFM_SEGMENTS = (
    "Champions",
    "Loyal",
    "New",
    "Potential",
    "At risk",
    "Cannot lose them",
    "Hibernating",
    "Lost",
)


def segment_of(r: int, f: int, m: int) -> str:
    """The classic mapping over 1..5 quintile scores (5 = best)."""
    if r >= 4 and f >= 4:
        return "Champions"
    if r >= 3 and f >= 3:
        return "Loyal"
    if r >= 4 and f <= 2:
        return "New"
    if r <= 2 and f >= 4 and m >= 4:
        return "Cannot lose them"
    if r <= 2 and f >= 3:
        return "At risk"
    if r <= 2 and f <= 2 and m >= 3:
        return "Hibernating"
    if r <= 1:
        return "Lost"
    return "Potential"


def _quintiles(values: Sequence[float], *, higher_is_better: bool) -> List[int]:
    """Rank-based quintile scores 1..5; ties share the lower score."""
    arr = np.asarray(values, dtype=float)
    if len(arr) == 0:
        return []
    order = np.argsort(arr if higher_is_better else -arr, kind="stable")
    scores = np.empty(len(arr), dtype=int)
    for rank, index in enumerate(order):
        scores[index] = 1 + min(4, int(5 * rank / len(arr)))
    return scores.tolist()


def rfm_segments(
    accounts: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Each account ``{account_id, recency_days, frequency, monetary}`` with its scores
    and segment. Recency: fewer days is better."""
    if not accounts:
        return []
    r = _quintiles([a["recency_days"] for a in accounts], higher_is_better=False)
    f = _quintiles([a["frequency"] for a in accounts], higher_is_better=True)
    m = _quintiles([a["monetary"] for a in accounts], higher_is_better=True)
    return [
        {
            **dict(account),
            "r": r[i],
            "f": f[i],
            "m": m[i],
            "segment": segment_of(r[i], f[i], m[i]),
        }
        for i, account in enumerate(accounts)
    ]
