"""Phase-2 ablations for the review response.

Two questions the first pass answered by assertion rather than measurement:

1. **Prune gate (DECISION §4).** The doc defends a three-condition gate
   (recency-floor AND importance-below-floor AND not-currently-valid), but the
   first pass measured only ``recency < 0.05``. This ablation runs both gates and
   reports the pruning panel for each, so §4 describes what was actually measured.

2. **Reinforcement rule (DECISION §3).** The doc adopts "reinforce every injected
   top-k item" over the stricter "reinforce only ground-truth hits" because it
   "did not change the ranking" — true only under synthetic relevance. This
   ablation re-runs both policies under synthetic AND real embeddings, where fresh
   and stale blur, and reports stale-hit / recall.

Usage:
    ./.venv/bin/python ablations.py --seeds 5                       # prune-gate + synthetic reinforce
    ./.venv/bin/python ablations.py --seeds 5 --embedding           # also reinforce under embeddings
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
DECAY = ["exp_recency", "ttl_halflife", "importance_weighted", "ebbinghaus"]


def _mean(v: List[float]) -> float:
    return statistics.mean(v) if v else 0.0


def _prune_gate_ablation(seeds: List[int], k: int) -> Dict[str, Dict[str, dict]]:
    streams = [generate_stream(seed=s) for s in seeds]
    out: Dict[str, Dict[str, dict]] = {}
    for gate in ("recency", "three_condition"):
        acc: Dict[str, Dict[str, List[float]]] = {}
        for stream in streams:
            for strat in default_strategies(prune=True, prune_gate=gate):
                if strat.name not in DECAY:
                    continue
                r = run_simulation(stream=stream, strategy=strat, k=k, prune=True)
                s = acc.setdefault(strat.name, {})
                s.setdefault("prune_precision", []).append(r.pruning.prune_precision)
                s.setdefault("prune_recall", []).append(r.pruning.prune_recall)
                s.setdefault("false_forget", []).append(float(r.pruning.false_forget))
                s.setdefault("post_prune_recall", []).append(r.quality.recall_at_k)
                s.setdefault("retained_final", []).append(
                    float(r.pruning.retained_final)
                )
        out[gate] = {
            name: {kk: _mean(vv) for kk, vv in m.items()} for name, m in acc.items()
        }
    return out


def _reinforce_ablation(
    seeds: List[int], k: int, relevance: str
) -> Dict[str, Dict[str, dict]]:
    if relevance == "embedding":
        from harness.relevance import embed_stream

        streams = [embed_stream(generate_stream(seed=s)) for s in seeds]
    else:
        streams = [generate_stream(seed=s) for s in seeds]

    out: Dict[str, Dict[str, dict]] = {}
    for policy in ("all_topk", "valid_only"):
        acc: Dict[str, Dict[str, List[float]]] = {}
        for stream in streams:
            for strat in default_strategies(prune=False):
                if strat.name not in DECAY and strat.name != "no_decay":
                    continue
                r = run_simulation(
                    stream=stream, strategy=strat, k=k, reinforce_policy=policy
                )
                s = acc.setdefault(strat.name, {})
                s.setdefault("recall_at_k", []).append(r.quality.recall_at_k)
                s.setdefault("stale_hit_rate", []).append(r.quality.stale_hit_rate)
                s.setdefault("stale_hit_versioned", []).append(
                    r.quality.stale_hit_rate_versioned
                )
                s.setdefault("idle30", []).append(
                    r.quality.recall_by_idle_bucket.get("30d+", 0.0)
                )
        out[policy] = {
            name: {kk: _mean(vv) for kk, vv in m.items()} for name, m in acc.items()
        }
    return out


def _fmt_prune(gate_out: Dict[str, Dict[str, dict]]) -> str:
    md = "## Prune-gate ablation (recency-only vs DECISION §4 three-condition)\n\n"
    md += "| strategy | gate | precision | recall | false-forget | post-prune recall | retained |\n"
    md += "|---|---|---|---|---|---|---|\n"
    for name in DECAY:
        for gate in ("recency", "three_condition"):
            m = gate_out[gate].get(name, {})
            md += (
                f"| {name} | {gate} | {m.get('prune_precision', 0):.3f} | {m.get('prune_recall', 0):.3f} | "
                f"{m.get('false_forget', 0):.1f} | {m.get('post_prune_recall', 0):.3f} | "
                f"{m.get('retained_final', 0):.0f} |\n"
            )
    return md


def _fmt_reinforce(tag: str, out: Dict[str, Dict[str, dict]]) -> str:
    md = f"## Reinforcement ablation -- {tag} (all-top-k vs valid-only)\n\n"
    md += "| strategy | policy | recall | stale-hit | stale-hit (versioned) | idle-30d+ |\n"
    md += "|---|---|---|---|---|---|\n"
    for name in ["no_decay"] + DECAY:
        for policy in ("all_topk", "valid_only"):
            m = out[policy].get(name, {})
            md += (
                f"| {name} | {policy} | {m.get('recall_at_k', 0):.3f} | {m.get('stale_hit_rate', 0):.3f} | "
                f"{m.get('stale_hit_versioned', 0):.3f} | {m.get('idle30', 0):.3f} |\n"
            )
    return md


def main() -> None:
    parser = argparse.ArgumentParser(description="SM-R5 Phase-2 ablations")
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument(
        "--embedding",
        action="store_true",
        help="also run reinforcement under real embeddings",
    )
    args = parser.parse_args()

    seeds = list(range(args.seeds))
    gate_out = _prune_gate_ablation(seeds=seeds, k=args.k)
    reinforce_syn = _reinforce_ablation(seeds=seeds, k=args.k, relevance="synthetic")

    md = "# SM-R5 Phase-2 ablations\n\n"
    md += f"Seeds: {seeds}  |  k = {args.k}\n\n"
    md += _fmt_prune(gate_out) + "\n"
    md += _fmt_reinforce("synthetic", reinforce_syn) + "\n"

    payload: Dict[str, object] = {
        "seeds": seeds,
        "k": args.k,
        "prune_gate": gate_out,
        "reinforce_synthetic": reinforce_syn,
    }

    if args.embedding:
        reinforce_emb = _reinforce_ablation(
            seeds=seeds, k=args.k, relevance="embedding"
        )
        md += _fmt_reinforce("embedding", reinforce_emb) + "\n"
        payload["reinforce_embedding"] = reinforce_emb

    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "ablations.json").write_text(json.dumps(payload, indent=2))
    (RESULTS_DIR / "ablations.md").write_text(md)
    print(md)
    print(f"Wrote {RESULTS_DIR / 'ablations.json'} and {RESULTS_DIR / 'ablations.md'}")


async def amain() -> None:
    """Async entry point (mirrors :func:`main`)."""
    main()


if __name__ == "__main__":
    asyncio.run(amain())
