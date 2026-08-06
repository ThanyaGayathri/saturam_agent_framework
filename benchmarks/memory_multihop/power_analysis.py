"""How many questions does the adopt threshold actually need?

PR #15's review flagged that the GO leans on `all-hops-hit`, whose CIs overlap at n=30, and
asked for n>=100 **or a power analysis**. This is the power analysis, so the requirement is
grounded in a number rather than a round guess.

`all-hops-hit` is Bernoulli per question and the two methods are scored on the **same**
questions, so the right test is paired: McNemar's / a paired bootstrap on the per-question
difference. Power therefore depends on the **discordant pairs** (questions where exactly one
method succeeds), not on the marginal rates — a distinction that matters here, because the two
retrievers agree on most questions and the effective sample is much smaller than n.

Two estimates are produced:

1. **Empirical** — resample the observed per-question outcome pairs with replacement at
   various n and count how often the paired test clears p<0.05. Makes no distributional
   assumption beyond "future questions look like these".
2. **Analytic** — a normal-approximation McNemar power calculation from the observed
   discordance, as a sanity check on (1).

    PYTHONPATH=. python power_analysis.py --tag smR4ModeA
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Dict, Tuple

import numpy as np

HERE = Path(__file__).resolve().parent
CHAINING = ("compositional", "inference", "bridge_comparison")
TARGET_NS = [30, 50, 75, 100, 150, 200, 300, 500, 800]


def _load_pairs(detail_path: Path, a: str, b: str, metric: str, chaining_only: bool) -> Tuple[np.ndarray, np.ndarray]:
    d = json.loads(detail_path.read_text())
    ra = {r["qid"]: r for r in d[a]}
    rb = {r["qid"]: r for r in d[b]}
    qids = [q for q in ra if q in rb]
    if chaining_only:
        qids = [q for q in qids if ra[q]["qtype"] in CHAINING]
    return (
        np.array([ra[q][metric] for q in qids], dtype=float),
        np.array([rb[q][metric] for q in qids], dtype=float),
    )


def _paired_bootstrap_p(diff: np.ndarray, rng: np.random.Generator, iters: int = 2000) -> float:
    idx = rng.integers(0, len(diff), size=(iters, len(diff)))
    boot = diff[idx].mean(axis=1)
    return float(min(1.0, 2.0 * min((boot <= 0).mean(), (boot >= 0).mean())))


def empirical_power(a: np.ndarray, b: np.ndarray, n: int, trials: int, seed: int = 7) -> float:
    """Resample question-outcome *pairs* to size n; how often does the paired test clear .05?"""
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(trials):
        idx = rng.integers(0, len(a), size=n)
        diff = a[idx] - b[idx]
        if diff.std() == 0:
            hits += int(diff.mean() != 0)
            continue
        if _paired_bootstrap_p(diff, rng, iters=1000) < 0.05:
            hits += 1
    return round(hits / trials, 3)


def mcnemar_n_required(a: np.ndarray, b: np.ndarray, power: float = 0.8, alpha: float = 0.05) -> Dict:
    """Normal-approximation McNemar sample size from the observed discordance."""
    b01 = float(((a == 0) & (b == 1)).mean())  # only B succeeds
    b10 = float(((a == 1) & (b == 0)).mean())  # only A succeeds
    pdisc = b01 + b10
    if pdisc == 0 or b10 == b01:
        return {"discordant_rate": round(pdisc, 4), "n_required": None, "note": "no measurable discordance"}
    psi = b10 / pdisc  # share of discordant pairs favouring A
    z_a = 1.959963985  # two-sided .05
    z_b = 0.8416212336 if abs(power - 0.8) < 1e-9 else 1.2815515655  # .80 / .90
    num = (z_a / 2.0 + z_b * math.sqrt(psi * (1 - psi))) ** 2
    n_disc = num / ((psi - 0.5) ** 2)
    return {
        "only_A_succeeds": round(b10, 4),
        "only_B_succeeds": round(b01, 4),
        "discordant_rate": round(pdisc, 4),
        "psi": round(psi, 4),
        "n_discordant_required": int(math.ceil(n_disc)),
        "n_required": int(math.ceil(n_disc / pdisc)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detail", type=Path, default=HERE / "results" / "detail_20260728T112549Z_smR4ModeA_tuned.json")
    ap.add_argument("--trials", type=int, default=400)
    ap.add_argument("--tag", type=str, default="")
    args = ap.parse_args()

    report: Dict = {"source": args.detail.name, "comparisons": []}
    comparisons = [
        ("hybrid", "vector", "all_hops_hit", False, "hybrid vs dense - all-hops (the adopt metric)"),
        ("hybrid", "vector", "all_hops_hit", True, "hybrid vs dense - all-hops, chaining-only"),
        ("hybrid", "vector", "recall@k", False, "hybrid vs dense - recall@5 (already separates)"),
        ("hybrid", "hippo", "all_hops_hit", False, "hybrid vs pure PPR - all-hops"),
    ]
    for a_name, b_name, metric, chain, label in comparisons:
        a, b = _load_pairs(args.detail, a_name, b_name, metric, chain)
        obs = float((a - b).mean())
        mc = mcnemar_n_required(a, b)
        curve = {n: empirical_power(a, b, n, args.trials) for n in TARGET_NS}
        n80 = next((n for n in TARGET_NS if curve[n] >= 0.80), None)
        entry = {
            "comparison": label,
            "n_observed": int(len(a)),
            "observed_diff": round(obs, 4),
            "mcnemar": mc,
            "empirical_power_curve": curve,
            "n_for_80pct_power_empirical": n80,
        }
        report["comparisons"].append(entry)
        print(f"\n=== {label} ===")
        print(f"  observed diff {obs:+.4f} at n={len(a)} | discordant pairs {mc.get('discordant_rate')}")
        print(f"  McNemar n for 80% power: {mc.get('n_required')}")
        print("  empirical power: " + "  ".join(f"n={n}:{p:.2f}" for n, p in curve.items()))
        print(f"  -> first n with >=80% power: {n80}")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = f"_{args.tag}" if args.tag else ""
    out = HERE / "results" / f"power_analysis_{stamp}{tag}.json"
    out.write_text(json.dumps(report, indent=2))
    print("\nWrote", out)


if __name__ == "__main__":
    main()
