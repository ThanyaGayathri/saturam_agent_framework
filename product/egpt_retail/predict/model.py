"""Logistic regression, in numpy, with the evaluation a readiness check needs (K26).

Why not a library: the product's wheel carries numpy (through the graph driver) and no
scikit-learn, and the model here is deliberately the simplest one that is calibrated and
explainable per row -- a coefficient times a standardised feature is a contribution a
grounded narrative can quote ("open 41 days vs a typical 15 adds most of the risk").
Gradient boosting can sit behind a flag once a holdout justifies it.

Everything is a pure function over arrays: fit, predict, and the three numbers readiness
shows -- AUC, Brier score, base rate -- plus calibration bins for the report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "Fitted",
    "auc",
    "brier",
    "calibration",
    "design",
    "fit_logistic",
    "predict_proba",
]


@dataclass
class Fitted:
    """A fitted model and what it needs to score a new row the same way."""

    numeric: List[str]
    categorical: List[str]
    levels: Dict[str, List[str]]
    means: np.ndarray
    scales: np.ndarray
    coef: np.ndarray
    intercept: float
    columns: List[str]
    iterations: int = 0
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "numeric": self.numeric,
            "categorical": self.categorical,
            "levels": self.levels,
            "means": self.means.tolist(),
            "scales": self.scales.tolist(),
            "coef": self.coef.tolist(),
            "intercept": float(self.intercept),
            "columns": self.columns,
            "iterations": self.iterations,
        }


def _levels_of(rows: Sequence[Mapping[str, Any]], name: str, cap: int) -> List[str]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = row.get(name)
        key = "" if value is None else str(value)
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts, key=lambda k: (-counts[k], k))
    return ordered[: cap - 1]  # the rest fold into "other"


def design(
    rows: Sequence[Mapping[str, Any]],
    *,
    numeric: Sequence[str],
    categorical: Sequence[str],
    levels: Optional[Mapping[str, Sequence[str]]] = None,
    level_cap: int = 12,
) -> Tuple[np.ndarray, List[str], Dict[str, List[str]]]:
    """Rows -> a matrix: numeric columns as they are (None -> 0), categoricals one-hot over
    the levels given (or the most common ``level_cap - 1``, the rest as 'other')."""
    resolved: Dict[str, List[str]] = {
        c: list(levels[c]) if levels and c in levels else _levels_of(rows, c, level_cap)
        for c in categorical
    }
    columns: List[str] = list(numeric)
    for c in categorical:
        columns += [f"{c}={lvl}" for lvl in resolved[c]] + [f"{c}=other"]
    matrix = np.zeros((len(rows), len(columns)), dtype=float)
    for i, row in enumerate(rows):
        for j, name in enumerate(numeric):
            value = row.get(name)
            matrix[i, j] = 0.0 if value is None else float(value)
        offset = len(numeric)
        for c in categorical:
            lvls = resolved[c]
            value = row.get(c)
            key = "" if value is None else str(value)
            k = lvls.index(key) if key in lvls else len(lvls)
            matrix[i, offset + k] = 1.0
            offset += len(lvls) + 1
    return matrix, columns, resolved


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logistic(
    rows: Sequence[Mapping[str, Any]],
    labels: Sequence[int],
    *,
    numeric: Sequence[str],
    categorical: Sequence[str],
    l2: float = 1.0,
    iterations: int = 300,
    learning_rate: float = 0.5,
) -> Fitted:
    """Fit by full-batch gradient descent on the L2-regularised log loss, numeric features
    standardised (the coefficients are then comparable, which the drivers rely on)."""
    y = np.asarray(labels, dtype=float)
    matrix, columns, levels = design(rows, numeric=numeric, categorical=categorical)
    n_num = len(numeric)
    means = matrix[:, :n_num].mean(axis=0) if n_num else np.zeros(0)
    scales = matrix[:, :n_num].std(axis=0) if n_num else np.zeros(0)
    scales = np.where(scales == 0, 1.0, scales)
    x = matrix.copy()
    if n_num:
        x[:, :n_num] = (x[:, :n_num] - means) / scales
    n, d = x.shape
    coef = np.zeros(d)
    intercept = float(np.log((y.mean() + 1e-6) / (1 - y.mean() + 1e-6)))
    for _ in range(iterations):
        p = _sigmoid(x @ coef + intercept)
        grad = x.T @ (p - y) / n + l2 * coef / n
        coef -= learning_rate * grad
        intercept -= learning_rate * float((p - y).mean())
    return Fitted(
        numeric=list(numeric),
        categorical=list(categorical),
        levels=levels,
        means=means,
        scales=scales,
        coef=coef,
        intercept=intercept,
        columns=columns,
        iterations=iterations,
    )


def predict_proba(
    fitted: Fitted, rows: Sequence[Mapping[str, Any]]
) -> Tuple[np.ndarray, np.ndarray]:
    """Probabilities and, per row, each column's contribution to the logit (coefficient
    times standardised value) -- what the drivers are read from."""
    matrix, _, _ = design(
        rows,
        numeric=fitted.numeric,
        categorical=fitted.categorical,
        levels=fitted.levels,
    )
    x = matrix.copy()
    n_num = len(fitted.numeric)
    if n_num:
        x[:, :n_num] = (x[:, :n_num] - fitted.means) / fitted.scales
    contributions = x * fitted.coef
    return _sigmoid(contributions.sum(axis=1) + fitted.intercept), contributions


def auc(labels: Sequence[int], scores: Sequence[float]) -> Optional[float]:
    """Area under the ROC curve by the rank statistic; None when one class is absent."""
    y = np.asarray(labels, dtype=float)
    s = np.asarray(scores, dtype=float)
    positives, negatives = s[y == 1], s[y == 0]
    if len(positives) == 0 or len(negatives) == 0:
        return None
    order = np.argsort(s)
    ranks = np.empty(len(s), dtype=float)
    # average ranks for ties
    sorted_s = s[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    rank_sum = ranks[y == 1].sum()
    n_pos, n_neg = len(positives), len(negatives)
    return float((rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def brier(labels: Sequence[int], scores: Sequence[float]) -> float:
    y = np.asarray(labels, dtype=float)
    s = np.asarray(scores, dtype=float)
    return float(np.mean((s - y) ** 2)) if len(y) else 0.0


def calibration(
    labels: Sequence[int], scores: Sequence[float], *, bins: int = 5
) -> List[Dict[str, Any]]:
    """Predicted vs observed rate per score bin -- the report's calibration table."""
    y = np.asarray(labels, dtype=float)
    s = np.asarray(scores, dtype=float)
    out: List[Dict[str, Any]] = []
    edges = np.linspace(0, 1, bins + 1)
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (s >= lo) & ((s < hi) if hi < 1 else (s <= hi))
        if not mask.any():
            continue
        out.append(
            {
                "from": round(float(lo), 2),
                "to": round(float(hi), 2),
                "rows": int(mask.sum()),
                "predicted": round(float(s[mask].mean()), 3),
                "observed": round(float(y[mask].mean()), 3),
            }
        )
    return out
