"""Run the episodic-decay benchmark across all five candidate strategies.

Usage:
    ./.venv/bin/python run_benchmark.py                 # default: 5 seeds
    ./.venv/bin/python run_benchmark.py --seeds 10 --k 5

Writes:
    results/summary.json   machine-readable metrics (per strategy, averaged)
    results/summary.md     human-readable comparison tables

No external services; deterministic given the seeds. Both a sync ``main`` and an
async ``amain`` entry point are provided.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from pathlib import Path
from typing import Dict, List

from harness.simulate import run_simulation
from harness.strategies import default_strategies
from harness.stream import generate_stream

RESULTS_DIR = Path(__file__).parent / "results"


def _mean(values: List[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _collect(seeds: List[int], k: int, prune: bool) -> Dict[str, dict]:
    """Run every strategy over every seed; average the metrics."""
    # Build strategies once per seed (never inside the inner loop).
    accum: Dict[str, Dict[str, List[float]]] = {}
    bucket_accum: Dict[str, Dict[str, List[float]]] = {}
    params: Dict[str, dict] = {}

    for seed in seeds:
        stream = generate_stream(seed=seed)
        strategies = default_strategies(prune=prune)
        for strategy in strategies:
            result = run_simulation(stream=stream, strategy=strategy, k=k, prune=prune)
            q = result.quality
            p = result.pruning
            name = result.strategy
            params[name] = result.params
            store = accum.setdefault(name, {})
            for key, val in (
                ("precision_at_k", q.precision_at_k),
                ("recall_at_k", q.recall_at_k),
                ("ndcg_at_k", q.ndcg_at_k),
                ("mrr", q.mrr),
                ("stale_hit_rate", q.stale_hit_rate),
                ("prune_precision", p.prune_precision),
                ("prune_recall", p.prune_recall),
                ("false_forget", float(p.false_forget)),
                ("retained_final", float(p.retained_final)),
            ):
                store.setdefault(key, []).append(val)
            bstore = bucket_accum.setdefault(name, {})
            for bucket, recall in q.recall_by_age_bucket.items():
                bstore.setdefault(bucket, []).append(recall)

    summary: Dict[str, dict] = {}
    for name, metrics in accum.items():
        summary[name] = {key: _mean(vals) for key, vals in metrics.items()}
        summary[name]["recall_by_age_bucket"] = {
            b: _mean(v) for b, v in bucket_accum[name].items()
        }
        summary[name]["params"] = params[name]
    return summary


def _fmt_quality_table(summary: Dict[str, dict]) -> str:
    header = "| strategy | nDCG@k | precision@k | recall@k | MRR | stale-hit-rate |\n"
    header += "|---|---|---|---|---|---|\n"
    rows = ""
    for name, m in summary.items():
        rows += (
            f"| {name} | {m['ndcg_at_k']:.3f} | {m['precision_at_k']:.3f} | "
            f"{m['recall_at_k']:.3f} | {m['mrr']:.3f} | {m['stale_hit_rate']:.3f} |\n"
        )
    return header + rows


def _fmt_prune_table(summary: Dict[str, dict]) -> str:
    header = "| strategy | prune-precision | prune-recall | false-forget | retained-final |\n"
    header += "|---|---|---|---|---|\n"
    rows = ""
    for name, m in summary.items():
        rows += (
            f"| {name} | {m['prune_precision']:.3f} | {m['prune_recall']:.3f} | "
            f"{m['false_forget']:.1f} | {m['retained_final']:.0f} |\n"
        )
    return header + rows


def _fmt_bucket_table(summary: Dict[str, dict]) -> str:
    buckets = ["0-1d", "1-7d", "7-30d", "30d+"]
    header = "| strategy | " + " | ".join(buckets) + " |\n"
    header += "|" + "---|" * (len(buckets) + 1) + "\n"
    rows = ""
    for name, m in summary.items():
        cells = []
        for b in buckets:
            val = m["recall_by_age_bucket"].get(b)
            cells.append(f"{val:.3f}" if val is not None else "-")
        rows += f"| {name} | " + " | ".join(cells) + " |\n"
    return header + rows


def _write_outputs(
    seeds: List[int], k: int, ranking: Dict[str, dict], pruning: Dict[str, dict]
) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    payload = {
        "seeds": seeds,
        "k": k,
        "ranking_only": ranking,
        "with_pruning": pruning,
    }
    (RESULTS_DIR / "summary.json").write_text(json.dumps(payload, indent=2))

    md = "# Episodic Decay Benchmark Results\n\n"
    md += f"Seeds: {seeds}  |  k = {k}  |  candidates = {len(ranking)}\n\n"
    md += "## Retrieval quality (ranking only, no pruning)\n\n"
    md += _fmt_quality_table(ranking) + "\n"
    md += "Higher nDCG/precision/recall/MRR is better; lower stale-hit-rate is better.\n\n"
    md += "## Recall by fact-age bucket (ranking only)\n\n"
    md += _fmt_bucket_table(ranking) + "\n"
    md += "How well each strategy still finds the current fact as it ages.\n\n"
    md += "## Pruning behaviour (prune enabled)\n\n"
    md += _fmt_prune_table(pruning) + "\n"
    md += "prune-precision: of pruned, fraction already dead. prune-recall: of dead, "
    md += (
        "fraction pruned. false-forget: pruned while still needed (lower is better).\n"
    )
    (RESULTS_DIR / "summary.md").write_text(md)


def main() -> None:
    parser = argparse.ArgumentParser(description="Episodic decay benchmark (SM-R5)")
    parser.add_argument(
        "--seeds", type=int, default=5, help="Number of seeds to average over"
    )
    parser.add_argument("--k", type=int, default=5, help="Retrieval depth (top-k)")
    args = parser.parse_args()

    seeds = list(range(args.seeds))
    ranking = _collect(seeds=seeds, k=args.k, prune=False)
    pruning = _collect(seeds=seeds, k=args.k, prune=True)
    _write_outputs(seeds=seeds, k=args.k, ranking=ranking, pruning=pruning)

    print("Episodic decay benchmark")
    print(f"seeds={seeds} k={args.k}")
    print("")
    print("Retrieval quality (ranking only):")
    print(_fmt_quality_table(ranking))
    print("Recall by fact-age bucket:")
    print(_fmt_bucket_table(ranking))
    print("Pruning behaviour:")
    print(_fmt_prune_table(pruning))
    print(f"Wrote {RESULTS_DIR / 'summary.json'} and {RESULTS_DIR / 'summary.md'}")


async def amain() -> None:
    """Async entry point (mirrors :func:`main`)."""
    main()


if __name__ == "__main__":
    asyncio.run(amain())
