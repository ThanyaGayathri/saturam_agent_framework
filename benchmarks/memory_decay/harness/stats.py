"""Statistical significance for the episodic-decay benchmark.

The first pass ranked strategies on 5-seed ``statistics.mean`` alone, with no test
that a gap exceeds seed/query noise — the same weakness the sibling SM-R4
benchmark shipped its ``stats.py`` to close. This module is adapted from that one
(``benchmarks/memory_embedding/harness/stats.py``); it should be promoted to a
shared ``benchmarks/_common`` location once SM-R4 merges, rather than kept forked.

  - ``bootstrap_ci``    — 95% CI for a strategy's mean metric (resample queries).
  - ``paired_bootstrap`` — for two strategies scored on the SAME queries, the mean
    per-query difference, its CI, and a two-sided p for "difference != 0". Pairing
    cancels query-difficulty variance, so it is far more sensitive than comparing
    two independent CIs.
  - ``holm_adjust``     — Holm-Bonferroni step-down over the FAMILY of pairwise
    p-values a decision leans on, controlling family-wise error rate.

numpy only; deterministic given ``seed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping

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
    # Two-sided bootstrap p for H0 mean-diff == 0. Use both tail masses directly so
    # two identical strategies (all diffs 0) yield p = 1.0, not p = 0.0.
    prop_le0 = float(np.mean(boot <= 0.0))
    prop_ge0 = float(np.mean(boot >= 0.0))
    p = min(1.0, 2.0 * min(prop_le0, prop_ge0))
    return PairedResult(float(diffs.mean()), float(lo), float(hi), p, n, p < alpha)


def holm_adjust(pvals: Mapping[str, float], alpha: float = 0.05) -> Dict[str, dict]:
    """Holm-Bonferroni step-down adjusted p-values over a family of comparisons.

    Controls the family-wise error rate. Returns, per comparison, the raw p, the
    adjusted p (monotone non-decreasing in rank), and whether it stays significant
    at ``alpha`` after correction.
    """
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out: Dict[str, dict] = {}
    running = 0.0
    for i, (name, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)  # step-down weights m, m-1, ..., 1
        running = max(running, adj)  # enforce monotonicity across the ordered family
        out[name] = {
            "p_raw": round(p, 4),
            "p_holm": round(running, 4),
            "significant": running < alpha,
        }
    return out
