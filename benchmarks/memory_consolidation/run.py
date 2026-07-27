"""CLI entrypoint for the consolidation harness.

Examples
--------
Warm the cache with real Azure calls, all strategies, synthetic set + QA:
    python run.py --dataset synthetic --qa

Offline replay (CI-safe, deterministic; requires a warm cache):
    HARNESS_LLM_MODE=replay python run.py --dataset synthetic --qa

LOCOMO external-validity QA on one sample, first 6 sessions:
    python run.py --dataset locomo --locomo-sample 0 --max-sessions 6 --qa --qa-limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

from harness.datasets.synthetic import build_synthetic
from harness.llm import LLMClient
from harness.runner import arun_strategy, run_strategy
from harness.strategies import STRATEGIES

_RESULTS = Path(__file__).resolve().parent / "results"


def _load_dataset(args: argparse.Namespace):
    if args.dataset == "synthetic":
        return build_synthetic()
    if args.dataset == "locomo":
        from harness.datasets.locomo import build_locomo, download_locomo

        if args.download_locomo:
            print("Downloading LOCOMO ...")
            download_locomo()
        return build_locomo(sample_index=args.locomo_sample, max_sessions=args.max_sessions)
    raise ValueError(f"unknown dataset {args.dataset}")


def _print_table(reports: List[Dict[str, Any]]) -> None:
    cols = [
        ("strategy", "strategy", 13),
        ("quality", "composite_quality", 8),
        ("recall", ("label_metrics", "fact_recall"), 7),
        ("prec", ("label_metrics", "fact_precision"), 6),
        ("dedup", ("label_metrics", "dedup_score"), 6),
        ("conflict", ("label_metrics", "conflict_accuracy"), 8),
        ("prov_r", ("label_metrics", "provenance_recall"), 7),
        ("facts", ("label_metrics", "active_fact_count"), 6),
        ("tokens", ("usage", "total_tokens"), 8),
        ("qa", ("qa", "qa_accuracy"), 6),
        ("$", ("usage", "usd_cost"), 8),
    ]
    header = "  ".join(name.ljust(w) for name, _, w in cols)
    print(header)
    print("-" * len(header))
    for r in reports:
        cells = []
        for _name, key, w in cols:
            if isinstance(key, tuple):
                val = r.get(key[0], {})
                val = val.get(key[1]) if isinstance(val, dict) else None
            else:
                val = r.get(key)
            cells.append(("" if val is None else str(val)).ljust(w))
        print("  ".join(cells))


def main() -> None:
    parser = argparse.ArgumentParser(description="Episodic->semantic consolidation harness (SM-R7)")
    parser.add_argument("--dataset", choices=["synthetic", "locomo"], default="synthetic")
    parser.add_argument("--strategies", nargs="+", default=list(STRATEGIES), choices=list(STRATEGIES))
    parser.add_argument("--qa", action="store_true", help="run QA correctness (uses the LLM)")
    parser.add_argument("--qa-limit", type=int, default=None)
    parser.add_argument("--mode", default=None, help="LLM mode: auto|replay|live (else env HARNESS_LLM_MODE)")
    parser.add_argument("--async-run", action="store_true", help="use the async code path")
    parser.add_argument("--locomo-sample", type=int, default=0)
    parser.add_argument("--max-sessions", type=int, default=None)
    parser.add_argument("--download-locomo", action="store_true")
    parser.add_argument("--out", default=None, help="output json path (default results/<dataset>_results.json)")
    args = parser.parse_args()

    dataset = _load_dataset(args)
    llm = LLMClient(mode=args.mode)

    reports: List[Dict[str, Any]] = []
    for name in args.strategies:
        print(f"Running strategy: {name} ...")
        if args.async_run:
            report = asyncio.run(arun_strategy(name, dataset, llm, with_qa=args.qa, qa_limit=args.qa_limit))
        else:
            report = run_strategy(name, dataset, llm, with_qa=args.qa, qa_limit=args.qa_limit)
        reports.append(report)

    _RESULTS.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else _RESULTS / f"{args.dataset}_results.json"
    out.write_text(json.dumps(reports, indent=2))
    print()
    _print_table(reports)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
