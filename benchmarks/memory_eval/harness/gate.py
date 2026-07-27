"""CI pass/fail gate over a results payload.

A run is a regression guard only if it can FAIL a build. ``check_report`` applies
provisional SLOs to one report; ``check_all`` folds a list. Thresholds are
PROVISIONAL and marked for SM-R1 to ratify (seeded from SM-R3's p95 SLO and a
conservative recall floor); override per-benchmark once real baselines exist.

Design: gates are declared as data (``GateConfig``), not scattered ``if``s, so the
CI job and the plan reference the same thresholds. Metrics that are absent for a
benchmark (e.g. bitemporal on LOCOMO) are skipped, not failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class GateConfig:
    """Provisional SLOs. None disables that gate. (SM-R1 to ratify.)"""

    min_qa_accuracy: Optional[float] = 0.50
    min_recall_at_k: Optional[float] = 0.60
    max_query_p95_ms: Optional[float] = 1500.0  # provisional; SM-R3 latency SLO family
    max_cost_usd: Optional[float] = None  # set per-run once real token costs are known


def check_report(report: dict, cfg: GateConfig) -> List[str]:
    """Return a list of human-readable gate failures for one report (empty = pass)."""
    fails: List[str] = []
    who = f"{report.get('benchmark')}/{report.get('provider')}"

    qa = report.get("qa_accuracy")
    if cfg.min_qa_accuracy is not None and qa and qa["value"] < cfg.min_qa_accuracy:
        fails.append(f"{who}: QA {qa['value']:.3f} < min {cfg.min_qa_accuracy}")

    recall = report.get("retrieval", {}).get("recall_at_k")
    if cfg.min_recall_at_k is not None and recall and recall["value"] < cfg.min_recall_at_k:
        fails.append(f"{who}: recall@k {recall['value']:.3f} < min {cfg.min_recall_at_k}")

    q95 = report.get("latency_ms", {}).get("query", {}).get("p95")
    if cfg.max_query_p95_ms is not None and q95 is not None and q95 > cfg.max_query_p95_ms:
        fails.append(f"{who}: query p95 {q95:.1f}ms > max {cfg.max_query_p95_ms}ms")

    cost = report.get("cost")
    if cfg.max_cost_usd is not None and cost and cost["cost_usd"] > cfg.max_cost_usd:
        fails.append(f"{who}: cost ${cost['cost_usd']:.4f} > max ${cfg.max_cost_usd}")

    return fails


def check_all(reports: List[dict], cfg: Optional[GateConfig] = None) -> Tuple[bool, List[str]]:
    """Return (passed, failures) over many reports."""
    cfg = cfg or GateConfig()
    failures: List[str] = []
    for r in reports:
        failures.extend(check_report(r, cfg))
    return (not failures, failures)
