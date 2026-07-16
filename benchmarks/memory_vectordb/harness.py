"""Measurement harness for the SM-R3 vector-store benchmark.

Drives a backend purely through the VectorDb interface and records:
- insert throughput (records/sec) for the bulk write
- search latency p50/p95/p99 (ms) over a fixed query set
- recall@k against exact brute-force ground truth

Two multi-tenancy modes are exercised, matching the SM-R3 question
"tenant_id filter vs per-tenant collection":
- filter     : one collection, tenant_id in metadata, filtered search
- collection : one collection per tenant, unfiltered search within it
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import numpy as np

from egpt.knowledge.document import Document
from egpt.vectordb.search import SearchType

from .backends import get_builder
from .dataset import Record, exact_topk, generate_queries, generate_records
from .embedder import DeterministicEmbedder

_INSERT_BATCH = 2_000


def _doc(r: Record) -> Document:
    return Document(
        content=r.content,
        id=r.id,
        meta_data={
            "rid": r.id,
            "tenant_id": r.tenant_id,
            "type": r.mem_type,
            "importance": r.importance,
        },
    )


def _returned_id(d: Document) -> Optional[str]:
    if d.meta_data and "rid" in d.meta_data:
        return str(d.meta_data["rid"])
    return d.content  # fallback: content is unique in this corpus


def _percentiles(latencies_ms: List[float]) -> Dict[str, float]:
    arr = np.array(latencies_ms, dtype=np.float64)
    return {
        "p50_ms": round(float(np.percentile(arr, 50)), 3),
        "p95_ms": round(float(np.percentile(arr, 95)), 3),
        "p99_ms": round(float(np.percentile(arr, 99)), 3),
        "mean_ms": round(float(arr.mean()), 3),
    }


def _insert_all(store, records: List[Record]) -> float:
    """Insert records in batches; return wall-clock seconds."""
    start = time.perf_counter()
    for i in range(0, len(records), _INSERT_BATCH):
        batch = [_doc(r) for r in records[i : i + _INSERT_BATCH]]
        store.insert(content_hash="bench", documents=batch)
    return time.perf_counter() - start


def run_case(
    backend: str,
    n: int,
    num_tenants: int,
    tenancy: str,
    k: int = 10,
    num_queries: int = 200,
    dim: int = 768,
    seed: int = 7,
) -> Dict:
    """Run one benchmark case and return a metrics dict."""
    if tenancy not in ("filter", "collection"):
        raise ValueError("tenancy must be 'filter' or 'collection'")

    embedder = DeterministicEmbedder(dimensions=dim)
    builder = get_builder(backend, embedder, SearchType.vector)

    records = generate_records(n, num_tenants, seed=seed)
    queries = generate_queries(num_queries, num_tenants, seed=seed + 1)
    truth = exact_topk(records, queries, embedder, k=k, per_tenant=True)

    base = f"bench_{backend}_{tenancy}_{n}"
    stores: Dict[str, object] = {}
    insert_seconds = 0.0

    try:
        if tenancy == "filter":
            store = builder(base)
            store.drop() if store.exists() else None
            store.create()
            insert_seconds = _insert_all(store, records)
            stores["_single"] = store
        else:  # per-tenant collection
            by_tenant: Dict[int, List[Record]] = {}
            for r in records:
                by_tenant.setdefault(r.tenant_id, []).append(r)
            for t, recs in by_tenant.items():
                s = builder(f"{base}_t{t}")
                s.drop() if s.exists() else None
                s.create()
                insert_seconds += _insert_all(s, recs)
                stores[t] = s

        # --- query phase ---
        latencies: List[float] = []
        hits = 0
        total = 0
        for qi, q in enumerate(queries):
            t = q["tenant_id"]
            if tenancy == "filter":
                store = stores["_single"]
                filt = {"tenant_id": t}
            else:
                store = stores.get(t)
                filt = None
                if store is None:
                    continue

            start = time.perf_counter()
            results = store.search(query=q["text"], limit=k, filters=filt)
            latencies.append((time.perf_counter() - start) * 1000.0)

            got = {_returned_id(d) for d in results}
            gold = set(truth[qi])
            hits += len(got & gold)
            total += len(gold)

        recall = round(hits / total, 4) if total else 0.0
        insert_qps = round(n / insert_seconds, 1) if insert_seconds > 0 else None

        metrics = {
            "backend": backend,
            "n_records": n,
            "num_tenants": num_tenants,
            "tenancy": tenancy,
            "dim": dim,
            "k": k,
            "num_queries": len(latencies),
            "insert_seconds": round(insert_seconds, 2),
            "insert_qps": insert_qps,
            "recall_at_k": recall,
            **_percentiles(latencies),
        }
        return metrics
    finally:
        for s in stores.values():
            try:
                s.drop()
            except Exception:
                pass
