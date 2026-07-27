"""Analyze benchmark results: CIs, significance, tie-aware ranking, cross-set agreement.

Reads one or more results JSON files produced by `run.py` (each result carries
`per_query` metric values), and for a chosen metric reports, per dataset:
  - each model's mean with a 95% bootstrap CI,
  - whether the gap to the next-best model is statistically significant (paired),
    so "ties within noise" are called out rather than ranked.
Also reports rank agreement of the model ordering across datasets (the synthetic
set should agree with the public benchmarks — that agreement is the signal).

Usage:
    PYTHONPATH=. python -m harness.analyze --metric ndcg@10 results/*.json
"""

from __future__ import annotations

import argparse
import glob
import json
from typing import Dict, List

from harness import stats


def _load(paths: List[str]) -> List[dict]:
    files: List[str] = []
    for p in paths:
        files.extend(sorted(glob.glob(p)))
    return [json.loads(open(f).read()) for f in files]


def _spearman(order_a: List[str], order_b: List[str]) -> float:
    common = [m for m in order_a if m in order_b]
    if len(common) < 2:
        return float("nan")
    ra = {m: i for i, m in enumerate(order_a) if m in common}
    rb = {m: i for i, m in enumerate(order_b) if m in common}
    n = len(common)
    d2 = sum((ra[m] - rb[m]) ** 2 for m in common)
    return 1 - (6 * d2) / (n * (n * n - 1))


def analyze(payloads: List[dict], metric: str) -> Dict[str, List[dict]]:
    per_dataset_order: Dict[str, List[str]] = {}
    report: Dict[str, List[dict]] = {}
    for pay in payloads:
        ds = pay["dataset"]
        model_pq = {
            r["key"]: r["per_query"][metric]
            for r in pay["results"]
            if "per_query" in r and metric in r.get("per_query", {})
        }
        if not model_pq:
            continue
        rows = stats.rank_with_ties(model_pq, metric_name=metric)
        report[ds] = rows
        per_dataset_order[ds] = [r["model"] for r in rows]

    # cross-dataset rank agreement (Spearman) vs the synthetic set if present
    ref = next((d for d in per_dataset_order if d.startswith("synthetic")), None)
    if ref:
        agree = {}
        for ds, order in per_dataset_order.items():
            if ds == ref:
                continue
            agree[ds] = round(_spearman(per_dataset_order[ref], order), 3)
        report["_rank_agreement_vs_" + ref] = agree  # type: ignore
    return report


def _print(report: Dict, metric: str) -> None:
    for ds, rows in report.items():
        if ds.startswith("_rank_agreement"):
            print(f"\n## {ds}: {rows}")
            continue
        print(f"\n## {ds} — {metric} (mean [95% CI], tie? vs next)")
        for r in rows:
            tie = r.get("tie_with_next")
            tag = (
                ""
                if tie is None
                else ("  ~tie" if tie else f"  >sig (p={r['p_next']})")
            )
            print(
                f"  {r['model']:<20} {r['mean']:.4f} [{r['ci_lo']:.4f}, {r['ci_hi']:.4f}]{tag}"
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default="ndcg@10")
    ap.add_argument("--out", default=None)
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args()
    payloads = _load(args.paths)
    report = analyze(payloads, args.metric)
    _print(report, args.metric)
    if args.out:
        open(args.out, "w").write(
            json.dumps({"metric": args.metric, "report": report}, indent=2) + "\n"
        )
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
