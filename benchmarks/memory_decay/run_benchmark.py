"""Run the episodic-decay benchmark across all five candidate strategies.

Usage:
    ./.venv/bin/python run_benchmark.py                        # synthetic relevance
    ./.venv/bin/python run_benchmark.py --seeds 10 --k 5
    ./.venv/bin/python run_benchmark.py --relevance embedding  # confirm w/ real embeddings

Writes (suffixed by relevance mode for the embedding run):
    results/summary.json / summary_embedding.json
    results/summary.md   / summary_embedding.md

The synthetic mode needs no external services and is deterministic given the
seeds; the embedding mode additionally needs a local sentence-transformers model
(``pip install -r requirements.txt``). Both a sync ``main`` and an async ``amain``
entry point are provided.
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


def _build_streams(seeds: List[int], relevance: str, model_name: str) -> List:
    """Build one stream per seed, embedding the text if requested.

    Streams are built once and reused for both the ranking and pruning passes so
    real embeddings (the expensive part) are computed a single time per seed.
    """
    streams = []
    for seed in seeds:
        stream = generate_stream(seed=seed)
        if relevance == "embedding":
            from harness.relevance import embed_stream, embeddings_available

            if not embeddings_available():
                raise SystemExit(
                    "embedding relevance needs sentence-transformers; "
                    "install it with: ./.venv/bin/pip install -r requirements.txt"
                )
            embed_stream(stream, model_name=model_name)
        streams.append(stream)
    return streams


def _collect(streams: List, k: int, prune: bool) -> Dict[str, dict]:
    """Run every strategy over every (prebuilt) stream; average the metrics."""
    accum: Dict[str, Dict[str, List[float]]] = {}
    bucket_accum: Dict[str, Dict[str, List[float]]] = {}
    params: Dict[str, dict] = {}

    for stream in streams:
        # Build strategies once per stream (never inside the inner loop).
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
    seeds: List[int],
    k: int,
    relevance: str,
    ranking: Dict[str, dict],
    pruning: Dict[str, dict],
) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    suffix = "_embedding" if relevance == "embedding" else ""
    payload = {
        "seeds": seeds,
        "k": k,
        "relevance": relevance,
        "ranking_only": ranking,
        "with_pruning": pruning,
    }
    (RESULTS_DIR / f"summary{suffix}.json").write_text(json.dumps(payload, indent=2))

    md = "# Episodic Decay Benchmark Results\n\n"
    md += f"Seeds: {seeds}  |  k = {k}  |  relevance = {relevance}  |  candidates = {len(ranking)}\n\n"
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
    suffix = "_embedding" if relevance == "embedding" else ""
    (RESULTS_DIR / f"summary{suffix}.md").write_text(md)


def main() -> None:
    parser = argparse.ArgumentParser(description="Episodic decay benchmark (SM-R5)")
    parser.add_argument(
        "--seeds", type=int, default=5, help="Number of seeds to average over"
    )
    parser.add_argument("--k", type=int, default=5, help="Retrieval depth (top-k)")
    parser.add_argument(
        "--relevance",
        choices=["synthetic", "embedding"],
        default="synthetic",
        help="Relevance signal: deterministic synthetic (default) or real text embeddings",
    )
    parser.add_argument(
        "--embedding-model",
        default="sentence-transformers/all-MiniLM-L6-v2",
        help="sentence-transformers model id (embedding mode only)",
    )
    args = parser.parse_args()

    seeds = list(range(args.seeds))
    streams = _build_streams(
        seeds=seeds, relevance=args.relevance, model_name=args.embedding_model
    )
    ranking = _collect(streams=streams, k=args.k, prune=False)
    pruning = _collect(streams=streams, k=args.k, prune=True)
    _write_outputs(
        seeds=seeds,
        k=args.k,
        relevance=args.relevance,
        ranking=ranking,
        pruning=pruning,
    )

    suffix = "_embedding" if args.relevance == "embedding" else ""
    print("Episodic decay benchmark")
    print(f"seeds={seeds} k={args.k} relevance={args.relevance}")
    print("")
    print("Retrieval quality (ranking only):")
    print(_fmt_quality_table(ranking))
    print("Recall by fact-age bucket:")
    print(_fmt_bucket_table(ranking))
    print("Pruning behaviour:")
    print(_fmt_prune_table(pruning))
    print(
        f"Wrote {RESULTS_DIR / f'summary{suffix}.json'} and {RESULTS_DIR / f'summary{suffix}.md'}"
    )


async def amain() -> None:
    """Async entry point (mirrors :func:`main`)."""
    main()


if __name__ == "__main__":
    asyncio.run(amain())
