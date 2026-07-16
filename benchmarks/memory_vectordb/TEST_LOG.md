# TEST_LOG — SM-R3 memory vector-store benchmark

### Smoke test (n=1000, 10 tenants, 20 queries)

**Status:** PASS

**Description:** End-to-end validation of the harness across pgvector, Qdrant, Milvus in
both `filter` and `collection` tenancy modes.

**Result:** pgvector/Qdrant recall 1.0 both modes. Surfaced the Milvus metadata-filter
defect (filter mode recall 0.0); see findings/.

---

### Scale run (n=100000, 50 tenants, 200 queries, dim=768)

**Status:** PASS

**Description:** Primary decision-grade run across all three backends, both tenancy modes,
as a single sequential pass (clean absolute latencies).
Results: `results/summary_20260716T091045Z_scale100k.md`.

**Result (p95):** pgvector filter 134 ms / collection 39 ms; Qdrant filter 62 ms /
collection 16 ms; Milvus collection 16 ms. All recall ≈ 1.0. Milvus `filter` was 0.0 on the
seed adapter (defect); re-run on the corrected adapter (see below) gives recall 0.93 /
p95 65 ms.

---

### Milvus filter re-run on corrected adapter (n=100000)

**Status:** PASS

**Description:** Milvus `filter` re-run with the metadata-as-JSON fix applied locally
(uncommitted; preserved as findings/milvus-metadata-json.patch) to measure true capability.
Results: `results/summary_20260716T103948Z_milvus_fixed.md`.

**Result:** recall 0.93, p95 65 ms — confirms the fix makes Milvus filtered multi-tenancy
work (unpatched: 0.0).

---

### Scale run (n=1000000, 50 tenants — clean, sequential, no concurrent load)

**Status:** PASS

**Description:** pgvector and Qdrant at 1M, filter + collection, each backend run alone to
avoid CPU/IO contention (an earlier concurrent run inflated latencies ~2-4×; archived under
results/_contended/ and discarded).
Results: `results/summary_*scale1m_pg_clean.md`, `results/summary_*scale1m_qdrant_clean.md`.

**Result (p95 / recall):**
- pgvector filter: 771 ms / 1.0 — filter-outside-HNSW does not scale (latency).
- pgvector collection: 236 ms / 1.0.
- qdrant filter: 548 ms / **0.29** — filter-inside-HNSW loses recall without a tenant
  payload index (see findings/).
- qdrant collection: 86 ms / 1.0.

Key conclusion: naive shared-table `tenant_id` filtering is not viable at 1M on either store
(pgvector fails on latency, Qdrant on recall). Multi-tenancy must use store-native
partitioning / payload indexing. Drives DECISION.md.

---
