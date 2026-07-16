"""Threshold sensitivity + noise-robustness sweeps (offline only).

Two studies, both averaged over seeds and fully deterministic:

1. **Sensitivity** — how each tunable approach responds to its main threshold
   (time-gap seconds, fixed-window size, embedding-drift ``threshold_c``, EM-LLM
   ``gamma`` and objective, and EM-LLM refine on/off). Finds the knee for the
   provisional defaults in DECISION.md.
2. **Robustness** — how the leading approaches degrade as the stream gets noisier
   (more backchannel + drift filler and fewer boundaries marked by a time gap).

Writes ``results/sensitivity.{json,md}`` and ``results/robustness.{json,md}``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from pathlib import Path
from typing import Dict, List

from harness.segmenters import (
    EmbeddingDrift,
    EmLlm,
    FixedWindow,
    Segmenter,
    TimeGap,
)
from harness.simulate import run_segmenter
from harness.stream import StreamParams, generate_stream

RESULTS_DIR = Path(__file__).parent / "results"


def _mean(xs: List[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def _eval(segmenter: Segmenter, seeds: List[int], params: StreamParams, tol: int) -> Dict[str, float]:
    f1, pk, wd, npred = [], [], [], []
    for seed in seeds:
        stream = generate_stream(seed=seed, params=params)
        r = run_segmenter(stream, segmenter, tol=tol)
        f1.append(r.f1)
        pk.append(r.pk)
        wd.append(r.window_diff)
        npred.append(float(r.n_pred + 1))
    return {"f1": _mean(f1), "pk": _mean(pk), "window_diff": _mean(wd), "segs": _mean(npred)}


def _sensitivity(seeds: List[int], tol: int) -> Dict[str, list]:
    base = StreamParams()
    out: Dict[str, list] = {}

    out["time_gap.gap_s"] = [
        {"value": g, **_eval(TimeGap(gap_s=g), seeds, base, tol)} for g in [180, 300, 600, 900, 1800]
    ]
    out["fixed_window.n"] = [{"value": n, **_eval(FixedWindow(n=n), seeds, base, tol)} for n in [5, 8, 10, 12, 15]]
    out["embedding_drift.threshold_c"] = [
        {"value": c, **_eval(EmbeddingDrift(threshold_c=c), seeds, base, tol)} for c in [0.0, 0.25, 0.5, 0.75, 1.0]
    ]
    out["em_llm.gamma"] = [{"value": g, **_eval(EmLlm(gamma=g), seeds, base, tol)} for g in [0.5, 0.75, 1.0, 1.25, 1.5]]
    out["em_llm.objective"] = [
        {"value": obj, **_eval(EmLlm(objective=obj), seeds, base, tol)} for obj in ["modularity", "conductance"]
    ]
    out["em_llm.refine"] = [{"value": ref, **_eval(EmLlm(refine=ref), seeds, base, tol)} for ref in [False, True]]
    return out


def _robustness(seeds: List[int], tol: int) -> Dict[str, list]:
    """Degradation of the leading approaches as noise rises."""
    # (name, backchannel_rate, drift_rate, p_time_gap)
    levels = [
        ("clean", 0.0, 0.0, 0.8),
        ("low", 0.08, 0.05, 0.6),
        ("medium", 0.15, 0.10, 0.5),
        ("high", 0.25, 0.18, 0.4),
    ]
    approaches = {
        "time_gap": TimeGap(),
        "fixed_window": FixedWindow(),
        "embedding_drift": EmbeddingDrift(),
        "surprise_em_llm": EmLlm(),
    }
    out: Dict[str, list] = {}
    for name, seg in approaches.items():
        rows = []
        for level_name, backchannel, drift, p_gap in levels:
            params = StreamParams(backchannel_rate=backchannel, drift_rate=drift, p_time_gap=p_gap)
            rows.append({"noise": level_name, **_eval(seg, seeds, params, tol)})
        out[name] = rows
    return out


def _fmt_sensitivity(sens: Dict[str, list]) -> str:
    md = "# Sensitivity\n\n"
    for knob, rows in sens.items():
        md += f"## {knob}\n\n"
        md += "| value | F1 | Pk | WinDiff | segs |\n|---|---|---|---|---|\n"
        for r in rows:
            md += f"| {r['value']} | {r['f1']:.3f} | {r['pk']:.3f} | {r['window_diff']:.3f} | {r['segs']:.1f} |\n"
        md += "\n"
    return md


def _fmt_robustness(rob: Dict[str, list]) -> str:
    md = "# Robustness to noise\n\n"
    md += "F1 (higher better) / Pk (lower better) as backchannel+drift rise and fewer boundaries get a time gap.\n\n"
    for name, rows in rob.items():
        md += f"## {name}\n\n"
        md += "| noise | F1 | Pk | WinDiff | segs |\n|---|---|---|---|---|\n"
        for r in rows:
            md += f"| {r['noise']} | {r['f1']:.3f} | {r['pk']:.3f} | {r['window_diff']:.3f} | {r['segs']:.1f} |\n"
        md += "\n"
    return md


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Segmentation sensitivity + robustness sweeps (SM-R6)")
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--tol", type=int, default=1)
    args = parser.parse_args(argv)
    seeds = list(range(args.seeds))

    RESULTS_DIR.mkdir(exist_ok=True)
    sens = _sensitivity(seeds, args.tol)
    rob = _robustness(seeds, args.tol)

    (RESULTS_DIR / "sensitivity.json").write_text(
        json.dumps({"seeds": seeds, "tol": args.tol, "sweeps": sens}, indent=2)
    )
    (RESULTS_DIR / "sensitivity.md").write_text(_fmt_sensitivity(sens))
    (RESULTS_DIR / "robustness.json").write_text(json.dumps({"seeds": seeds, "tol": args.tol, "levels": rob}, indent=2))
    (RESULTS_DIR / "robustness.md").write_text(_fmt_robustness(rob))

    print("Sensitivity + robustness sweeps")
    print(_fmt_sensitivity(sens))
    print(_fmt_robustness(rob))
    print("Wrote results/sensitivity.{json,md} and results/robustness.{json,md}")


async def amain(argv=None) -> None:
    """Async entry point (mirrors :func:`main`)."""
    main(argv)


if __name__ == "__main__":
    asyncio.run(amain())
