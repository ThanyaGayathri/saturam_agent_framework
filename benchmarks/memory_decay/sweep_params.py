"""Parameter-sensitivity sweeps for the decay candidates.

For each tunable parameter we sweep a range (holding others at the provisional
default) and record nDCG@k and stale-hit-rate, averaged over seeds. This answers
"how sensitive is the decision to the parameter values?" for DECISION.md.

Usage:
    ./.venv/bin/python sweep_params.py --seeds 5

Writes results/sensitivity.json and results/sensitivity.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from harness.simulate import run_simulation
from harness.strategies import (
    DecayStrategy,
    Ebbinghaus,
    ExpRecency,
    ImportanceWeighted,
    TtlHalflife,
)
from harness.stream import generate_stream

RESULTS_DIR = Path(__file__).parent / "results"

DAY = 24.0


def _mean(values: List[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _eval_factory(
    seeds: List[int], k: int, make: Callable[[float], DecayStrategy]
) -> Callable[[float], dict]:
    """Return a function mapping a parameter value to averaged metrics."""
    streams = [generate_stream(seed=s) for s in seeds]

    def evaluate(value: float) -> dict:
        ndcgs: List[float] = []
        stale: List[float] = []
        for stream in streams:
            strategy = make(
                value
            )  # fresh strategy per stream; never reused across seeds
            result = run_simulation(stream=stream, strategy=strategy, k=k)
            ndcgs.append(result.quality.ndcg_at_k)
            stale.append(result.quality.stale_hit_rate)
        return {"ndcg_at_k": _mean(ndcgs), "stale_hit_rate": _mean(stale)}

    return evaluate


def _sweep(seeds: List[int], k: int) -> Dict[str, dict]:
    sweeps: Dict[str, dict] = {}

    # Each grid: label -> (parameter values, factory building a strategy at a value).
    grids: List[Tuple[str, List[float], Callable[[float], DecayStrategy]]] = [
        (
            "exp_recency.base",
            [0.990, 0.993, 0.995, 0.997, 0.999],
            lambda v: ExpRecency(base=float(v)),
        ),
        (
            "exp_recency.gamma",
            [0.0, 0.25, 0.5, 0.75, 1.0],
            lambda v: ExpRecency(gamma=float(v)),
        ),
        (
            "ttl_halflife.half_life_days",
            [1.0, 3.0, 7.0, 14.0, 30.0],
            lambda v: TtlHalflife(half_life_hours=v * DAY),
        ),
        (
            "importance_weighted.imp_factor",
            [0.0, 2.0, 4.0, 8.0, 16.0],
            lambda v: ImportanceWeighted(imp_factor=float(v)),
        ),
        (
            "importance_weighted.base_half_life_days",
            [2.0, 3.0, 5.0, 7.0, 14.0],
            lambda v: ImportanceWeighted(base_half_life_hours=v * DAY),
        ),
        (
            "ebbinghaus.s0_days",
            [1.0, 3.0, 5.0, 10.0, 20.0],
            lambda v: Ebbinghaus(s0_hours=v * DAY),
        ),
        (
            "ebbinghaus.reinforce_gain_days",
            [0.0, 2.0, 5.0, 10.0, 20.0],
            lambda v: Ebbinghaus(reinforce_gain_hours=v * DAY),
        ),
    ]

    for label, values, make in grids:
        evaluate = _eval_factory(seeds=seeds, k=k, make=make)
        points = []
        for value in values:
            metrics = evaluate(value)
            points.append({"value": value, **metrics})
        ndcg_vals = [pt["ndcg_at_k"] for pt in points]
        sweeps[label] = {
            "points": points,
            "ndcg_spread": max(ndcg_vals) - min(ndcg_vals),
        }
    return sweeps


def _fmt(sweeps: Dict[str, dict]) -> str:
    md = "# Decay Parameter Sensitivity\n\n"
    for label, data in sweeps.items():
        md += f"## {label}\n\n"
        md += "| value | nDCG@k | stale-hit-rate |\n|---|---|---|\n"
        for pt in data["points"]:
            md += f"| {pt['value']} | {pt['ndcg_at_k']:.3f} | {pt['stale_hit_rate']:.3f} |\n"
        md += f"\nnDCG spread across sweep: {data['ndcg_spread']:.3f}\n\n"
    return md


def main() -> None:
    parser = argparse.ArgumentParser(description="Decay parameter sensitivity (SM-R5)")
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    seeds = list(range(args.seeds))
    sweeps = _sweep(seeds=seeds, k=args.k)

    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "sensitivity.json").write_text(
        json.dumps({"seeds": seeds, "k": args.k, "sweeps": sweeps}, indent=2)
    )
    (RESULTS_DIR / "sensitivity.md").write_text(_fmt(sweeps))

    print("Parameter sensitivity sweep")
    print(_fmt(sweeps))
    print(
        f"Wrote {RESULTS_DIR / 'sensitivity.json'} and {RESULTS_DIR / 'sensitivity.md'}"
    )


async def amain() -> None:
    """Async entry point (mirrors :func:`main`)."""
    main()


if __name__ == "__main__":
    asyncio.run(amain())
