# SM-R3 — Memory vector-store benchmark (v2)

Reproducible benchmark that drives **pgvector** and **Qdrant** (and, when stable, **Milvus**)
through the same `egpt` `VectorDb` interface and measures **recall + latency** for memory
retrieval at **per-tenant** scale — the evidence behind [`DECISION.md`](DECISION.md).

v2 was rebuilt after the PR #7 review. Key differences from v1:

- **Real embeddings** — a public ANN dataset (GloVe-100-angular), not i.i.d. Gaussian, so
  recall is discriminative instead of pinned to 1.0.
- **Real HNSW** — the harness explicitly builds the ANN index (the egpt adapter's `create()`
  builds only the table and its `optimize()` is broken — see [`findings/`](findings/README.md)),
  with a matched `ef_search`.
- **Per-tenant scale** — sized as `per_tenant × tenants`, not total.
- **Three tenancy modes** — `filter` (shared table + `tenant_id`), `collection` (per tenant),
  `partition` (pgvector declarative partitioning by tenant).
- **Warmup + 1000 queries × 3 repeats**, cross-tenant **leakage assertion**, explicit `--seed`.

## What it measures

| Metric | Meaning |
| --- | --- |
| `recall@k` | overlap of the store's top-k with exact cosine top-k *within the query's tenant* |
| `p95_ms` / `p95_per_repeat_ms` | search latency p95 (pooled) + per-repeat spread |
| `cross_tenant_leaks` | returned rows whose tenant ≠ the query tenant (must be 0) |
| `insert_qps` | bulk insert throughput (includes index build) |

## Setup

1. **Download the dataset** (~460 MB) and point the harness at it:
   ```bash
   mkdir -p /path/to/data
   curl -L -o /path/to/data/glove-100-angular.hdf5 http://ann-benchmarks.com/glove-100-angular.hdf5
   export MEMBENCH_ANN_HDF5=/path/to/data/glove-100-angular.hdf5
   ```
2. **Start the stores** (alt Qdrant port avoids clashes):
   ```bash
   bash cookbook/scripts/run_pgvector.sh                       # :5532
   docker run -d --name smr3-qdrant -p 6433:6333 qdrant/qdrant # :6433
   docker compose -f benchmarks/memory_vectordb/docker/milvus-compose.yml up -d  # :19530
   export QDRANT_URL=http://localhost:6433
   ```
3. **Deps:** a venv with `egpt`, `numpy`, `h5py`, `pgvector`, `psycopg[binary]`,
   `qdrant-client`, `pymilvus`.

## Running

```bash
python -m benchmarks.memory_vectordb.run \
  --backends pgvector qdrant \
  --per-tenant 100000 --tenants 10 \
  --tenancy filter collection partition \
  --queries 1000 --k 10 --ef 64 --repeats 3 --seed 7
```

Results (raw JSON + markdown) land in [`results/`](results/). Endpoints override via
`PGV_URL`, `QDRANT_URL`, `MILVUS_URI`.

## Files

| File | Role |
| --- | --- |
| `realdata.py` | dataset loader + `DatasetEmbedder` (real vectors via the interface) + ground truth |
| `backends.py` | factory building each store behind the `VectorDb` ABC (matched `ef_search`) |
| `partition_backend.py` | pgvector declarative-partitioning tenancy (raw SQL, benchmark-only) |
| `harness.py` | one case → metrics (warmup, repeats, leakage, HNSW build) |
| `run.py` | CLI sweep over backends × tenancy at per-tenant scale |
| `DECISION.md` | the SM-R3 decision |
| `findings/` | core defects found + preserved patches (deferred to implementation) |
| `TEST_LOG.md` | run log |

## Known limitation

Runs are on a single laptop Docker host; absolute latencies are indicative, and Milvus was
unstable here (see DECISION.md). The **relative** recall/latency/tenancy comparison — which
drives the decision — is robust and reproducible from the committed results.
