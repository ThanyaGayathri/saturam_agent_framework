"""Importance-confound sensitivity sweep (addresses the oracle-importance critique).

The generator assigns ``importance`` from ``kind``, and ``kind`` also fixes
validity/queryability — so at ``importance_noise = 0`` importance is a near-perfect
oracle for "durable, long-valid fact." A production importance scorer is *not* that
clean. This sweep decorrelates importance from kind (``importance_noise`` in [0, 1];
1 = fully independent) and re-runs the whole comparison, so we can see how much of
importance_weighted's advantage survives an imperfectly-calibrated signal — and give
SM-R1 a concrete "how good must the importance scorer be" threshold.

Reported per noise level: importance_weighted's recall and decay-resistance
(idle-30d+) vs the pure-recency decay strategies it must beat (exp_recency,
ebbinghaus), and the margin. If the margin vanishes as noise rises, the win depends
on importance calibration — which is the honest finding to hand SM-R1.

Usage:
    ./.venv/bin/python confound.py --seeds 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Dict, List

from run_benchmark import _build_streams, _collect

RESULTS_DIR = Path(__file__).parent / "results"

NOISE_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]
DECAY_PEERS = ["exp_recency", "ebbinghaus"]


def _idle_30d(summary: Dict[str, dict], name: str) -> float:
    return summary[name].get("recall_by_idle_bucket", {}).get("30d+", 0.0)


def _sweep(seeds: List[int], k: int) -> List[dict]:
    rows: List[dict] = []
    for noise in NOISE_GRID:
        streams = _build_streams(
            seeds=seeds, relevance="synthetic", model_name="", importance_noise=noise
        )
        ranking = _collect(streams=streams, k=k, prune=False)
        iw_recall = ranking["importance_weighted"]["recall_at_k"]
        iw_idle30 = _idle_30d(ranking, "importance_weighted")
        peer_recall = max(ranking[p]["recall_at_k"] for p in DECAY_PEERS)
        peer_idle30 = max(_idle_30d(ranking, p) for p in DECAY_PEERS)
        rows.append(
            {
                "importance_noise": noise,
                "iw_recall": iw_recall,
                "iw_idle30": iw_idle30,
                "best_peer_recall": peer_recall,
                "best_peer_idle30": peer_idle30,
                "recall_margin": iw_recall - peer_recall,
                "idle30_margin": iw_idle30 - peer_idle30,
                "no_decay_recall": ranking["no_decay"]["recall_at_k"],
                "iw_stale_versioned": ranking["importance_weighted"][
                    "stale_hit_rate_versioned"
                ],
            }
        )
    return rows


def _fmt(rows: List[dict]) -> str:
    md = "# Importance-confound sensitivity\n\n"
    md += (
        "importance_weighted vs the best pure-recency peer (exp_recency/ebbinghaus) as "
    )
    md += "importance is decorrelated from kind. margin > 0 means salience-weighting still helps.\n\n"
    md += "| importance_noise | IW recall | peer recall | recall margin | IW idle-30d+ | peer idle-30d+ | idle-30d+ margin |\n"
    md += "|---|---|---|---|---|---|---|\n"
    for r in rows:
        md += (
            f"| {r['importance_noise']:.2f} | {r['iw_recall']:.3f} | {r['best_peer_recall']:.3f} | "
            f"{r['recall_margin']:+.3f} | {r['iw_idle30']:.3f} | {r['best_peer_idle30']:.3f} | "
            f"{r['idle30_margin']:+.3f} |\n"
        )
    return md


def main() -> None:
    parser = argparse.ArgumentParser(description="Importance-confound sweep (SM-R5)")
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    seeds = list(range(args.seeds))
    rows = _sweep(seeds=seeds, k=args.k)

    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "confound.json").write_text(
        json.dumps({"seeds": seeds, "k": args.k, "rows": rows}, indent=2)
    )
    (RESULTS_DIR / "confound.md").write_text(_fmt(rows))
    print(_fmt(rows))
    print(f"Wrote {RESULTS_DIR / 'confound.json'} and {RESULTS_DIR / 'confound.md'}")


async def amain() -> None:
    """Async entry point (mirrors :func:`main`)."""
    main()


if __name__ == "__main__":
    asyncio.run(amain())
