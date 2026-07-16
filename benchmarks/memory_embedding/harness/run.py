"""Run the SM-R4 memory-embedding benchmark.

For each candidate model (exercised through its egpt embedder abstraction) we
measure:

  - retrieval quality : recall@{1,5,10}, nDCG@10, MRR@10 on the chosen eval set
  - latency           : single-query embedding p50/p95 (ms), sync path
  - throughput        : documents/second (sync sequential and async concurrent)
  - sync/async parity : max abs vector difference between the two code paths
  - storage cost      : pgvector footprint at 10M vectors for the model's dim

Quality/latency/throughput are always *measured* — never cited. Static
footprint columns (params, disk, license, offline) come from the registry.

Usage:
    PYTHONPATH=libs/agno python harness/run.py --dataset synthetic --mode A
    PYTHONPATH=libs/agno python harness/run.py --dataset synthetic --models openai-3-small
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Dict, List

import numpy as np

from harness import datasets, embedders, metrics, storage

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1))))
    return s[idx]


def _normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def _rank(
    query_vecs: np.ndarray, doc_vecs: np.ndarray, doc_ids: List[str]
) -> List[List[str]]:
    # cosine == dot product on normalized vectors
    sims = query_vecs @ doc_vecs.T  # (nq, nd)
    order = np.argsort(-sims, axis=1)
    return [[doc_ids[j] for j in row] for row in order]


def evaluate_candidate(
    cand: embedders.Candidate, eset: datasets.EvalSet, latency_reps: int = 3
) -> Dict:
    embedder = cand.build()

    doc_ids = list(eset.corpus.keys())
    doc_texts = [embedders.doc_prefix(cand.key) + eset.corpus[d] for d in doc_ids]
    query_ids = list(eset.queries.keys())
    query_texts = [
        embedders.query_prefix(cand.key) + eset.queries[q] for q in query_ids
    ]

    # --- sync embedding (quality + throughput) ---
    t0 = time.perf_counter()
    doc_vecs_sync = embedders.embed_texts_sync(embedder, doc_texts)
    sync_doc_secs = time.perf_counter() - t0
    query_vecs_sync = embedders.embed_texts_sync(embedder, query_texts)

    doc_mat = _normalize(np.asarray(doc_vecs_sync, dtype=np.float64))
    q_mat = _normalize(np.asarray(query_vecs_sync, dtype=np.float64))
    measured_dim = doc_mat.shape[1]

    rankings_list = _rank(q_mat, doc_mat, doc_ids)
    rankings = {qid: r for qid, r in zip(query_ids, rankings_list)}
    quality = metrics.aggregate(rankings, eset.qrels)

    # --- async embedding (parity + async throughput) ---
    t0 = time.perf_counter()
    doc_vecs_async = asyncio.run(embedders.embed_texts_async(embedder, doc_texts))
    async_doc_secs = time.perf_counter() - t0
    async_mat = _normalize(np.asarray(doc_vecs_async, dtype=np.float64))
    parity = (
        float(np.max(np.abs(doc_mat - async_mat)))
        if async_mat.shape == doc_mat.shape
        else -1.0
    )

    # --- single-query latency (sync path) ---
    latencies_ms: List[float] = []
    for _ in range(latency_reps):
        for t in query_texts:
            s = time.perf_counter()
            embedder.get_embedding(t)
            latencies_ms.append((time.perf_counter() - s) * 1000.0)

    n_docs = len(doc_ids)
    st = storage.estimate(measured_dim)

    return {
        "key": cand.key,
        "mode": cand.mode,
        "egpt_class": cand.egpt_class,
        "model_id": cand.model_id,
        "declared_dim": cand.dim,
        "measured_dim": measured_dim,
        "license": cand.license,
        "offline": cand.offline,
        "params_m": cand.params_m,
        "disk_mb": cand.disk_mb,
        "quality": {k: round(v, 4) for k, v in quality.items()},
        "latency_ms": {
            "p50": round(median(latencies_ms), 2),
            "p95": round(_percentile(latencies_ms, 95), 2),
            "n": len(latencies_ms),
        },
        "throughput_docs_per_s": {
            "sync": round(n_docs / sync_doc_secs, 1) if sync_doc_secs > 0 else None,
            "async": round(n_docs / async_doc_secs, 1) if async_doc_secs > 0 else None,
        },
        "sync_async_max_abs_diff": round(parity, 8),
        "storage_10m": {
            "storage_type": st.storage_type,
            "raw_gb": st.raw_gb,
            "index_gb": st.index_gb,
            "total_gb": st.total_gb,
            "fits_vector": st.fits_vector,
            "fits_halfvec": st.fits_halfvec,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="synthetic", help="synthetic | beir:scifact")
    ap.add_argument("--mode", default=None, help="filter candidates by mode: A or B")
    ap.add_argument("--models", nargs="*", default=None, help="explicit candidate keys")
    ap.add_argument("--latency-reps", type=int, default=3)
    ap.add_argument("--beir-max-queries", type=int, default=100)
    ap.add_argument("--beir-max-distractors", type=int, default=2000)
    ap.add_argument("--out", default=None, help="output json filename (under results/)")
    args = ap.parse_args()

    if args.dataset.startswith("beir:"):
        eset = datasets.load(
            args.dataset,
            max_queries=args.beir_max_queries,
            max_distractors=args.beir_max_distractors,
        )
    else:
        eset = datasets.load(args.dataset)
    print(f"dataset={eset.name} corpus={len(eset.corpus)} queries={len(eset.queries)}")

    if args.models:
        cands = [embedders.get_candidate(k) for k in args.models]
    else:
        cands = embedders.list_candidates(mode=args.mode)

    results = []
    for cand in cands:
        print(f"--- {cand.key} ({cand.egpt_class} / {cand.model_id}) ---")
        try:
            res = evaluate_candidate(cand, eset, latency_reps=args.latency_reps)
            results.append(res)
            q = res["quality"]
            print(
                f"    recall@5={q['recall@5']} ndcg@10={q['ndcg@10']} "
                f"p50={res['latency_ms']['p50']}ms dim={res['measured_dim']} "
                f"parity={res['sync_async_max_abs_diff']}"
            )
        except Exception as e:  # noqa: BLE001 - benchmark should continue past a failing model
            print(f"    FAILED: {type(e).__name__}: {e}")
            results.append({"key": cand.key, "error": f"{type(e).__name__}: {e}"})

    RESULTS_DIR.mkdir(exist_ok=True)
    ds_tag = eset.name.replace(":", "_")
    out_name = args.out or f"results_{ds_tag}.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": eset.name,
        "corpus_size": len(eset.corpus),
        "query_count": len(eset.queries),
        "results": results,
    }
    (RESULTS_DIR / out_name).write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {RESULTS_DIR / out_name}")


if __name__ == "__main__":
    main()
