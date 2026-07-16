"""Run the event-segmentation benchmark across all candidate approaches.

Usage:
    ./.venv/bin/python run_benchmark.py                          # offline, synthetic vectors
    ./.venv/bin/python run_benchmark.py --seeds 10 --tol 1
    ./.venv/bin/python run_benchmark.py --relevance embedding    # confirm w/ real embeddings
    ./.venv/bin/python run_benchmark.py --llm --llm-provider ollama          # + on-prem SLM
    ./.venv/bin/python run_benchmark.py --llm --llm-provider azure_responses # + gpt-5.5 (Azure)

Offline candidates need no services or keys and are deterministic given the seeds.
The ``--llm`` run additionally calls a real model (Azure Responses via .env, or a
local ollama SLM) and records its latency/cost. Results are written to
``results/`` (suffixed by mode). Both a sync ``main`` and async ``amain`` are
provided.
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


def _mean(values: List[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _build_streams(seeds: List[int], relevance: str, model_name: str, params: StreamParams) -> List[Stream]:
    streams: List[Stream] = []
    for seed in seeds:
        stream = generate_stream(seed=seed, params=params)
        if relevance == "embedding":
            from harness.embeddings import embed_stream, embeddings_available

            if not embeddings_available():
                raise SystemExit(
                    "embedding relevance needs sentence-transformers; "
                    "install it with: ./.venv/bin/pip install -r requirements.txt"
                )
            embed_stream(stream)
        streams.append(stream)
    return streams


def _accumulate(results_per_seed: Dict[str, List[SegResult]]) -> Dict[str, dict]:
    summary: Dict[str, dict] = {}
    for name, results in results_per_seed.items():
        agg: Dict[str, object] = {}
        for key in _METRIC_KEYS:
            agg[key] = _mean([float(getattr(r, key)) for r in results])
        agg["params"] = results[0].params
        summary[name] = agg
    return summary


def _fmt_table(summary: Dict[str, dict], with_cost: bool) -> str:
    if with_cost:
        header = "| approach | F1 | precision | recall | Pk | WinDiff | segs | latency(ms) | cost($) |\n"
        header += "|---|---|---|---|---|---|---|---|---|\n"
    else:
        header = "| approach | F1 | precision | recall | Pk | WinDiff | segs | latency(ms) |\n"
        header += "|---|---|---|---|---|---|---|---|\n"
    rows = ""
    for name, m in summary.items():
        base = (
            f"| {name} | {m['f1']:.3f} | {m['precision']:.3f} | {m['recall']:.3f} | "
            f"{m['pk']:.3f} | {m['window_diff']:.3f} | {m['n_pred'] + 1:.1f} | {m['latency_ms']:.3f} |"
        )
        if with_cost:
            base += f" {m['cost_usd']:.5f} |"
        rows += base + "\n"
    return header + rows


def _write_outputs(seeds: List[int], tol: int, relevance: str, suffix: str, summary: Dict[str, dict]) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    payload = {"seeds": seeds, "tol": tol, "relevance": relevance, "results": summary}
    (RESULTS_DIR / f"summary{suffix}.json").write_text(json.dumps(payload, indent=2))
    with_cost = any(m["cost_usd"] > 0 or m["input_tokens"] > 0 for m in summary.values())
    md = "# Event Segmentation Benchmark Results\n\n"
    md += f"Seeds: {seeds}  |  boundary tolerance: +/-{tol} events  |  relevance: {relevance}\n\n"
    md += _fmt_table(summary, with_cost) + "\n"
    md += "Higher F1/precision/recall is better; **lower Pk / WindowDiff is better**. "
    md += "`segs` is the mean number of predicted episodes per stream.\n"
    (RESULTS_DIR / f"summary{suffix}.md").write_text(md)


def _run(
    seeds: List[int],
    tol: int,
    relevance: str,
    params: StreamParams,
    include_llm: bool,
    llm_provider: str,
) -> Dict[str, dict]:
    streams = _build_streams(seeds=seeds, relevance=relevance, model_name="", params=params)
    results_per_seed: Dict[str, List[SegResult]] = {}

    for stream in streams:
        # Build segmenters once per stream (never inside the metric loop).
        for seg in default_segmenters():
            res = run_segmenter(stream, seg, tol=tol)
            results_per_seed.setdefault(res.name, []).append(res)

    if include_llm:
        from harness.llm import LLMBoundaryDetector, load_env

        load_env()
        detector = LLMBoundaryDetector(provider=llm_provider)  # one model, reused
        label = "llm_boundary" if llm_provider == "azure_responses" else "slm_boundary"
        for stream in streams:
            res = run_llm(stream, detector, tol=tol, name=label)
            results_per_seed.setdefault(res.name, []).append(res)

    return _accumulate(results_per_seed)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Event segmentation benchmark (SM-R6)")
    parser.add_argument("--seeds", type=int, default=5, help="Number of seeds to average over")
    parser.add_argument("--tol", type=int, default=1, help="Boundary match tolerance (+/- events)")
    parser.add_argument(
        "--relevance",
        choices=["synthetic", "embedding"],
        default="synthetic",
        help="Embedding source: deterministic synthetic (default) or real text embeddings",
    )
    parser.add_argument("--llm", action="store_true", help="Also run the LLM/SLM candidate")
    parser.add_argument(
        "--llm-provider",
        choices=["azure_responses", "ollama"],
        default="azure_responses",
        help="LLM backend for --llm (Azure Responses or on-prem ollama SLM)",
    )
    args = parser.parse_args(argv)

    seeds = list(range(args.seeds))
    params = StreamParams()
    summary = _run(
        seeds=seeds,
        tol=args.tol,
        relevance=args.relevance,
        params=params,
        include_llm=args.llm,
        llm_provider=args.llm_provider,
    )

    suffix = ""
    if args.relevance == "embedding":
        suffix = "_embedding"
    if args.llm:
        suffix += "_slm" if args.llm_provider == "ollama" else "_llm"
    _write_outputs(seeds=seeds, tol=args.tol, relevance=args.relevance, suffix=suffix, summary=summary)

    with_cost = any(m["cost_usd"] > 0 or m["input_tokens"] > 0 for m in summary.values())
    print("Event segmentation benchmark")
    print(f"seeds={seeds} tol=+/-{args.tol} relevance={args.relevance}")
    print("")
    print(_fmt_table(summary, with_cost))
    print(f"Wrote results/summary{suffix}.json and results/summary{suffix}.md")


async def amain(argv: Optional[List[str]] = None) -> None:
    """Async entry point (mirrors :func:`main`)."""
    main(argv)


if __name__ == "__main__":
    asyncio.run(amain())
