"""``ef_search`` sweep for the pgvector memory substrate (SM-V1 #95, ADR 0003 section A.3b).

The ADR ratifies ``Recall@10 >= 0.95`` **conditionally**: SM-R3 measured 0.881 at 100k/tenant with
``ef_search = 64`` and says only that a higher ``ef`` *narrows* the gap "at some latency cost" --
there is no sweep in its ``results/``. So the ruling is that SM-V1 runs one at the deployed
per-tenant size and reports recall **and** p95 together, and that failing to reach 0.95 inside the
50 ms budget makes 0.95 the escalation trigger to Qdrant rather than a floor to quietly lower.

This is that sweep, and it differs from ``run.py`` in one way that matters: it drives the **memory
substrate** (``PgVectorMemorySubstrate``) rather than the ``VectorDb`` adapter, so what is measured
is the layout this ticket ships -- one partition and one HNSW index per tenant, the scope predicate
on every read -- and not a benchmark harness's own table.

Vectors are real (GloVe-100-angular, the same source SM-R3 v2 used) because recall on i.i.d.
Gaussian vectors is pinned at 1.0 and measures nothing. Queries are in-tenant record vectors with
small noise, and ground truth is exact cosine top-k **within the query's tenant**, computed in
numpy -- so a recall shortfall is the index's, not the predicate's.

    python -m benchmarks.memory_vectordb.ef_sweep --per-tenant 100000 --tenants 3

Requires a running Postgres with pgvector (``cookbook/scripts/run_pgvector.sh``) and the dataset at
``MEMBENCH_ANN_HDF5``.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from typing import Any, Dict, List

import numpy as np

from egpt.integrations.memory.vector.pgvector import PgVectorMemorySubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryQuery, MemoryRecord, Scope

TABLE = "bench_memory_vectors"


class VectorEmbedder:
    """Returns a precomputed real vector, selected by an id encoded in the text.

    The substrate embeds whatever it is given, so the only way to measure *its* recall on real
    vectors without changing its interface is to hand it an embedder that resolves ``R:<i>`` and
    ``Q:<i>`` to rows of the dataset. Same trick as ``realdata.DatasetEmbedder``.
    """

    def __init__(self, records: np.ndarray, queries: np.ndarray) -> None:
        self.records, self.queries = records, queries
        self.dimensions = int(records.shape[1])

    def get_embedding(self, text: str):
        kind, index = text.split(":", 1)
        source = self.records if kind == "R" else self.queries
        return [float(value) for value in source[int(index)]]

    async def async_get_embedding(self, text: str):
        return self.get_embedding(text)


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


def load(path: str, needed: int) -> np.ndarray:
    import h5py

    with h5py.File(path, "r") as handle:
        return _normalize(np.asarray(handle["train"][:needed]))


def ground_truth(tenant_vectors: np.ndarray, query: np.ndarray, k: int) -> set:
    """Exact cosine top-k inside one tenant. Unit vectors, so a dot product ranks them."""
    scores = tenant_vectors @ query
    return set(np.argsort(-scores)[:k].tolist())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-tenant", type=int, default=20_000)
    parser.add_argument("--tenants", type=int, default=3)
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument(
        "--ef", type=int, nargs="+", default=[16, 32, 64, 128, 256, 512]
    )
    parser.add_argument("--noise", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--db-url",
        default=os.getenv(
            "EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5533/ai"
        ),
    )
    parser.add_argument("--dataset", default=os.getenv("MEMBENCH_ANN_HDF5", ""))
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    total = args.per_tenant * args.tenants
    vectors = load(args.dataset, total)
    rng = np.random.default_rng(args.seed)

    # Queries are in-tenant records with small noise: a realistic "find similar memories" probe,
    # and one whose true neighbours sit in a dense region rather than being trivially separable.
    query_sources = rng.choice(args.per_tenant, size=args.queries, replace=False)
    query_vectors = _normalize(
        vectors[query_sources]
        + rng.normal(0.0, args.noise, size=(args.queries, vectors.shape[1])).astype(
            np.float32
        )
    )

    embedder = VectorEmbedder(vectors, query_vectors)
    substrate = PgVectorMemorySubstrate(
        db_url=args.db_url, embedder=embedder, table=TABLE, auto_init_schema=False
    )
    from sqlalchemy import text as sql_text

    with substrate._engine.begin() as connection:
        connection.execute(sql_text(f"DROP TABLE IF EXISTS ai.{TABLE} CASCADE"))
    substrate.init_schema()

    scopes = [
        Scope(tenant_id=f"t{index}", user_id="ada") for index in range(args.tenants)
    ]
    print(f"loading {total} vectors into {args.tenants} tenant(s) of {args.per_tenant}")
    started = time.time()
    for tenant, scope in enumerate(scopes):
        base = tenant * args.per_tenant
        for chunk in range(0, args.per_tenant, 2_000):
            rows = [
                MemoryRecord(
                    id=f"r{base + offset}",
                    tier=MemoryTier.EPISODIC,
                    content=f"R:{base + offset}",
                    scope=scope,
                    created_at=base + offset,
                )
                for offset in range(chunk, min(chunk + 2_000, args.per_tenant))
            ]
            substrate.upsert(scope, rows)
        print(f"  tenant {tenant} loaded ({time.time() - started:.0f}s)")

    # Ground truth for tenant 0 only: every query is drawn from it, and the point of the sweep is
    # per-tenant recall. The other tenants exist so the index a query traverses is a partition's,
    # not the whole table's.
    tenant_zero = vectors[: args.per_tenant]
    truth = [
        ground_truth(tenant_zero, query_vectors[index], args.k)
        for index in range(args.queries)
    ]

    results: List[Dict[str, Any]] = []
    for ef in args.ef:
        substrate.ef_search = ef
        latencies, recalls = [], []
        for index in range(args.queries):
            query = MemoryQuery(
                text=f"Q:{index}", limit=args.k, tiers=[MemoryTier.EPISODIC]
            )
            clock = time.perf_counter()
            hits = substrate.search(scopes[0], query)
            latencies.append((time.perf_counter() - clock) * 1000.0)
            got = {int(hit.id[1:]) for hit in hits}
            recalls.append(len(got & truth[index]) / float(args.k))
        row = {
            "ef_search": ef,
            "recall@10": round(statistics.fmean(recalls), 4),
            "p50_ms": round(statistics.median(latencies), 2),
            "p95_ms": round(sorted(latencies)[int(0.95 * len(latencies)) - 1], 2),
        }
        results.append(row)
        print(
            f"  ef={ef:>4}  recall@{args.k}={row['recall@10']:.3f}  p50={row['p50_ms']}ms  p95={row['p95_ms']}ms"
        )

    substrate.close()
    payload = {
        "per_tenant": args.per_tenant,
        "tenants": args.tenants,
        "queries": args.queries,
        "k": args.k,
        "dim": int(vectors.shape[1]),
        "results": results,
    }
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(payload, handle, indent=2)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
