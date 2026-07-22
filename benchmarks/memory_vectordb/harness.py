"""Measurement harness for the SM-R3 vector-store benchmark (v2, review-hardened).

Changes vs v1 (addresses PR review):
- Real embeddings from a public ANN dataset (realdata.py) instead of i.i.d. Gaussian
  (recall is now discriminative, not pinned to 1.0).                        [review #3]
- Matched, non-degenerate HNSW ef_search on pgvector; recall reported next to
  latency so cross-store comparison is read at achieved recall.             [review #1]
- Scale is expressed **per tenant** (per_tenant x num_tenants), not total.  [review #2]
- Third tenancy mode `partition` (pgvector declarative partitioning).       [review #4]
- Warmup queries + >=1000 queries + repeated runs with spread.              [review #6]
- Cross-tenant leakage is asserted (returned rid must be the query tenant). [review #9]
- Explicit query seed (threaded from run.py --seed).                        [review #8]
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import numpy as np

from egpt.knowledge.document import Document
from egpt.vectordb.search import SearchType

from .backends import PGV_URL, get_builder
from .realdata import (
    DatasetEmbedder,
    Record,
    build_queries,
    build_records,
    exact_topk_per_tenant,
)

_INSERT_BATCH = 2_000
_WARMUP = 100


def _doc(r: Record) -> Document:
    return Document(
        content=r.content,
        id=r.id,
        meta_data={"rid": r.id, "tenant_id": r.tenant_id},
    )


def _returned_id(d: Document) -> Optional[str]:
    if d.meta_data and "rid" in d.meta_data:
        return str(d.meta_data["rid"])
    return None


def _pct(latencies: List[float]) -> Dict[str, float]:
    a = np.array(latencies, dtype=np.float64)
    return {
        "p50_ms": round(float(np.percentile(a, 50)), 3),
        "p95_ms": round(float(np.percentile(a, 95)), 3),
        "p99_ms": round(float(np.percentile(a, 99)), 3),
        "mean_ms": round(float(a.mean()), 3),
    }


def _insert_all(store, records: List[Record]) -> float:
    start = time.perf_counter()
    for i in range(0, len(records), _INSERT_BATCH):
        store.insert(
            content_hash="bench",
            documents=[_doc(r) for r in records[i : i + _INSERT_BATCH]],
        )
    # CRITICAL: egpt's create() only builds the table; the ANN index is separate. Without
    # it pgvector does an EXACT SEQUENTIAL SCAN (recall always 1.0, latency = scan cost, not
    # HNSW). The adapter's optimize() is meant to build it but is itself broken (it runs
    # `SET maintenance_work_mem = $1` as a bind param, which Postgres rejects — see
    # findings/). So we build the identical HNSW index via raw SQL here; the store's SEARCH
    # path (which we benchmark) is unchanged. qdrant/milvus auto-index on insert.
    _ensure_pgvector_hnsw(store)
    return time.perf_counter() - start


def _ensure_pgvector_hnsw(store) -> None:
    """Build an HNSW index on an egpt PgVector table (the adapter's optimize() is broken)."""
    if store.__class__.__name__ != "PgVector":
        return
    import psycopg

    fullname = store.table.fullname  # e.g. "ai.bench_pgvector_filter_..."
    idx = f"{store.table_name}_hnsw_bench"
    dsn = PGV_URL.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as c:
        # Serial build: parallel maintenance workers put maintenance_work_mem into DSM
        # (/dev/shm), which is tiny in the docker image and fails to resize.
        c.execute("SET max_parallel_maintenance_workers = 0")
        c.execute("SET maintenance_work_mem = '512MB'")
        c.execute(
            f"CREATE INDEX IF NOT EXISTS {idx} ON {fullname} "
            f"USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 200)"
        )


def run_case(
    backend: str,
    per_tenant: int,
    num_tenants: int,
    tenancy: str,
    train: np.ndarray,
    k: int = 10,
    num_queries: int = 1000,
    ef_search: int = 64,
    seed: int = 7,
    repeats: int = 3,
) -> Dict:
    """Run one benchmark case (real vectors) and return a metrics dict."""
    if tenancy not in ("filter", "collection", "partition"):
        raise ValueError("tenancy must be filter|collection|partition")
    if tenancy == "partition" and backend != "pgvector":
        raise ValueError("partition tenancy is pgvector-only")

    total = per_tenant * num_tenants
    embedder = DatasetEmbedder(train)
    records = build_records(total, num_tenants, train, seed=seed)
    queries = build_queries(num_queries, records, embedder, seed=seed + 1)
    truth = exact_topk_per_tenant(records, queries, embedder, k=k)
    rid_tenant = {r.id: r.tenant_id for r in records}

    base = f"bench_{backend}_{tenancy}_{per_tenant}x{num_tenants}"
    stores: Dict = {}
    part = None
    insert_seconds = 0.0

    def _run_query(q, timed: bool):
        t = q["tenant_id"]
        if tenancy == "partition":
            ids = part.search_ids(embedder._vec(q["text"]), t, k)
        elif tenancy == "filter":
            res = stores["_single"].search(
                query=q["text"], limit=k, filters={"tenant_id": t}
            )
            ids = [_returned_id(d) for d in res]
        else:  # collection
            res = stores[t].search(query=q["text"], limit=k, filters=None)
            ids = [_returned_id(d) for d in res]
        return ids

    try:
        if tenancy == "partition":
            from .partition_backend import PgvectorPartitioned

            dsn = PGV_URL.replace("postgresql+psycopg://", "postgresql://")
            part = PgvectorPartitioned(
                dsn, base, embedder.dimensions, ef_search, list(range(num_tenants))
            )
            if part.exists():
                part.drop()
            part.create()
            insert_seconds = part.insert(records, embedder)
        elif tenancy == "filter":
            builder = get_builder(
                backend, embedder, SearchType.vector, ef_search=ef_search
            )
            s = builder(base)
            s.drop() if s.exists() else None
            s.create()
            insert_seconds = _insert_all(s, records)
            stores["_single"] = s
        else:  # collection
            builder = get_builder(
                backend, embedder, SearchType.vector, ef_search=ef_search
            )
            by_tenant: Dict[int, List[Record]] = {}
            for r in records:
                by_tenant.setdefault(r.tenant_id, []).append(r)
            for t, recs in by_tenant.items():
                s = builder(f"{base}_t{t}")
                s.drop() if s.exists() else None
                s.create()
                insert_seconds += _insert_all(s, recs)
                stores[t] = s

        # warmup (not timed)
        for q in queries[:_WARMUP]:
            _run_query(q, timed=False)

        # timed repeats
        all_lat: List[float] = []
        per_rep_p95: List[float] = []
        hits = tot = leaks = 0
        recall_counted = False
        for _ in range(repeats):
            rep_lat: List[float] = []
            for qi, q in enumerate(queries):
                start = time.perf_counter()
                ids = _run_query(q, timed=True)
                rep_lat.append((time.perf_counter() - start) * 1000.0)
                if not recall_counted:
                    got = {i for i in ids if i}
                    hits += len(got & set(truth[qi]))
                    tot += len(truth[qi])
                    leaks += sum(1 for i in got if rid_tenant.get(i) != q["tenant_id"])
            recall_counted = True
            all_lat.extend(rep_lat)
            per_rep_p95.append(round(float(np.percentile(rep_lat, 95)), 3))

        return {
            "backend": backend,
            "per_tenant": per_tenant,
            "num_tenants": num_tenants,
            "total_vectors": total,
            "tenancy": tenancy,
            "dim": embedder.dimensions,
            "k": k,
            "ef_search": ef_search,
            "num_queries": num_queries,
            "repeats": repeats,
            "insert_seconds": round(insert_seconds, 2),
            "insert_qps": round(total / insert_seconds, 1)
            if insert_seconds > 0
            else None,
            "recall_at_k": round(hits / tot, 4) if tot else 0.0,
            "cross_tenant_leaks": leaks,
            "p95_per_repeat_ms": per_rep_p95,
            **_pct(all_lat),
        }
    finally:
        if part is not None:
            try:
                part.close()
                part.drop()
            except Exception:
                pass
        for s in stores.values():
            try:
                s.drop()
            except Exception:
                pass
