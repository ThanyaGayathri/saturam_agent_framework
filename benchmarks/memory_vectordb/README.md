# SM-R3 — Memory vector-store benchmark

Reproducible benchmark that drives **pgvector**, **Qdrant**, and **Milvus** through the
same `egpt` `VectorDb` interface and measures insert throughput, search latency, and
recall at a fixed scale — the evidence behind [`DECISION.md`](DECISION.md).

This exists because SM-R3 is a *decision* task, and the team rule for decision tasks is
"implement all options, measure the trade-off, then decide" — not decide from cited
numbers alone. The vector-store abstraction already ships (18 backends behind the
`VectorDb` ABC), so this is an integration benchmark, not a rewrite.

## What it measures

For each `(backend, scale, tenancy)` case:

| Metric | Meaning |
| --- | --- |
| `recall@k` | overlap of the store's ANN top-k with exact brute-force top-k |
| `p50/p95/p99 ms` | search latency percentiles over the query set |
| `insert q/s` | bulk-insert throughput through the `VectorDb` interface |

**Multi-tenancy modes** (the core SM-R3 question):
- `filter` — one collection, `tenant_id` in metadata, filtered search.
- `collection` — one collection per tenant, unfiltered search within it.

## Design notes

- **Deterministic embedder** ([`embedder.py`](embedder.py)) maps text → a hash-seeded unit
  vector. It is *not* semantic — the goal is that every backend indexes the identical
  vectors, with no embedding API cost or network jitter, so we compare *stores*, not models.
- Because vectors are a pure function of content, **exact top-k ground truth**
  ([`dataset.py`](dataset.py)) is computed by chunked brute force, and recall is measured
  against it.
- Everything goes through the `VectorDb` interface ([`backends.py`](backends.py)) — the
  harness never touches a store-specific API. Switching stores is one factory line.

## Running it

Start the three stores (Docker):

```bash
# pgvector on :5532, qdrant on :6433 (alt ports to avoid clashes), milvus on :19530
bash cookbook/scripts/run_pgvector.sh
docker run -d --name smr3-qdrant -p 6433:6333 -p 6434:6334 qdrant/qdrant
docker compose -f benchmarks/memory_vectordb/docker/milvus-compose.yml up -d
export QDRANT_URL=http://localhost:6433
```

Run the sweep (any venv with `egpt`, `numpy`, `pgvector`, `qdrant-client`, `pymilvus`):

```bash
python -m benchmarks.memory_vectordb.run \
  --backends pgvector qdrant milvus \
  --scales 100000 1000000 \
  --tenancy filter collection \
  --tenants 50 --queries 200 --k 10 --dim 768
```

Results (raw JSON + markdown summary) land in [`results/`](results/). Endpoints are
overridable via `PGV_URL`, `QDRANT_URL`, `MILVUS_URI`.

## Files

| File | Role |
| --- | --- |
| `embedder.py` | deterministic offline embedder |
| `dataset.py` | synthetic memory corpus + exact-NN ground truth |
| `backends.py` | factory building each store behind the `VectorDb` ABC |
| `harness.py` | one benchmark case → metrics |
| `run.py` | CLI sweep over backends × scales × tenancy |
| `docker/milvus-compose.yml` | Milvus standalone (etcd + minio + milvus) |
| `DECISION.md` | the SM-R3 decision document |
| `TEST_LOG.md` | run log |
