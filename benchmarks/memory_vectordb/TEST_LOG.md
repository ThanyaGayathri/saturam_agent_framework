# TEST_LOG — SM-R3 memory vector-store benchmark (v2)

v2 rebuild after PR #7 review: real embeddings (GloVe-100-angular), real HNSW (matched
ef=64), per-tenant scale, 3 tenancy modes, 1000 queries × 3 repeats, leakage asserted.

### Foundation gate (100k/tenant × 3, real data)

**Status:** PASS. Confirmed recall is now discriminative (not 1.0) and HNSW is actually
used. Surfaced that the egpt PgVector adapter builds no index and its `optimize()` is broken
(findings 3 + 4) — the harness now builds the HNSW index via raw SQL. Cross-tenant leaks = 0.

### Run A — 100k/tenant × 10 (1M total): per-tenant target

**Status:** PASS (pgvector + qdrant). Milvus errored (container down overnight).
Result: `results/summary_20260720T200715Z_matrixA.md`.
- pgvector: filter recall 0.564 / p95 25 ms; collection 0.881 / 20.7 ms; partition 0.881 / 15.2 ms.
- qdrant: filter 0.781 / 766.7 ms; collection 1.000 / 13.4 ms. Leaks = 0.

### Run B — 20k/tenant × 50 (1M total): high cardinality

**Status:** PASS. Result: `results/summary_20260721T021033Z_matrixB.md`.
- pgvector: filter recall **0.217** / 23 ms; collection 0.950 / 16.9 ms; **partition 1.000 / 13.7 ms**.
- qdrant: filter 0.986 / 901.7 ms; collection 1.000 / 14.4 ms. Leaks = 0.
- Headline: shared-table filter recall collapses with tenant count; partitioning is best.

### Run C — 1M/tenant × 1 (pure scale)

**Status:** PARTIAL. pgvector filter recall 0.799 / 21 ms; collection 0.796 / 67.7 ms
(salvaged from log). Partition + qdrant not completed — a serial 1M HNSW build stalled
overnight after the host slept; killed. Non-critical (Runs A/B decide the question).

### Milvus

**Status:** NOT MEASURED at v2 scale. Functionally validated earlier (patched adapter returns
correct tenant rows; unpatched returns nothing). The container repeatedly went
unavailable / gRPC-timed-out under load in this environment. 100M+ escape hatch, so it does
not affect the decision.

### Gates

**Status:** `./scripts/format.sh` + `./scripts/validate.sh` pass; core (`libs/`) untouched
(Milvus fix kept as `findings/milvus-metadata-json.patch`, reverted from the tree).
