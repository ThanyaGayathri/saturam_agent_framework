"""Metrics for the multi-hop spike.

Two families, both graded (§5):

Retrieval (labelled by the dataset, so non-circular):
  - ``recall_at_k``       — fraction of a question's gold passages present in top-k.
  - ``all_hops_hit``      — 1 if *every* gold passage is in top-k, else 0. This is
                            the discriminative multi-hop signal: you only answer a
                            2-hop question if you retrieve *both* bridge passages.

Answer quality (SQuAD-style, matching the 2Wiki official eval):
  - ``exact_match``       — normalized exact match of predicted vs gold answer.
  - ``token_f1``          — token-overlap F1 (partial credit).
  - ``cover_match``       — gold answer string appears in the prediction (lenient).

Normalization mirrors the official SQuAD/2Wiki scorer: lowercase, strip articles,
punctuation, and extra whitespace.
"""

from __future__ import annotations

import re
import string
from typing import List, Sequence


def _normalize(text: str) -> str:
    text = text.lower()
    text = "".join(ch for ch in text if ch not in set(string.punctuation))
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    text = " ".join(text.split())
    return text


# ------------------------------------------------------------------ retrieval
def recall_at_k(retrieved_titles: Sequence[str], gold_titles: Sequence[str], k: int) -> float:
    if not gold_titles:
        return 0.0
    topk = set(retrieved_titles[:k])
    hit = sum(1 for g in gold_titles if g in topk)
    return hit / len(gold_titles)


def all_hops_hit(retrieved_titles: Sequence[str], gold_titles: Sequence[str], k: int) -> float:
    if not gold_titles:
        return 0.0
    topk = set(retrieved_titles[:k])
    return 1.0 if all(g in topk for g in gold_titles) else 0.0


# ------------------------------------------------------------------- answers
def exact_match(pred: str, gold: str) -> float:
    return 1.0 if _normalize(pred) == _normalize(gold) else 0.0


def cover_match(pred: str, gold: str) -> float:
    gp, pp = _normalize(gold), _normalize(pred)
    return 1.0 if gp and gp in pp else 0.0


def token_f1(pred: str, gold: str) -> float:
    pred_toks = _normalize(pred).split()
    gold_toks = _normalize(gold).split()
    if not pred_toks or not gold_toks:
        return float(pred_toks == gold_toks)
    common: dict = {}
    for t in pred_toks:
        common[t] = common.get(t, 0) + 1
    num_same = 0
    seen: dict = {}
    for t in gold_toks:
        seen[t] = seen.get(t, 0) + 1
        if seen[t] <= common.get(t, 0):
            num_same += 1
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_toks)
    recall = num_same / len(gold_toks)
    return 2 * precision * recall / (precision + recall)


def mean(xs: Sequence[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def bootstrap_ci(xs: Sequence[float], iters: int = 2000, seed: int = 7, alpha: float = 0.05):
    """95% bootstrap CI on the mean — so close options can be called a tie honestly."""
    import random as _random

    xs = list(xs)
    if not xs:
        return (0.0, 0.0)
    rng = _random.Random(seed)
    n = len(xs)
    means: List[float] = []
    for _ in range(iters):
        sample = [xs[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int((alpha / 2) * iters)]
    hi = means[int((1 - alpha / 2) * iters)]
    return (round(lo, 4), round(hi, 4))
