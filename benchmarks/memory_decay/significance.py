"""Paired-bootstrap significance for the headline decay comparisons.

The first pass ranked strategies on 5-seed means with no test that the gaps
exceed noise (e.g. importance_weighted nDCG 0.866 vs no_decay 0.893, or the
embedding-run recall margins). This runs a paired bootstrap over the pooled
per-query values (queries shared across strategies on the same seeds) and
Holm-corrects the family of comparisons the decision leans on.

Usage:
    ./.venv/bin/python significance.py --seeds 5                 # synthetic
    ./.venv/bin/python significance.py --seeds 5 --relevance embedding
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Dict

from harness.simulate import run_simulation
from harness.stats import holm_adjust, paired_bootstrap
from harness.strategies import default_strategies
from harness.stream import generate_stream

RESULTS_DIR = Path(__file__).parent / "results"

# The comparisons the decision leans on: importance_weighted vs each rival.
CHOSEN = "importance_weighted"


def _collect(
    seeds, k: int, relevance: str, model_name: str
) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Per-query recall and nDCG per strategy, keyed by "seed:query_id"."""
    pq: Dict[str, Dict[str, Dict[str, float]]] = {}
    for seed in seeds:
        stream = generate_stream(seed=seed)
        if relevance == "embedding":
            from harness.relevance import embed_stream

            embed_stream(stream, model_name=model_name)
        for strat in default_strategies(prune=False):
            r = run_simulation(stream=stream, strategy=strat, k=k)
            slot = pq.setdefault(strat.name, {"recall": {}, "ndcg": {}})
            for qid, val in r.per_query_recall.items():
                slot["recall"][f"{seed}:{qid}"] = val
            for qid, val in r.per_query_ndcg.items():
                slot["ndcg"][f"{seed}:{qid}"] = val
    return pq


def _run(seeds, k: int, relevance: str, model_name: str) -> dict:
    pq = _collect(seeds, k=k, relevance=relevance, model_name=model_name)
    rivals = [n for n in pq if n != CHOSEN]

    comparisons = []
    pfamily: Dict[str, float] = {}
    for metric in ("recall", "ndcg"):
        for rival in rivals:
            pr = paired_bootstrap(pq[CHOSEN][metric], pq[rival][metric])
            label = f"{CHOSEN} vs {rival} [{metric}]"
            comparisons.append(
                {
                    "comparison": label,
                    "metric": metric,
                    "rival": rival,
                    "delta": round(pr.delta, 4),
                    "ci_lo": round(pr.lo, 4),
                    "ci_hi": round(pr.hi, 4),
                    "p_raw": round(pr.p_value, 4),
                    "n": pr.n,
                }
            )
            pfamily[label] = pr.p_value

    holm = holm_adjust(pfamily)
    for c in comparisons:
        label = str(c["comparison"])
        c["p_holm"] = holm[label]["p_holm"]
        c["significant"] = holm[label]["significant"]
    return {"comparisons": comparisons}


def _fmt(relevance: str, result: dict) -> str:
    md = f"# Paired-bootstrap significance -- {relevance}\n\n"
    md += f"Chosen = {CHOSEN}. Paired bootstrap over pooled per-query values; "
    md += "Holm-corrected over the family. delta = chosen - rival (positive favours chosen).\n\n"
    md += "| comparison | delta | 95% CI | p (Holm) | significant |\n|---|---|---|---|---|\n"
    for c in result["comparisons"]:
        md += (
            f"| {c['comparison']} | {c['delta']:+.4f} | [{c['ci_lo']:+.3f}, {c['ci_hi']:+.3f}] | "
            f"{c['p_holm']:.4f} | {'yes' if c['significant'] else 'NO (tie)'} |\n"
        )
    return md


def main() -> None:
    parser = argparse.ArgumentParser(description="Decay significance testing (SM-R5)")
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument(
        "--relevance", choices=["synthetic", "embedding"], default="synthetic"
    )
    parser.add_argument(
        "--embedding-model", default="sentence-transformers/all-MiniLM-L6-v2"
    )
    args = parser.parse_args()

    seeds = list(range(args.seeds))
    result = _run(
        seeds, k=args.k, relevance=args.relevance, model_name=args.embedding_model
    )

    suffix = "_embedding" if args.relevance == "embedding" else ""
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f"significance{suffix}.json").write_text(
        json.dumps(
            {"seeds": seeds, "k": args.k, "relevance": args.relevance, **result},
            indent=2,
        )
    )
    md = _fmt(args.relevance, result)
    (RESULTS_DIR / f"significance{suffix}.md").write_text(md)
    print(md)
    print(f"Wrote results/significance{suffix}.md")


async def amain() -> None:
    """Async entry point (mirrors :func:`main`)."""
    main()


if __name__ == "__main__":
    asyncio.run(amain())
