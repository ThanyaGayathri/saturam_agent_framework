"""Does a distribution-relative `link_threshold` survive an embedder swap? (absolute does not)

The SM-R4 re-run showed that inheriting an absolute cosine cutoff of 0.5 — tuned on
`text-embedding-3-small` — cost pure PPR ~0.18 recall@5 under `bge-base-en-v1.5`, silently.
SPIKE.md recommends replacing it with a **percentile of the observed entity-entity cosine
distribution**, so the cutoff is defined relative to whatever scale the embedder produces.

This tests that recommendation rather than asserting it. The same KG (same cached OpenIE
triples, so graph topology is fixed) is embedded with **three** embedders spanning both of
SM-R4's ratified modes plus the original placeholder, and each is evaluated under:

  - **absolute** cutoffs (0.5 = the inherited value, 0.65 = tuned for bge-base)
  - **percentile** cutoffs, resolved per embedder at index time

A portable rule is one whose *retrieval quality* is stable across the three embedders. Spread
across embedders is the metric of interest — not the absolute level.

Needs Azure embedding credentials for the two provider models; the local Mode A model needs
none. OpenIE replays free from cache.

    PYTHONPATH=. python threshold_portability.py --tag smR4
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
from harness.embeddings import CachedEmbedder, EmbedderSpec
from harness.kg import KnowledgeGraph
from harness.llm import LLMClient, Usage, load_env
from harness.openie import extract_query_entities, extract_triples
from harness.retrievers import HippoRetriever, _passage_repr

HERE = Path(__file__).resolve().parent
CHAINING = ("compositional", "inference", "bridge_comparison")

EMBEDDERS = [
    ("bge-base (SM-R4 Mode A)", EmbedderSpec("local", "BAAI/bge-base-en-v1.5", 768)),
    ("3-large@1024 (SM-R4 Mode B)", EmbedderSpec("azure", "text-embedding-3-large", 1024)),
    ("3-small@1536 (original placeholder)", EmbedderSpec("azure", "text-embedding-3-small", 1536)),
]
ABSOLUTE = [0.5, 0.65]
PERCENTILES = [99.0, 99.5, 99.9]


def _score(r: HippoRetriever, es, k: int, q_ent: Dict[str, List[str]], qvec: Dict[str, np.ndarray]) -> Dict:
    rec, ah = [], []
    for it in es.items:
        ents = q_ent[it.qid] or [it.question]
        seed = r._seed_from_entities(ents, np.vstack([qvec[e] for e in ents]))
        if not seed:
            seed = r._seed_from_entities([it.question], np.vstack([qvec[it.question]]))
        titles = [x.title for x in r._finalize_ranks(seed, k)]
        rec.append(metrics.recall_at_k(titles, it.gold_titles, k))
        ah.append(metrics.all_hops_hit(titles, it.gold_titles, k))
    return {"recall@k": round(float(np.mean(rec)), 4), "all_hops_hit": round(float(np.mean(ah)), 4)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval", type=Path, default=HERE / "data" / "eval_set.json")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--tag", type=str, default="")
    args = ap.parse_args()

    load_env()
    es = load_eval_set(args.eval)
    llm, usage = LLMClient(), Usage()

    # Triples and query NER are embedder-independent: extract once (cache replay).
    triples = {p.pid: extract_triples(_passage_repr(p), llm, usage) for p in es.passages}
    q_ent = {it.qid: extract_query_entities(it.question, llm, usage) for it in es.items}
    print(f"OpenIE replayed; LLM spend this run: ${usage.to_dict()['usd_cost']:.4f}")

    report: Dict = {"k": args.k, "embedders": []}
    for label, spec in EMBEDDERS:
        try:
            emb = CachedEmbedder(model_tag=spec.tag)
            emb._embedder = None
            from harness.embeddings import build_embedder

            emb._embedder = build_embedder(spec)
        except Exception as exc:  # noqa: BLE001
            print(f"\n{label}: SKIPPED ({type(exc).__name__}: {exc})")
            report["embedders"].append({"label": label, "spec": spec.tag, "skipped": str(exc)})
            continue

        kg = KnowledgeGraph()
        for p in es.passages:
            kg.add_passage(p.pid, triples[p.pid])
        keys = kg.entity_keys()
        mat = emb.embed([kg.entity_display[x] for x in keys])
        kg.finalize({x: mat[i] for i, x in enumerate(keys)}, synonym_tau=0.85)

        # One batched embed for every query string this embedder will ever need.
        q_strings = sorted({e for v in q_ent.values() for e in v} | {it.question for it in es.items})
        qmat = emb.embed(q_strings)
        qvec = {t: qmat[i] for i, t in enumerate(q_strings)}

        sub = mat if mat.shape[0] <= 2000 else mat[np.random.default_rng(7).choice(mat.shape[0], 2000, replace=False)]
        sims = sub @ sub.T
        iu = np.triu_indices(sims.shape[0], k=1)
        bg = sims[iu]
        dist = {f"p{q}": round(float(np.percentile(bg, q)), 4) for q in (50, 90, 99, 99.5, 99.9)}
        print(f"\n=== {label} ({spec.tag}) ===")
        print(f"  background entity-entity cosine: {json.dumps(dist)}")

        entry = {"label": label, "spec": spec.tag, "background": dist, "absolute": {}, "percentile": {}}
        for t in ABSOLUTE:
            r = HippoRetriever(llm=llm, embedder=emb, link_threshold=t)
            r.kg, r._entity_keys, r._entity_matrix = kg, keys, mat
            r._pid_to_title = {p.pid: p.title for p in es.passages}
            entry["absolute"][str(t)] = _score(r, es, args.k, q_ent, qvec)
            print(f"  absolute  {t:<5} -> {json.dumps(entry['absolute'][str(t)])}")
        for q in PERCENTILES:
            r = HippoRetriever(llm=llm, embedder=emb, link_percentile=q)
            r.kg, r._entity_keys, r._entity_matrix = kg, keys, mat
            r._pid_to_title = {p.pid: p.title for p in es.passages}
            r._calibrate_threshold()
            s = _score(r, es, args.k, q_ent, qvec)
            s["resolved_cutoff"] = round(r.effective_link_threshold, 4)
            entry["percentile"][f"p{q}"] = s
            print(
                f"  percentile p{q:<5} (cutoff {s['resolved_cutoff']:.4f}) -> recall@k={s['recall@k']} all_hops={s['all_hops_hit']}"
            )
        # Oracle: this embedder's best achievable recall over a dense cutoff grid.
        grid = [round(float(np.percentile(bg, q)), 4) for q in (95, 97, 98, 99, 99.3, 99.5, 99.7, 99.9, 99.95)]
        best = 0.0
        for t in sorted(set(grid)):
            rr = HippoRetriever(llm=llm, embedder=emb, link_threshold=t)
            rr.kg, rr._entity_keys, rr._entity_matrix = kg, keys, mat
            rr._pid_to_title = {p.pid: p.title for p in es.passages}
            best = max(best, _score(rr, es, args.k, q_ent, qvec)["recall@k"])
        entry["oracle_best_recall"] = round(best, 4)
        for t in ABSOLUTE:
            entry["absolute"][str(t)]["regret"] = round(best - entry["absolute"][str(t)]["recall@k"], 4)
        for q in PERCENTILES:
            entry["percentile"][f"p{q}"]["regret"] = round(best - entry["percentile"][f"p{q}"]["recall@k"], 4)
        print(f"  oracle best recall@5 for this embedder: {best:.4f}")
        report["embedders"].append(entry)

    # Spread across embedders is the portability metric.
    live = [e for e in report["embedders"] if "skipped" not in e]
    if len(live) > 1:
        print("\n=== portability: REGRET vs each embedder's own oracle (lower = more portable) ===")
        summary = {}
        # Regret vs each embedder's own oracle isolates threshold-rule quality from
        # embedder quality; raw spread conflates the two.
        for t in ABSOLUTE:
            reg = [e["absolute"][str(t)]["regret"] for e in live]
            summary[f"absolute_{t}"] = {
                "regret": reg,
                "worst": round(max(reg), 4),
                "mean": round(sum(reg) / len(reg), 4),
            }
            print(f"  absolute  {t:<6} regret={reg} worst={max(reg):.4f}")
        for q in PERCENTILES:
            reg = [e["percentile"][f"p{q}"]["regret"] for e in live]
            summary[f"percentile_p{q}"] = {
                "regret": reg,
                "worst": round(max(reg), 4),
                "mean": round(sum(reg) / len(reg), 4),
            }
            print(f"  percentile p{q:<6} regret={reg} worst={max(reg):.4f}")
        report["portability"] = summary

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = f"_{args.tag}" if args.tag else ""
    out = HERE / "results" / f"threshold_portability_{stamp}{tag}.json"
    out.write_text(json.dumps(report, indent=2))
    print("\nWrote", out)


if __name__ == "__main__":
    main()
