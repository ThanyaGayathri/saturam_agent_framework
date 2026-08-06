"""Statistical significance for the memory-embedding benchmark.

PR #5's weakness was ranking models whose score gaps were within noise. This
module quantifies that noise:

  - `bootstrap_ci`  — a 95% confidence interval for a model's mean metric, by
    resampling queries with replacement.
  - `paired_bootstrap` — for two models scored on the SAME queries, the mean
    per-query difference, its CI, and a two-sided p-value for "difference != 0".
    Paired (same queries) cancels query-difficulty variance, so it is far more
    sensitive than comparing two independent CIs.
  - `holm_adjust` — Holm-Bonferroni step-down correction over the FAMILY of
    pairwise p-values a decision leans on, so a "significant" verdict controls the
    family-wise error rate rather than being an uncorrected single-comparison p.

numpy only; deterministic given `seed`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping

import numpy as np


@dataclass
class CI:
    mean: float
    lo: float
    hi: float
    n: int


def bootstrap_ci(
    values: Mapping[str, float], iters: int = 10000, seed: int = 20, alpha: float = 0.05
) -> CI:
    arr = np.array(list(values.values()), dtype=np.float64)
    n = len(arr)
    if n == 0:
        return CI(0.0, 0.0, 0.0, 0)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(iters, n))
    means = arr[idx].mean(axis=1)
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return CI(float(arr.mean()), float(lo), float(hi), n)


@dataclass
class PairedResult:
    delta: float  # mean(a) - mean(b) over shared queries
    lo: float
    hi: float
    p_value: float  # two-sided, H0: delta == 0
    n: int
    significant: bool  # at alpha


def paired_bootstrap(
    a: Mapping[str, float],
    b: Mapping[str, float],
    iters: int = 10000,
    seed: int = 21,
    alpha: float = 0.05,
) -> PairedResult:
    shared = sorted(set(a) & set(b))
    diffs = np.array([a[q] - b[q] for q in shared], dtype=np.float64)
    n = len(diffs)
    if n == 0:
        return PairedResult(0.0, 0.0, 0.0, 1.0, 0, False)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(iters, n))
    boot = diffs[idx].mean(axis=1)
    lo, hi = np.percentile(boot, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    # two-sided bootstrap p for H0 mean-diff == 0. Use both tail masses directly
    # (not 1 - prop_le0) so bootstrap mass sitting exactly at 0 — e.g. two
    # identical models where every diff is 0 — yields p = 1.0, not p = 0.0.
    prop_le0 = float(np.mean(boot <= 0.0))
    prop_ge0 = float(np.mean(boot >= 0.0))
    p = min(1.0, 2.0 * min(prop_le0, prop_ge0))
    return PairedResult(float(diffs.mean()), float(lo), float(hi), p, n, p < alpha)


def rank_with_ties(
    model_pq: Dict[str, Mapping[str, float]],
    metric_name: str = "ndcg@10",
    iters: int = 10000,
    seed: int = 22,
    alpha: float = 0.05,
) -> List[Dict]:
    """Order models by mean metric and mark which neighbours are statistical ties.

    `model_pq[model]` is that model's per-query values for the metric. Returns a
    list of rows (best first) with mean, CI, and whether the gap to the NEXT model
    is significant (paired). A non-significant gap = a tie the decision must not
    over-read.
    """
    means = {m: (sum(v.values()) / len(v) if v else 0.0) for m, v in model_pq.items()}
    order = sorted(means, key=lambda m: means[m], reverse=True)
    rows: List[Dict] = []
    for i, m in enumerate(order):
        ci = bootstrap_ci(model_pq[m], iters=iters, seed=seed, alpha=alpha)
        row = {
            "model": m,
            "mean": round(ci.mean, 4),
            "ci_lo": round(ci.lo, 4),
            "ci_hi": round(ci.hi, 4),
            "n": ci.n,
        }
        if i + 1 < len(order):
            pr = paired_bootstrap(
                model_pq[m], model_pq[order[i + 1]], iters=iters, seed=seed, alpha=alpha
            )
            row["vs_next"] = order[i + 1]
            row["delta_next"] = round(pr.delta, 4)
            row["p_next"] = round(pr.p_value, 4)
            row["tie_with_next"] = not pr.significant
        rows.append(row)
    return rows


def holm_adjust(pvals: Mapping[str, float], alpha: float = 0.05) -> Dict[str, dict]:
    """Holm-Bonferroni step-down adjusted p-values over a family of comparisons.

    Controls the family-wise error rate. Returns, per comparison, the raw p, the
    adjusted p (monotone non-decreasing in rank), and whether it stays significant
    at `alpha` after correction. Use this on the SET of pairwise tests the decision
    relies on, not on a single comparison in isolation.
    """
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out: Dict[str, dict] = {}
    running = 0.0
    for i, (name, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)  # step-down weight m, m-1, ..., 1
        running = max(running, adj)  # enforce monotonicity across the ordered family
        out[name] = {
            "p_raw": round(p, 4),
            "p_holm": round(running, 4),
            "significant": running < alpha,
        }
    return out
