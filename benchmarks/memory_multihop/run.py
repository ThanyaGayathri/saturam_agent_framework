"""End-to-end multi-hop spike runner.

    build eval set -> index (per method) -> retrieve -> answer -> score

Every method is driven through the same ``MultiHopRetriever`` seam, so the numbers
are comparable and the only difference is the retrieval strategy.

Examples
--------
Smoke (one question, prints the chain, cheap):
    PYTHONPATH=. ../../.venvs/multihop/bin/python run.py --methods vector hippo --limit 1 --demo

Full run (all 30 questions, both baselines + PPR + hybrid):
    PYTHONPATH=. ../../.venvs/multihop/bin/python run.py --methods vector hippo hybrid

Offline replay against the warm cache (no credentials, deterministic):
    HARNESS_LLM_MODE=replay HARNESS_EMBED_MODE=replay PYTHONPATH=. \
        ../../.venvs/multihop/bin/python run.py --methods vector hippo hybrid
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List

from harness import metrics
from harness.dataset import EvalItem, EvalSet, load_eval_set
from harness.embeddings import CachedEmbedder
from harness.fusion import build_fusion
from harness.llm import LLMClient, Usage, load_env
from harness.reader import answer as read_answer
from harness.retrievers import (
    HippoRetriever,
    HybridRetriever,
    Retrieved,
    VectorRetriever,
)

HERE = Path(__file__).resolve().parent
CHAINING = ("compositional", "inference", "bridge_comparison")


def build_retriever(
    name: str,
    llm: LLMClient,
    embedder: CachedEmbedder,
    link_threshold: float = 0.5,
    fusion: str = "rrf",
    vector_weight: float = 0.5,
):
    """Build a retrieval strategy.

    ``link_threshold`` (query-entity -> KG-node cosine cutoff) is **embedder-specific**
    and must be passed explicitly rather than inherited: see `tau_sweep.py`, where the
    value tuned for `text-embedding-3-small` (0.5) costs ~0.18 recall@5 under SM-R4's
    `bge-base`, whose random entity-pair cosines sit far higher (p50 0.41 vs the 0.5
    cutoff, so ~5% of all pairs falsely qualify as links and mis-seed PPR).
    """
    if name == "vector":
        return VectorRetriever(embedder=embedder)
    if name == "hippo":
        return HippoRetriever(llm=llm, embedder=embedder, link_threshold=link_threshold)
    if name == "hybrid":
        return HybridRetriever(
            VectorRetriever(embedder=embedder),
            HippoRetriever(llm=llm, embedder=embedder, link_threshold=link_threshold),
            fusion=build_fusion(fusion, vector_weight=vector_weight),
        )
    raise ValueError(f"unknown method {name}")


def score_item(retrieved: List[Retrieved], item: EvalItem, k: int) -> Dict:
    titles = [r.title for r in retrieved]
    return {
        "recall@k": metrics.recall_at_k(titles, item.gold_titles, k),
        "all_hops_hit": metrics.all_hops_hit(titles, item.gold_titles, k),
    }


def aggregate(rows: List[Dict], key: str, items: List[EvalItem]) -> Dict:
    vals = [r[key] for r in rows]
    lo, hi = metrics.bootstrap_ci(vals)
    return {"mean": round(metrics.mean(vals), 4), "ci95": [lo, hi], "n": len(vals)}


def run_method(
    name: str,
    es: EvalSet,
    k: int,
    llm: LLMClient,
    embedder: CachedEmbedder,
    limit: int,
    demo: bool,
    retrieval_only: bool = False,
    link_threshold: float = 0.5,
    fusion: str = "rrf",
    vector_weight: float = 0.5,
) -> Dict:
    from harness import openie, reader

    texts = {p.pid: p.text for p in es.passages}
    retriever = build_retriever(name, llm, embedder, link_threshold, fusion, vector_weight)
    print(f"\n=== {name}: indexing {len(es.passages)} passages ===")
    reader_fails_before = len(reader.READER_FAILURES)
    fails_before = len(openie.EXTRACTION_FAILURES)
    retriever.index(es.passages)
    extraction_failures = len(openie.EXTRACTION_FAILURES) - fails_before
    print(
        "  index stats:",
        json.dumps(retriever.stats.to_dict()),
        "| openie_failures:",
        extraction_failures,
    )

    items = es.items[:limit] if limit else es.items
    read_usage = Usage()
    per_item: List[Dict] = []
    lat: List[float] = []
    for it in items:
        t0 = time.perf_counter()
        retrieved = retriever.retrieve(it.question, k)
        lat.append(time.perf_counter() - t0)
        # Retrieval metrics are scored against the dataset's gold titles and need no
        # LLM; only the reader (EM/F1/cover) does. --retrieval-only therefore lets the
        # dense leg be re-measured on a host with no LLM credentials.
        pred = "" if retrieval_only else read_answer(it.question, retrieved, texts, llm, read_usage)
        row = score_item(retrieved, it, k)
        row.update(
            {
                "qid": it.qid,
                "qtype": it.qtype,
                "em": metrics.exact_match(pred, it.answer),
                "f1": metrics.token_f1(pred, it.answer),
                "cover": metrics.cover_match(pred, it.answer),
                "pred": pred,
                "gold": it.answer,
                "retrieved_titles": [r.title for r in retrieved],
            }
        )
        per_item.append(row)
        if demo:
            print(f"\n  Q ({it.qtype}): {it.question}")
            print(f"  gold passages: {it.gold_titles}")
            print(f"  retrieved:     {[r.title for r in retrieved]}")
            print(
                f"  pred: {pred!r}  gold: {it.answer!r}  "
                f"recall@{k}={row['recall@k']:.2f} allhops={row['all_hops_hit']:.0f} f1={row['f1']:.2f}"
            )

    chaining = [r for r in per_item if r["qtype"] in CHAINING]
    summary = {
        "method": name,
        "k": k,
        "n_items": len(per_item),
        # Which embedder produced these numbers — SM-R9's original run did not record
        # this, which is how a non-SM-R4 embedder went unnoticed to review (PR #15).
        "embedder": embedder.spec.tag,
        "retrieval_only": retrieval_only,
        "link_threshold": link_threshold,
        "fusion": getattr(getattr(retriever, "fusion", None), "name", None),
        "index_stats": retriever.stats.to_dict(),
        "openie_failures": extraction_failures,
        # Reader calls the provider's content filter refused. Scored as no-answer
        # (EM/F1 = 0), so EM/F1 are a *lower bound* when this is non-zero.
        "reader_content_filtered": len(reader.READER_FAILURES) - reader_fails_before,
        "reader_usage": read_usage.to_dict(),
        "query_latency_s": {
            "mean": round(metrics.mean(lat), 3),
            "p95": round(sorted(lat)[max(0, int(0.95 * len(lat)) - 1)], 3) if lat else 0.0,
        },
        "overall": {m: aggregate(per_item, m, items) for m in ("recall@k", "all_hops_hit", "em", "f1", "cover")},
        "chaining_only": {m: aggregate(chaining, m, items) for m in ("recall@k", "all_hops_hit", "em", "f1", "cover")}
        if chaining
        else {},
    }
    return {"summary": summary, "per_item": per_item}


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the multi-hop retrieval spike.")
    ap.add_argument("--methods", nargs="+", default=["vector", "hippo", "hybrid"])
    ap.add_argument("--eval", type=Path, default=HERE / "data" / "eval_set.json")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0, help="cap questions (0 = all)")
    ap.add_argument("--demo", action="store_true", help="print each question's chain")
    ap.add_argument("--tag", type=str, default="", help="label for the results filename")
    ap.add_argument(
        "--retrieval-only",
        action="store_true",
        help="skip the LLM reader; score retrieval metrics only (runs without LLM credentials)",
    )
    ap.add_argument(
        "--link-threshold",
        type=float,
        default=0.5,
        help="query-entity->KG-node cosine cutoff; EMBEDDER-SPECIFIC (see tau_sweep.py). "
        "0.5 was tuned for text-embedding-3-small; ~0.65 fits bge-base-en-v1.5.",
    )
    ap.add_argument(
        "--fusion",
        choices=["rrf", "weighted"],
        default="rrf",
        help="hybrid fusion method. SM-R8 ratified 'weighted' for its 2-leg case; 'rrf' is "
        "SM-R9's original prototype default.",
    )
    ap.add_argument(
        "--vector-weight",
        type=float,
        default=0.5,
        help="weight on the dense leg for --fusion weighted (PPR leg gets 1-w).",
    )
    args = ap.parse_args()

    load_env()
    es = load_eval_set(args.eval)
    # One shared LLM + embedder (with caches) so cost accounting and reuse are honest,
    # and so we never build agents/models inside the loop.
    llm = LLMClient()
    embedder = CachedEmbedder()
    print(f"embedder: {embedder.spec.tag}  (SM-R4 ratified: see harness/embeddings.py)")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = f"_{args.tag}" if args.tag else ""
    out_json = HERE / "results" / f"results_{stamp}{tag}.json"
    detail_json = HERE / "results" / f"detail_{stamp}{tag}.json"

    def _flush(results: Dict) -> None:
        """Persist after every method so a later failure can't discard earlier work."""
        out_json.write_text(json.dumps({m: r["summary"] for m, r in results.items()}, indent=2))
        detail_json.write_text(json.dumps({m: r["per_item"] for m, r in results.items()}, indent=2, ensure_ascii=False))

    results: Dict = {}
    for name in args.methods:
        results[name] = run_method(
            name,
            es,
            args.k,
            llm,
            embedder,
            args.limit,
            args.demo,
            args.retrieval_only,
            args.link_threshold,
            args.fusion,
            args.vector_weight,
        )
        _flush(results)

    from harness import reader

    if reader.READER_FAILURES:
        print(f"\nreader calls refused by the provider content filter: {len(reader.READER_FAILURES)}")
        for f in reader.READER_FAILURES:
            print(f"  - {f['reason']}: {f['tag']}")

    print("\nWrote", out_json)
    _print_table(results, args.k)


def _print_table(results: Dict, k: int) -> None:
    print(f"\n{'method':8} {'recall@k':>9} {'allhops':>8} {'EM':>6} {'F1':>6}  (chaining: allhops / F1)")
    for name, r in results.items():
        s = r["summary"]
        o = s["overall"]
        c = s.get("chaining_only") or o
        print(
            f"{name:8} {o['recall@k']['mean']:>9.3f} {o['all_hops_hit']['mean']:>8.3f} "
            f"{o['em']['mean']:>6.3f} {o['f1']['mean']:>6.3f}   "
            f"{c['all_hops_hit']['mean']:.3f} / {c['f1']['mean']:.3f}"
        )


if __name__ == "__main__":
    main()
