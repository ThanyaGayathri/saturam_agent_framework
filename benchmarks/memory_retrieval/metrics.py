"""Retrieval-quality metrics + noise handling for SM-R8.

Quality metrics (nDCG@k, recall@k, MRR) are computed with **pytrec_eval** -- the
standard TREC evaluation library BEIR itself uses -- so the numbers are directly
comparable to published BEIR results and we are not hand-rolling a metric.

Because two fusion/rerank options often differ by only a few points, this module
also provides the noise machinery the decision needs:

- `mean_ci`: bootstrap 95% CI on a metric's mean across queries.
- `paired_bootstrap`: paired bootstrap on the per-query delta between two runs,
  returning the delta CI and a two-sided p-value. Options are only *ranked* when
  this says the gap clears noise; otherwise they are reported as a tie.

A "run" is `{qid: {doc_id: score}}`, and a "result" pools per-query scores across
all `RetrievalTask`s in a dataset (BEIR = 1 task, LongMemEval = 500 tasks), keyed
by a global `task_id::qid` so paired comparisons align query-for-query.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pytrec_eval

# k values reported. recall/ndcg at retrieval depth + final depth.
NDCG_KS = (10,)
RECALL_KS = (10, 50, 100)
DEFAULT_MEASURES = {
    f"ndcg_cut.{','.join(str(k) for k in NDCG_KS)}",
    f"recall.{','.join(str(k) for k in RECALL_KS)}",
    "recip_rank",
}


def _pytrec_keys() -> List[str]:
    keys = [f"ndcg_cut_{k}" for k in NDCG_KS]
    keys += [f"recall_{k}" for k in RECALL_KS]
    keys += ["recip_rank"]
    return keys


@dataclass
class MetricResult:
    """Per-query metric values pooled across a dataset's tasks.

    per_query[metric] is an ordered dict {global_qid: value}; the ordering is
    stable across runs so paired comparisons line up.
    """

    per_query: Dict[str, Dict[str, float]]

    @property
    def metrics(self) -> List[str]:
        return list(self.per_query.keys())

    def mean(self, metric: str) -> float:
        vals = list(self.per_query[metric].values())
        return float(np.mean(vals)) if vals else 0.0

    def n(self) -> int:
        any_metric = next(iter(self.per_query.values()), {})
        return len(any_metric)


def evaluate_task(
    qrels: Dict[str, Dict[str, int]],
    run: Dict[str, Dict[str, float]],
    task_id: str,
) -> Dict[str, Dict[str, float]]:
    """Evaluate one task; return {metric: {task_id::qid: value}}."""
    # pytrec_eval requires int relevance and non-empty runs per judged query.
    ev = pytrec_eval.RelevanceEvaluator(qrels, DEFAULT_MEASURES)
    scored = ev.evaluate(run)
    out: Dict[str, Dict[str, float]] = {k: {} for k in _pytrec_keys()}
    for qid, md in scored.items():
        gid = f"{task_id}::{qid}"
        for k in _pytrec_keys():
            out[k][gid] = float(md.get(k, 0.0))
    return out


def evaluate(
    dataset, run_by_task: Dict[str, Dict[str, Dict[str, float]]]
) -> MetricResult:
    """Pool per-query metrics across all tasks.

    run_by_task maps task_id -> run ({qid: {doc_id: score}}).
    """
    pooled: Dict[str, Dict[str, float]] = {k: {} for k in _pytrec_keys()}
    for task in dataset.tasks:
        run = run_by_task.get(task.task_id, {})
        if not run:
            continue
        part = evaluate_task(task.qrels, run, task.task_id)
        for k in pooled:
            pooled[k].update(part[k])
    return MetricResult(per_query=pooled)


# --------------------------------------------------------------------------- #
# Noise / significance
# --------------------------------------------------------------------------- #
def mean_ci(
    values: List[float], n_boot: int = 10000, seed: int = 7, alpha: float = 0.05
) -> Tuple[float, float, float]:
    """Bootstrap (mean, lo, hi) at the given alpha."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return 0.0, 0.0, 0.0
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(n_boot, arr.size))
    boot = arr[idx].mean(axis=1)
    lo, hi = np.quantile(boot, [alpha / 2, 1 - alpha / 2])
    return float(arr.mean()), float(lo), float(hi)


def paired_bootstrap(
    a: Dict[str, float],
    b: Dict[str, float],
    n_boot: int = 10000,
    seed: int = 7,
    alpha: float = 0.05,
) -> Dict[str, float]:
    """Paired bootstrap on per-query (a - b).

    a, b are {global_qid: value}; only shared qids are compared. Returns the mean
    delta, its CI, a two-sided bootstrap p-value, and n. `significant` is True when
    the CI excludes 0 -- i.e. the gap clears noise and the options may be ranked.
    """
    shared = [q for q in a if q in b]
    if not shared:
        return {
            "delta": 0.0,
            "lo": 0.0,
            "hi": 0.0,
            "p_value": 1.0,
            "n": 0,
            "significant": 0.0,
        }
    d = np.array([a[q] - b[q] for q in shared], dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, d.size, size=(n_boot, d.size))
    boot = d[idx].mean(axis=1)
    lo, hi = np.quantile(boot, [alpha / 2, 1 - alpha / 2])
    # Two-sided p-value: how much of the bootstrap delta distribution sits on the
    # far side of 0. A degenerate (all-equal) distribution yields p=1.0 (clear tie).
    prop_ge = float(np.mean(boot >= 0.0))
    prop_le = float(np.mean(boot <= 0.0))
    p = min(1.0, 2.0 * min(prop_ge, prop_le))
    if p < 1.0:
        p = max(p, 1.0 / n_boot)
    significant = 1.0 if (lo > 0.0 or hi < 0.0) else 0.0
    return {
        "delta": float(d.mean()),
        "lo": float(lo),
        "hi": float(hi),
        "p_value": p,
        "n": float(len(shared)),
        "significant": significant,
    }
