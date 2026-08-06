"""Sensitivity of PPR retrieval to the two embedder-specific cosine thresholds.

`synonym_tau` (entity--entity synonymy edges) and `link_threshold` (query-entity ->
KG-node linking) are **absolute cosine thresholds**. They were tuned against
`text-embedding-3-small`; SM-R4's ratified embedder has a different cosine scale, so
carrying the values over unchanged is an assumption, not a neutral default (PR #15
review). This measures how much that assumption is worth.

Cheap to run: OpenIE triples and query NER replay from `results/llm_cache/`, entity
vectors come from the local Mode-A embedder, and only retrieval metrics are scored
(no reader), so a full sweep costs no LLM spend once the main run has warmed the cache.

    PYTHONPATH=. python tau_sweep.py --tag smR4ModeA
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
from harness.kg import KnowledgeGraph
from harness.llm import LLMClient, Usage, load_env
from harness.openie import extract_query_entities, extract_triples
from harness.retrievers import HippoRetriever, _passage_repr

HERE = Path(__file__).resolve().parent
CHAINING = ("compositional", "inference", "bridge_comparison")

SYNONYM_TAUS = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
LINK_THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]


def _per_item(retriever: HippoRetriever, es, k: int, q_entities: Dict[str, List[str]]):
    """Per-question (recall@k, all_hops_hit) — needed for honest LOOCV selection."""
    rec, ah = [], []
    for it in es.items:
        ents = q_entities[it.qid] or [it.question]
        qv = retriever.embedder.embed(ents)
        seed = retriever._seed_from_entities(ents, qv)
        if not seed:
            seed = retriever._seed_from_entities([it.question], retriever.embedder.embed([it.question]))
        titles = [r.title for r in retriever._finalize_ranks(seed, k)]
        rec.append(metrics.recall_at_k(titles, it.gold_titles, k))
        ah.append(metrics.all_hops_hit(titles, it.gold_titles, k))
    return np.array(rec), np.array(ah)


def _loocv(per_threshold: Dict[float, np.ndarray], thresholds: List[float]) -> Dict:
    """Leave-one-out threshold selection.

    Tuning `link_threshold` on the same 30 questions it is then scored on is
    in-sample optimisation and will read high. For each held-out question, pick the
    threshold that maximises the mean over the *other* 29, then score only the held-out
    one. The average is an honest out-of-sample estimate of "re-tune per embedder".
    """
    mat = np.vstack([per_threshold[t] for t in thresholds])  # (n_thresholds, n_items)
    n = mat.shape[1]
    held, picks = [], []
    for i in range(n):
        others = np.delete(mat, i, axis=1).mean(axis=1)
        best = int(np.argmax(others))
        picks.append(thresholds[best])
        held.append(mat[best, i])
    return {
        "loocv_mean": round(float(np.mean(held)), 4),
        "in_sample_best": round(float(mat.mean(axis=1).max()), 4),
        "in_sample_best_threshold": thresholds[int(np.argmax(mat.mean(axis=1)))],
        "selected_thresholds": sorted({round(p, 2) for p in picks}),
    }


def _score(retriever: HippoRetriever, es, k: int, q_entities: Dict[str, List[str]]) -> Dict:
    rec, ah, rec_c, ah_c = [], [], [], []
    for it in es.items:
        ents = q_entities[it.qid] or [it.question]
        qv = retriever.embedder.embed(ents)
        seed = retriever._seed_from_entities(ents, qv)
        if not seed:
            seed = retriever._seed_from_entities([it.question], retriever.embedder.embed([it.question]))
        titles = [r.title for r in retriever._finalize_ranks(seed, k)]
        r = metrics.recall_at_k(titles, it.gold_titles, k)
        a = metrics.all_hops_hit(titles, it.gold_titles, k)
        rec.append(r)
        ah.append(a)
        if it.qtype in CHAINING:
            rec_c.append(r)
            ah_c.append(a)
    return {
        "recall@k": round(metrics.mean(rec), 4),
        "all_hops_hit": round(metrics.mean(ah), 4),
        "chaining_recall@k": round(metrics.mean(rec_c), 4),
        "chaining_all_hops_hit": round(metrics.mean(ah_c), 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval", type=Path, default=HERE / "data" / "eval_set.json")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--tag", type=str, default="")
    args = ap.parse_args()

    load_env()
    es = load_eval_set(args.eval)
    llm, usage = LLMClient(), Usage()
    embedder = CachedEmbedder()
    print(f"embedder: {embedder.spec.tag}")

    # Extract once — triples and query NER are tau-independent (cache replay).
    triples = {p.pid: extract_triples(_passage_repr(p), llm, usage) for p in es.passages}
    q_entities = {it.qid: extract_query_entities(it.question, llm, usage) for it in es.items}

    # Entity set and its vectors are also tau-independent: embed once, reuse per tau.
    base = KnowledgeGraph()
    for p in es.passages:
        base.add_passage(p.pid, triples[p.pid])
    keys = base.entity_keys()
    mat = embedder.embed([base.entity_display[k] for k in keys])
    ent_vecs = {k: mat[i] for i, k in enumerate(keys)}
    print(f"KG: {len(keys)} entities | LLM usage this run: {usage.to_dict()}")

    # Cosine distribution of the real entity set — the distribution the thresholds sit in.
    sims = mat @ mat.T
    iu = np.triu_indices(len(keys), k=1)
    off = sims[iu]
    dist = {
        "n_entities": len(keys),
        "n_pairs": int(off.size),
        "p50": round(float(np.percentile(off, 50)), 4),
        "p90": round(float(np.percentile(off, 90)), 4),
        "p99": round(float(np.percentile(off, 99)), 4),
        "p99_9": round(float(np.percentile(off, 99.9)), 4),
        "max": round(float(off.max()), 4),
    }
    print("entity-entity cosine distribution:", json.dumps(dist))

    rows = []
    for tau in SYNONYM_TAUS:
        kg = KnowledgeGraph()
        for p in es.passages:
            kg.add_passage(p.pid, triples[p.pid])
        n_before = kg.graph.number_of_edges()
        kg.finalize(ent_vecs, synonym_tau=tau)
        syn_edges = kg.graph.number_of_edges() - n_before

        r = HippoRetriever(llm=llm, embedder=embedder, synonym_tau=tau)
        r.kg = kg
        r._entity_keys, r._entity_matrix = keys, mat
        r._pid_to_title = {p.pid: p.title for p in es.passages}
        row = {
            "synonym_tau": tau,
            "link_threshold": 0.5,
            "synonym_edges": syn_edges,
            **_score(r, es, args.k, q_entities),
        }
        rows.append(row)
        print(
            f"  tau={tau:.2f} syn_edges={syn_edges:6d} -> {json.dumps({k: v for k, v in row.items() if '@' in k or 'hops' in k})}"
        )

    # link_threshold sweep at the inherited tau.
    kg = KnowledgeGraph()
    for p in es.passages:
        kg.add_passage(p.pid, triples[p.pid])
    kg.finalize(ent_vecs, synonym_tau=0.85)
    link_rows = []
    rec_by_t: Dict[float, np.ndarray] = {}
    ah_by_t: Dict[float, np.ndarray] = {}
    for lt in LINK_THRESHOLDS:
        r = HippoRetriever(llm=llm, embedder=embedder, synonym_tau=0.85, link_threshold=lt)
        r.kg = kg
        r._entity_keys, r._entity_matrix = keys, mat
        r._pid_to_title = {p.pid: p.title for p in es.passages}
        rec_by_t[lt], ah_by_t[lt] = _per_item(r, es, args.k, q_entities)
        row = {"synonym_tau": 0.85, "link_threshold": lt, **_score(r, es, args.k, q_entities)}
        link_rows.append(row)
        print(f"  link_threshold={lt:.2f} -> {json.dumps({k: v for k, v in row.items() if '@' in k or 'hops' in k})}")

    loocv = {
        "recall@k": _loocv(rec_by_t, LINK_THRESHOLDS),
        "all_hops_hit": _loocv(ah_by_t, LINK_THRESHOLDS),
    }
    print("\nleave-one-out threshold selection (honest out-of-sample):")
    print("  recall@k:     ", json.dumps(loocv["recall@k"]))
    print("  all_hops_hit: ", json.dumps(loocv["all_hops_hit"]))

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = f"_{args.tag}" if args.tag else ""
    out = HERE / "results" / f"tau_sweep_{stamp}{tag}.json"
    out.write_text(
        json.dumps(
            {
                "embedder": embedder.spec.tag,
                "k": args.k,
                "entity_cosine_distribution": dist,
                "synonym_tau_sweep": rows,
                "link_threshold_sweep": link_rows,
                "loocv_link_threshold": loocv,
                "llm_usage": usage.to_dict(),
            },
            indent=2,
        )
    )
    print("Wrote", out)


if __name__ == "__main__":
    main()
