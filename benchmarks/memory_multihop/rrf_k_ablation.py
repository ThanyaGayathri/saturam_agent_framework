"""Ablate `rrf_k` to test *why* RRF beat weighted fusion on the dense+PPR leg pair.

`results/summary_fusion.md` proposes a mechanism: RRF's advantage comes from its **flat
rank decay**, which makes fused score depend mostly on whether a passage appears in *both*
legs (cross-leg agreement) rather than on how highly either leg ranked it. Weighted min-max
is steep, so one leg's top hit dominates.

`rrf_k` controls exactly that flatness:

    contribution(rank) = 1 / (k + rank)
    flatness = contribution(1) / contribution(10) = (k + 10) / (k + 1)

  k=0    -> ratio 10.0  (very steep; behaves like a rank-weighted vote)
  k=1    -> ratio  5.5  (comparable steepness to weighted min-max, ~6.7)
  k=60   -> ratio  1.15 (near-flat; the default)
  k=1000 -> ratio  1.01 (effectively a pure set-intersection vote)

**The prediction:** if the mechanism is right, retrieval quality should *increase* with k
(flatter = more agreement-driven) and low k should collapse toward weighted's numbers. If
quality is flat in k, or peaks at low k, the explanation is wrong and should be retracted.

Retrieval-only and fully cache-replayed — no LLM spend once the corpus OpenIE pass is warm.

    PYTHONPATH=. python rrf_k_ablation.py --tag smR4ModeA
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
RRF_KS = [0, 1, 2, 5, 10, 20, 60, 200, 1000, 10000]


def _per_item(retriever, es, k: int):
    rec, ah, chain = [], [], []
    for it in es.items:
        titles = [r.title for r in retriever.retrieve(it.question, k)]
        rec.append(metrics.recall_at_k(titles, it.gold_titles, k))
        ah.append(metrics.all_hops_hit(titles, it.gold_titles, k))
        chain.append(it.qtype in CHAINING)
    return np.array(rec), np.array(ah), np.array(chain)


def _both_legs_rate(vec, hip, es, k: int, pool: int = 10) -> float:
    """Share of gold passages that appear in BOTH legs' pools.

    If the agreement story holds, this is the population RRF's flatness exploits: passages
    both legs saw but neither ranked top.
    """
    both = total = 0
    for it in es.items:
        a = {r.title for r in vec.retrieve(it.question, pool)}
        b = {r.title for r in hip.retrieve(it.question, pool)}
        for g in it.gold_titles:
            total += 1
            if g in a and g in b:
                both += 1
    return round(both / total, 4) if total else 0.0


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

    vec = VectorRetriever(embedder=embedder)
    hip = HippoRetriever(llm=llm, embedder=embedder, link_threshold=args.link_threshold)
    vec.index(es.passages)
    hip.index(es.passages)

    agree = _both_legs_rate(vec, hip, es, args.k)
    print(f"gold passages present in BOTH legs' pools: {agree:.1%}")

    rows: List[Dict] = []
    for k_ in RRF_KS:
        h = HybridRetriever(vec, hip, fusion=RRFFusion(k=k_))
        rec, ah, chain = _per_item(h, es, args.k)
        flat = (k_ + 10) / (k_ + 1)
        row = {
            "rrf_k": k_,
            "flatness_ratio": round(flat, 3),
            "recall@k": round(float(rec.mean()), 4),
            "all_hops_hit": round(float(ah.mean()), 4),
            "chaining_all_hops_hit": round(float(ah[chain].mean()), 4),
        }
        rows.append(row)
        print(
            f"  rrf_k={k_:<6} flatness={flat:6.2f}  recall@5={row['recall@k']:.4f} "
            f"all_hops={row['all_hops_hit']:.4f}  chaining_all_hops={row['chaining_all_hops_hit']:.4f}"
        )

    # Weighted reference points (steep) for the low-k comparison.
    wref = {}
    for w in (0.0, 0.5):
        h = HybridRetriever(vec, hip, fusion=WeightedFusion(vector_weight=w))
        rec, ah, _ = _per_item(h, es, args.k)
        wref[f"weighted(w={w})"] = {"recall@k": round(float(rec.mean()), 4), "all_hops_hit": round(float(ah.mean()), 4)}
    print("  reference:", json.dumps(wref))

    # Verdict: does quality rise with flatness?
    ks = [r["rrf_k"] for r in rows]
    rec_v = [r["recall@k"] for r in rows]
    ah_v = [r["all_hops_hit"] for r in rows]
    logk = np.log1p(np.array(ks, dtype=float))
    corr_rec = float(np.corrcoef(logk, rec_v)[0, 1])
    corr_ah = float(np.corrcoef(logk, ah_v)[0, 1])
    best_k_rec = ks[int(np.argmax(rec_v))]
    verdict = {
        "corr_log_k_vs_recall": round(corr_rec, 4),
        "corr_log_k_vs_all_hops": round(corr_ah, 4),
        "best_k_recall": best_k_rec,
        "best_k_all_hops": ks[int(np.argmax(ah_v))],
        "gold_in_both_legs_rate": agree,
        "supports_flatness_hypothesis": bool(corr_rec > 0.5 and best_k_rec >= 20),
    }
    print("\nverdict:", json.dumps(verdict, indent=1))

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = f"_{args.tag}" if args.tag else ""
    out = HERE / "results" / f"rrf_k_ablation_{stamp}{tag}.json"
    out.write_text(
        json.dumps(
            {
                "embedder": embedder.spec.tag,
                "k": args.k,
                "link_threshold": args.link_threshold,
                "sweep": rows,
                "weighted_reference": wref,
                "verdict": verdict,
                "llm_usage": usage.to_dict(),
            },
            indent=2,
        )
    )
    print("Wrote", out)


if __name__ == "__main__":
    main()
