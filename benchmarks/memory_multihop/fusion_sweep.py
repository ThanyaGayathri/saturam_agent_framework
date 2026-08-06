"""Weighted-vs-RRF fusion for the dense+PPR leg pair, with an honest out-of-sample estimate.

SM-R8 ratified **weighted** fusion for its 2-leg case and explicitly declined to adopt RRF
automatically once a graph leg joins; SM-R9 originally only ever ran RRF, which PR #15's
review flagged as an unmeasured gap. This closes it: both methods, same code path, same
corpus, with `vector_weight` swept across SM-R8's range (0.3-0.7) plus the extremes.

Retrieval-only and fully cache-replayed, so it costs no LLM spend once the corpus OpenIE
pass is warm.

    PYTHONPATH=. python fusion_sweep.py --tag smR4ModeA
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List

import numpy as np

from harness import metrics
from harness.dataset import load_eval_set
from harness.embeddings import CachedEmbedder
from harness.fusion import RRFFusion, WeightedFusion
from harness.llm import LLMClient, Usage, load_env
from harness.retrievers import HippoRetriever, HybridRetriever, VectorRetriever

HERE = Path(__file__).resolve().parent
CHAINING = ("compositional", "inference", "bridge_comparison")
VECTOR_WEIGHTS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


def _per_item(retriever, es, k: int):
    rec, ah, is_chain = [], [], []
    for it in es.items:
        titles = [r.title for r in retriever.retrieve(it.question, k)]
        rec.append(metrics.recall_at_k(titles, it.gold_titles, k))
        ah.append(metrics.all_hops_hit(titles, it.gold_titles, k))
        is_chain.append(it.qtype in CHAINING)
    return np.array(rec), np.array(ah), np.array(is_chain)


def _summarize(rec, ah, chain) -> Dict:
    return {
        "recall@k": round(float(rec.mean()), 4),
        "all_hops_hit": round(float(ah.mean()), 4),
        "chaining_recall@k": round(float(rec[chain].mean()), 4),
        "chaining_all_hops_hit": round(float(ah[chain].mean()), 4),
    }


def _paired_bootstrap(a: np.ndarray, b: np.ndarray, iters: int = 10000, seed: int = 7) -> Dict:
    """Paired bootstrap on the per-question difference a - b (same test SM-R8 used).

    Resamples questions with replacement, so it accounts for the fact that both methods
    are scored on the *same* 30 items. p is the two-sided fraction of resamples whose
    mean difference has the opposite sign to (or equals) zero.
    """
    rng = np.random.default_rng(seed)
    n = len(a)
    diff = a - b
    obs = float(diff.mean())
    idx = rng.integers(0, n, size=(iters, n))
    boot = diff[idx].mean(axis=1)
    p = float(min(1.0, 2.0 * min((boot <= 0).mean(), (boot >= 0).mean())))
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {
        "mean_diff": round(obs, 4),
        "ci95": [round(float(lo), 4), round(float(hi), 4)],
        "p_two_sided": round(p, 4),
        "significant_at_05": bool(p < 0.05),
    }


def _loocv(per_w: Dict[float, np.ndarray], weights: List[float]) -> Dict:
    """Pick the weight on the other n-1 questions, score the held-out one."""
    mat = np.vstack([per_w[w] for w in weights])
    held, picks = [], []
    for i in range(mat.shape[1]):
        others = np.delete(mat, i, axis=1).mean(axis=1)
        best = int(np.argmax(others))
        picks.append(weights[best])
        held.append(mat[best, i])
    return {
        "loocv_mean": round(float(np.mean(held)), 4),
        "in_sample_best": round(float(mat.mean(axis=1).max()), 4),
        "in_sample_best_weight": weights[int(np.argmax(mat.mean(axis=1)))],
        "selected_weights": sorted({round(p, 2) for p in picks}),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval", type=Path, default=HERE / "data" / "eval_set.json")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--link-threshold", type=float, default=0.65)
    ap.add_argument("--tag", type=str, default="")
    args = ap.parse_args()

    load_env()
    es = load_eval_set(args.eval)
    llm, usage = LLMClient(), Usage()
    embedder = CachedEmbedder()
    print(f"embedder: {embedder.spec.tag} | link_threshold={args.link_threshold}")

    # Index once and reuse both legs across every fusion configuration.
    vec = VectorRetriever(embedder=embedder)
    hip = HippoRetriever(llm=llm, embedder=embedder, link_threshold=args.link_threshold)
    vec.index(es.passages)
    hip.index(es.passages)

    rows = []
    rec_by_w: Dict[float, np.ndarray] = {}
    ah_by_w: Dict[float, np.ndarray] = {}

    rrf = HybridRetriever(vec, hip, fusion=RRFFusion(k=60))
    rec, ah, chain = _per_item(rrf, es, args.k)
    rrf_row = {"fusion": "rrf(k=60)", **_summarize(rec, ah, chain)}
    print(f"  {rrf_row['fusion']:22} -> {json.dumps({k: v for k, v in rrf_row.items() if k != 'fusion'})}")

    for w in VECTOR_WEIGHTS:
        h = HybridRetriever(vec, hip, fusion=WeightedFusion(vector_weight=w))
        rec, ah, chain = _per_item(h, es, args.k)
        rec_by_w[w], ah_by_w[w] = rec, ah
        row = {"fusion": "weighted", "vector_weight": w, **_summarize(rec, ah, chain)}
        rows.append(row)
        print(
            f"  weighted(w={w:.1f})         -> {json.dumps({k: v for k, v in row.items() if k not in ('fusion', 'vector_weight')})}"
        )

    # RRF vs the best weighted setting, tested pairwise on the same questions.
    rrf_rec, rrf_ah, _ = _per_item(rrf, es, args.k)
    best_w_rec = max(VECTOR_WEIGHTS, key=lambda w: rec_by_w[w].mean())
    best_w_ah = max(VECTOR_WEIGHTS, key=lambda w: ah_by_w[w].mean())
    sig = {
        "recall@k": {"best_weighted_w": best_w_rec, **_paired_bootstrap(rrf_rec, rec_by_w[best_w_rec])},
        "all_hops_hit": {"best_weighted_w": best_w_ah, **_paired_bootstrap(rrf_ah, ah_by_w[best_w_ah])},
    }
    print("\nRRF vs best weighted (paired bootstrap, 10k resamples):")
    for m, v in sig.items():
        print(
            f"  {m:14} RRF - weighted(w={v['best_weighted_w']}) = {v['mean_diff']:+.4f} "
            f"CI{v['ci95']} p={v['p_two_sided']} sig={v['significant_at_05']}"
        )

    loocv = {"recall@k": _loocv(rec_by_w, VECTOR_WEIGHTS), "all_hops_hit": _loocv(ah_by_w, VECTOR_WEIGHTS)}
    print("\nleave-one-out weight selection (honest out-of-sample):")
    print("  recall@k:     ", json.dumps(loocv["recall@k"]))
    print("  all_hops_hit: ", json.dumps(loocv["all_hops_hit"]))

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = f"_{args.tag}" if args.tag else ""
    out = HERE / "results" / f"fusion_sweep_{stamp}{tag}.json"
    out.write_text(
        json.dumps(
            {
                "embedder": embedder.spec.tag,
                "k": args.k,
                "link_threshold": args.link_threshold,
                "rrf": rrf_row,
                "weighted_sweep": rows,
                "loocv_vector_weight": loocv,
                "rrf_vs_weighted_significance": sig,
                "llm_usage": usage.to_dict(),
            },
            indent=2,
        )
    )
    print("Wrote", out)


if __name__ == "__main__":
    main()
