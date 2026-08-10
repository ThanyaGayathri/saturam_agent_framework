"""Small statistics primitives used by the metric suite.

Kept separate so the exact definitions (percentile method, CI method) are in one
auditable place. ``percentile`` uses linear interpolation between closest ranks
on sorted data, matching ``statistics.quantiles(..., method="inclusive")`` — the
same convention the seed's ``eval/performance.py`` uses for ``p95_run_time`` — so
latency SLOs reported here line up with the suite's own numbers in Phase 4.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def percentile(values: Sequence[float], q: float) -> float:
    """The q-th percentile (q in [0, 100]) via inclusive linear interpolation.

    For sorted x[0..n-1], the rank is ``(q/100) * (n - 1)``; the result linearly
    interpolates between the two nearest samples. Empty -> 0.0; single value ->
    that value; q is clamped to [0, 100].
    """
    if not values:
        return 0.0
    q = max(0.0, min(100.0, q))
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    rank = (q / 100.0) * (len(xs) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    frac = rank - lo
    return float(xs[lo] + (xs[hi] - xs[lo]) * frac)


def p95(values: Sequence[float]) -> float:
    return percentile(values, 95.0)


def summarize(values: Sequence[float]) -> dict:
    """mean / p50 / p95 / max over a set of samples (e.g. latencies)."""
    return {
        "n": len(values),
        "mean": mean(values),
        "p50": percentile(values, 50.0),
        "p95": p95(values),
        "max": max(values) if values else 0.0,
    }


def wilson_interval(successes: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """95%-default Wilson score interval for a binomial proportion.

    Used when ranking two options on an accuracy/recall proportion: if the
    intervals overlap, the skill (§5) says call it a tie rather than rank them.
    Returns (low, high) in [0, 1]. n == 0 -> (0.0, 0.0).
    """
    if n == 0:
        return (0.0, 0.0)
    phat = successes / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * ((phat * (1 - phat) + z * z / (4 * n)) / n) ** 0.5) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def proportion_with_ci(successes: int, n: int) -> Optional[dict]:
    """A proportion plus its Wilson CI, or None when there is nothing to measure."""
    if n == 0:
        return None
    low, high = wilson_interval(successes, n)
    return {"value": successes / n, "n": n, "ci95": [low, high]}
