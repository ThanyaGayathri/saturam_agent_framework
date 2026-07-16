"""Segmentation metrics.

Two families, both standard in the topic-segmentation literature:

- **Boundary detection** (precision / recall / F1) with a tolerance window: a
  predicted boundary counts as correct if it falls within ``tol`` events of a
  true boundary. Each true boundary is matched at most once (greedy nearest).
- **Segmentation error** (Pk and WindowDiff, Pevzner & Hearst 2002): window-based
  penalties that, unlike raw boundary F1, do not over-punish near-misses and do
  reward getting the *number* of segments roughly right. Both are in [0, 1],
  **lower is better**.

All operate on 0-indexed event positions. A boundary position ``i`` (1..N-1)
means "a new segment starts at event i".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence


@dataclass
class BoundaryMetrics:
    n_true: int
    n_pred: int
    tp: int
    precision: float
    recall: float
    f1: float
    tolerance: int


def segment_ids_from_boundaries(n_events: int, boundaries: Sequence[int]) -> List[int]:
    """Monotonic segment id per event (starts at 0, +1 at each boundary)."""
    bset = set(int(b) for b in boundaries)
    ids: List[int] = []
    seg = 0
    for i in range(n_events):
        if i in bset and i > 0:
            seg += 1
        ids.append(seg)
    return ids


def boundary_prf(true_b: Sequence[int], pred_b: Sequence[int], tol: int = 1) -> BoundaryMetrics:
    """Precision / recall / F1 of predicted boundaries with a +/- ``tol`` window.

    Greedy one-to-one matching: each predicted boundary consumes the nearest
    still-unmatched true boundary within ``tol``; unmatched predictions are false
    positives, unmatched truths are false negatives.
    """
    true_sorted = sorted(int(b) for b in true_b)
    pred_sorted = sorted(int(b) for b in pred_b)
    used = [False] * len(true_sorted)
    tp = 0
    for p in pred_sorted:
        best_j = -1
        best_d = tol + 1
        for j, t in enumerate(true_sorted):
            if used[j]:
                continue
            d = abs(p - t)
            if d <= tol and d < best_d:
                best_d = d
                best_j = j
        if best_j >= 0:
            used[best_j] = True
            tp += 1
    n_true = len(true_sorted)
    n_pred = len(pred_sorted)
    precision = tp / n_pred if n_pred else 0.0
    recall = tp / n_true if n_true else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return BoundaryMetrics(
        n_true=n_true,
        n_pred=n_pred,
        tp=tp,
        precision=precision,
        recall=recall,
        f1=f1,
        tolerance=tol,
    )


def window_size(n_events: int, n_true_boundaries: int) -> int:
    """Canonical Pk/WindowDiff window: half the average reference segment length."""
    n_segments = n_true_boundaries + 1
    avg_seg_len = n_events / n_segments if n_segments else n_events
    return max(2, min(n_events - 1, round(avg_seg_len / 2)))


def pk(ref_ids: Sequence[int], hyp_ids: Sequence[int], k: int) -> float:
    """Pk error: fraction of window endpoint pairs whose same-segment status differs."""
    n = len(ref_ids)
    if n <= k:
        return 0.0
    err = 0
    total = 0
    for i in range(n - k):
        total += 1
        same_ref = ref_ids[i] == ref_ids[i + k]
        same_hyp = hyp_ids[i] == hyp_ids[i + k]
        if same_ref != same_hyp:
            err += 1
    return err / total if total else 0.0


def window_diff(ref_ids: Sequence[int], hyp_ids: Sequence[int], k: int) -> float:
    """WindowDiff: fraction of windows where ref/hyp boundary counts disagree.

    ``ref_ids``/``hyp_ids`` are monotonic segment ids, so the number of boundaries
    inside window (i, i+k] is simply ``ids[i+k] - ids[i]``.
    """
    n = len(ref_ids)
    if n <= k:
        return 0.0
    err = 0
    total = 0
    for i in range(n - k):
        total += 1
        b_ref = ref_ids[i + k] - ref_ids[i]
        b_hyp = hyp_ids[i + k] - hyp_ids[i]
        if abs(b_ref - b_hyp) > 0:
            err += 1
    return err / total if total else 0.0
