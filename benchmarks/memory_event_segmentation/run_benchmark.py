"""Run the event-segmentation benchmark across all candidate approaches.

Datasets / configs:
    --dataset synthetic  (default)  deterministic labelled synthetic stream
      --config default              base noise
      --config hard                 + gradual/overlapping boundaries + mid-topic
                                    long pauses (external-validity hardening)
    --dataset tiage                 real TIAGE topic-shift dialogues (real,
                                    elliptical turns, human-annotated boundaries;
                                    no timestamps -> time_gap/hybrid skipped)

Usage:
    ./.venv/bin/python run_benchmark.py                              # synthetic, synthetic vectors
    ./.venv/bin/python run_benchmark.py --relevance embedding        # synthetic, real embeddings
    ./.venv/bin/python run_benchmark.py --config hard --relevance embedding
    ./.venv/bin/python run_benchmark.py --dataset tiage              # real dialogues (auto real embeddings)
    ./.venv/bin/python run_benchmark.py --llm --llm-provider azure_responses --relevance embedding
    ./.venv/bin/python run_benchmark.py --dataset tiage --llm --llm-provider azure_responses --tiage-limit 100

All means are reported with a standard deviation over the units averaged (seeds for
synthetic, dialogues for TIAGE). Offline modes are deterministic given the seeds.
Both a sync ``main`` and async ``amain`` entry point are provided.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from pathlib import Path
from typing import Dict, List, Optional

from harness.segmenters import default_segmenters
from harness.simulate import SegResult, run_llm, run_segmenter
from harness.stream import Stream, StreamParams, generate_stream

RESULTS_DIR = Path(__file__).parent / "results"

_METRIC_KEYS = [
    "precision",
    "recall",
    "f1",
    "pk",
    "window_diff",
    "n_pred",
    "latency_ms",
    "cost_usd",
    "input_tokens",
    "output_tokens",
]

# Segmenters that require real per-message timestamps (skipped on real datasets).
_NEEDS_TIMESTAMPS = {"time_gap", "hybrid_gap_emllm", "hybrid_gap_drift"}


def _mean(values: List[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _std(values: List[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def hard_params() -> StreamParams:
    """External-validity hardening: gradual boundaries + mid-topic long pauses."""
    return StreamParams(gradual_shift_turns=3, mid_topic_pause_rate=0.15)


def _build_streams(
    dataset: str,
    seeds: List[int],
    relevance: str,
    params: StreamParams,
    tiage_limit: Optional[int],
) -> List[Stream]:
    if dataset == "tiage":
        from harness.datasets import load_tiage
        from harness.embeddings import embed_stream, embeddings_available

        if not embeddings_available():
            raise SystemExit("tiage needs sentence-transformers; ./.venv/bin/pip install -r requirements.txt")
        streams = load_tiage(limit=tiage_limit)
        for s in streams:
            embed_stream(s)  # real text embeddings (real dialogues have no synthetic vectors)
        return streams

    streams = []
    for seed in seeds:
        stream = generate_stream(seed=seed, params=params)
        if relevance == "embedding":
            from harness.embeddings import embed_stream, embeddings_available

            if not embeddings_available():
                raise SystemExit("embedding relevance needs sentence-transformers; see requirements.txt")
            embed_stream(stream)
        streams.append(stream)
    return streams


def _segmenters_for(dataset: str) -> List:
    segs = default_segmenters()
    if dataset == "tiage":
        segs = [s for s in segs if s.name not in _NEEDS_TIMESTAMPS]
    return segs


def _accumulate(results_per_unit: Dict[str, List[SegResult]]) -> Dict[str, dict]:
    summary: Dict[str, dict] = {}
    for name, results in results_per_unit.items():
        agg: Dict[str, object] = {"n": len(results)}
        for key in _METRIC_KEYS:
            vals = [float(getattr(r, key)) for r in results]
            agg[key] = _mean(vals)
            agg[key + "_std"] = _std(vals)
        agg["params"] = results[0].params
        summary[name] = agg
    return summary


def _fmt_table(summary: Dict[str, dict], with_cost: bool) -> str:
    if with_cost:
        header = (
            "| approach | F1 (mean±sd) | precision | recall | Pk (mean±sd) | WinDiff | segs | latency(ms) | cost($) |\n"
        )
        header += "|---|---|---|---|---|---|---|---|---|\n"
    else:
        header = "| approach | F1 (mean±sd) | precision | recall | Pk (mean±sd) | WinDiff | segs | latency(ms) |\n"
        header += "|---|---|---|---|---|---|---|---|\n"
    rows = ""
    for name, m in summary.items():
        base = (
            f"| {name} | {m['f1']:.3f}±{m['f1_std']:.3f} | {m['precision']:.3f} | {m['recall']:.3f} | "
            f"{m['pk']:.3f}±{m['pk_std']:.3f} | {m['window_diff']:.3f} | {m['n_pred'] + 1:.1f} | {m['latency_ms']:.3f} |"
        )
        if with_cost:
            base += f" {m['cost_usd']:.5f} |"
        rows += base + "\n"
    return header + rows


def _write_outputs(meta: dict, suffix: str, summary: Dict[str, dict]) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    payload = {**meta, "results": summary}
    (RESULTS_DIR / f"summary{suffix}.json").write_text(json.dumps(payload, indent=2))
    with_cost = any(m["cost_usd"] > 0 or m["input_tokens"] > 0 for m in summary.values())
    md = "# Event Segmentation Benchmark Results\n\n"
    md += (
        f"dataset: {meta['dataset']}  |  config: {meta['config']}  |  n: {meta['n_units']}  |  "
        f"tolerance: +/-{meta['tol']} events  |  relevance: {meta['relevance']}\n\n"
    )
    md += _fmt_table(summary, with_cost) + "\n"
    md += "Higher F1/precision/recall is better; **lower Pk / WindowDiff is better**. "
    md += "sd = standard deviation over the averaged units. `segs` is the mean predicted episodes per stream.\n"
    if meta["dataset"] == "tiage":
        md += "\n_time_gap and the hybrids are omitted: TIAGE has no per-message timestamps._\n"
    (RESULTS_DIR / f"summary{suffix}.md").write_text(md)


def _run(
    dataset: str,
    config: str,
    seeds: List[int],
    tol: int,
    relevance: str,
    include_llm: bool,
    llm_provider: str,
    tiage_limit: Optional[int],
) -> Dict[str, dict]:
    params = hard_params() if config == "hard" else StreamParams()
    streams = _build_streams(dataset, seeds, relevance, params, tiage_limit)
    results_per_unit: Dict[str, List[SegResult]] = {}

    for stream in streams:
        for seg in _segmenters_for(dataset):  # built once per stream, never in the metric loop
            res = run_segmenter(stream, seg, tol=tol)
            results_per_unit.setdefault(res.name, []).append(res)

    if include_llm:
        from harness.llm import LLMBoundaryDetector, load_env

        load_env()
        detector = LLMBoundaryDetector(provider=llm_provider)  # one model, reused
        label = "llm_boundary" if llm_provider == "azure_responses" else "slm_boundary"
        for stream in streams:
            res = run_llm(stream, detector, tol=tol, name=label)
            results_per_unit.setdefault(res.name, []).append(res)

    return _accumulate(results_per_unit)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Event segmentation benchmark (SM-R6)")
    parser.add_argument("--dataset", choices=["synthetic", "tiage"], default="synthetic")
    parser.add_argument("--config", choices=["default", "hard"], default="default", help="synthetic only")
    parser.add_argument("--seeds", type=int, default=5, help="synthetic seeds to average over")
    parser.add_argument("--tol", type=int, default=1, help="Boundary match tolerance (+/- events)")
    parser.add_argument(
        "--relevance",
        choices=["synthetic", "embedding"],
        default="synthetic",
        help="Embedding source for synthetic dataset (tiage always uses real embeddings)",
    )
    parser.add_argument("--llm", action="store_true", help="Also run the LLM/SLM candidate")
    parser.add_argument(
        "--llm-provider",
        choices=["azure_responses", "ollama"],
        default="azure_responses",
    )
    parser.add_argument("--tiage-limit", type=int, default=None, help="Cap number of TIAGE dialogues")
    args = parser.parse_args(argv)

    seeds = list(range(args.seeds))
    relevance = "embedding" if args.dataset == "tiage" else args.relevance
    summary = _run(
        dataset=args.dataset,
        config=args.config,
        seeds=seeds,
        tol=args.tol,
        relevance=relevance,
        include_llm=args.llm,
        llm_provider=args.llm_provider,
        tiage_limit=args.tiage_limit,
    )

    n_units = int(next(iter(summary.values()))["n"]) if summary else 0

    if args.dataset == "tiage":
        suffix = "_tiage"
    else:
        suffix = "_hard" if args.config == "hard" else ""
        if relevance == "embedding":
            suffix += "_embedding"
    if args.llm:
        suffix += "_slm" if args.llm_provider == "ollama" else "_llm"

    meta = {
        "dataset": args.dataset,
        "config": args.config,
        "seeds": seeds,
        "n_units": n_units,
        "tol": args.tol,
        "relevance": relevance,
    }
    _write_outputs(meta=meta, suffix=suffix, summary=summary)

    with_cost = any(m["cost_usd"] > 0 or m["input_tokens"] > 0 for m in summary.values())
    print("Event segmentation benchmark")
    print(f"dataset={args.dataset} config={args.config} n={n_units} tol=+/-{args.tol} relevance={relevance}")
    print("")
    print(_fmt_table(summary, with_cost))
    print(f"Wrote results/summary{suffix}.json and results/summary{suffix}.md")


async def amain(argv: Optional[List[str]] = None) -> None:
    """Async entry point (mirrors :func:`main`)."""
    main(argv)


if __name__ == "__main__":
    asyncio.run(amain())
