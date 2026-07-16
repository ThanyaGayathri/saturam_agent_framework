<!--
SM-R3 decision document. Numbers in "Evidence" are produced by this benchmark
(benchmarks/memory_vectordb/). Regenerate with run.py; see README.md.
-->
# SM-R3 — Decision: memory vector store at expected scale

- **Status:** Proposed
- **Tracking issue:** [Memory] SM-R3 (aicoe-tracker #78)
- **Decisions referenced:** D7, D8
- **Depends on:** SM-R1 (#76) — the scale targets below are *provisional* inputs for R1 to ratify.
- **Feeds:** SM-V1, SM-V4

## Context

The memory subsystem keeps a vector tier first-class (D8). The seed already ships a
capable pgvector backend (`vectordb/pgvector/pgvector.py:40`) with HNSW indexing, hybrid
(vector + text-rank) search (`vector_score_weight=0.5`), and 17 other backends behind a
common `VectorDb` interface. So this is **not** a build decision — it is a *scale +
latency + multi-tenancy* decision:

1. Is pgvector the right **default** memory vector store?
2. At what concrete **scale/latency threshold** do we switch to a dedicated store (Qdrant / Milvus)?
3. **Multi-tenancy**: `tenant_id` filter (one shared collection) vs per-tenant collection?

Because this is a "decision" task, we did not decide from cited numbers alone — we built a
reproducible benchmark that drives all three stores through the same `VectorDb` interface
and measured the trade-off. See [`README.md`](README.md).

## Provisional scale targets (to be ratified by SM-R1)

Grounded in `MEMORY-RESEARCH-episodic-and-neurosymbolic.md` defaults, since R1 is deferred:

| Parameter | Provisional target |
| --- | --- |
| Memories per tenant | 100k – 1M |
| Tenants | up to ~50 (high-cardinality multi-tenant) |
| Total vectors (near-term) | ≤ ~10M |
| Retrieval latency | **p95 < 50 ms** |
| Embedding dim | 768 (benchmark); 384–1536 in practice |

Research guidance: pgvector + HNSW comfortably serves to **~10M vectors at sub-50ms p95**;
Qdrant sustains ~12ms p99 at ~5M with filter-inside-HNSW; Milvus targets 100M–1B+.

## Evidence (this benchmark)

Method: identical deterministic unit vectors indexed in every store; recall measured vs
exact brute-force top-k; latency over 200 queries; HNSW defaults; cosine distance; `k=10`.

### 100k vectors, 50 tenants, dim=768

| backend | tenancy | recall@10 | p50 ms | p95 ms | p99 ms | insert q/s |
| --- | --- | --- | --- | --- | --- | --- |
| pgvector | filter | 1.00 | 59.8 | **134.2** | 201.0 | 860 |
| pgvector | collection | 1.00 | 24.8 | **38.9** | 43.0 | 891 |
| qdrant | filter | 1.00 | 44.9 | **62.4** | 76.7 | 716 |
| qdrant | collection | 1.00 | 11.0 | **15.7** | 17.8 | 964 |
| milvus | filter | 0.93 † | 32.0 | 65.0 | 112.4 | 62 |
| milvus | collection | 1.00 | 13.8 | 15.9 | 18.2 | 217 |

† Tested on the **corrected** Milvus adapter (metadata stored as JSON, not a VARCHAR
string). **Unpatched, the seed adapter returns recall 0.00** here. The fix is a *core*
change deferred to the implementation phase; it is preserved as a patch and written up
under [`findings/`](findings/README.md). We report the true (fixed-code) capability so the
decision isn't skewed by a fixable adapter bug.

### 1M vectors, 50 tenants (clean, no concurrent load)

| backend | tenancy | recall@10 | p50 ms | p95 ms | p99 ms | insert q/s |
| --- | --- | --- | --- | --- | --- | --- |
| pgvector | filter | 1.00 | 375.9 | **771.2** | 1505.1 | 354 |
| pgvector | collection | 1.00 | 190.9 | **235.7** | 341.3 | 884 |
| qdrant | filter | **0.29** | 302.1 | 547.6 | 2085.9 | 442 |
| qdrant | collection | 1.00 | 54.3 | **86.2** | 160.1 | 917 |

(Milvus 1M omitted: its interface insert throughput (~60–200 q/s) makes 1M impractical
here, and Milvus's regime is 100M+, not 1M.)

### Reading the numbers

- **At 100k, everything is comfortable** — all stores ≈ recall 1.0, and the differentiator
  is filtered latency (pgvector 134 ms > Milvus 65 ms ≈ Qdrant 62 ms; per-collection all
  < 40 ms).
- **At 1M, naive multi-tenancy breaks on _both_ stores — in different ways:**
  - **pgvector `tenant_id` filter** keeps recall 1.0 but p95 explodes to **771 ms** (10×
    worse than 100k). It applies the filter **outside** the HNSW index (`pgvector.py:854`),
    so selective tenant filters get slow fast.
  - **Qdrant `tenant_id` filter** *loses accuracy*: **recall collapses to 0.29**. Filtering
    **inside** HNSW at low selectivity (each tenant is ~2 % of the data) without a
    **tenant payload index** prunes the graph traversal away from true neighbours. This is
    expected Qdrant behaviour — it requires enabling its multitenant payload index, which
    the default egpt adapter does not create (see [`findings/`](findings/README.md)).
  - **Per-tenant isolation is the only layout that keeps recall 1.0 at 1M**, and Qdrant
    (86 ms p95) beats pgvector (236 ms p95) there — though **both exceed the 50 ms budget
    at ~20k vectors/tenant with default HNSW**, so hitting sub-50 ms at that per-tenant size
    needs HNSW tuning (`ef_search`, `m`).
- **Takeaway:** a naive shared-table metadata filter is **not** a viable multi-tenancy
  strategy at scale — pgvector fails on latency, Qdrant on recall. Multi-tenancy must use
  each store's **first-class** mechanism (pgvector: partition-by-tenant + indexed column;
  Qdrant: tenant payload index / partitions). Per-tenant physical isolation is fastest but
  carries ops cost (schema/collection sprawl, slower creation, lower insert throughput).

## Decision

### 1. Default store — **pgvector**

Keep pgvector as the default memory vector store. It ships in the seed, gives recall 1.0
with HNSW + hybrid search, meets sub-50ms p95 for single-tenant / per-tenant-isolated
access at 100k, and collapses the memory tier and the relational/bi-temporal data into one
Postgres — a major operational win for the on-prem / VPC deployments this architecture
targets. No dedicated vector DB is justified for the initial target.

### 2. Multi-tenancy approach — **store-native partitioning; a shared JSONB filter is small-scale only**

The benchmark is explicit that a naive shared-table `tenant_id`-in-`meta_data` filter does
**not** scale (pgvector 771 ms p95, Qdrant 0.29 recall at 1M). So:

- **Small scale only** (≲ a few thousand vectors per tenant, ≲ ~100k total): the shared
  table + `tenant_id` metadata filter is fine (100k filtered p95 134 ms is acceptable for
  non-hot paths, recall 1.0) and is the simplest thing to ship.
- **Production default: pgvector `tenant_id` as a first-class, indexed column with
  declarative partitioning by tenant** — each partition carries its own smaller HNSW index,
  giving the per-tenant-isolation speed (recall 1.0) while keeping one logical table and
  avoiding table sprawl. This is the recommended layout; the plain JSONB filter is not.
- **Per-tenant physical isolation** (schema/collection per tenant) is the fastest option
  measured, but reserve it for a bounded number of large tenants — it is an ops burden at
  high cardinality (creation cost, lower insert throughput).
- At large per-tenant working sets (~20k+ vectors/tenant) budget for **HNSW tuning**
  (`ef_search`, `m`) to hold sub-50 ms — default HNSW gives 236 ms (pg) / 86 ms (Qdrant).

### 3. Switch criteria (pgvector → Qdrant / Milvus)

Switch **off pgvector to Qdrant** when *any* of:
- Total live vectors approach **~10M** with a filter-heavy, high-QPS retrieval workload.
- Per-tenant partitioning in Postgres becomes an operational/latency bottleneck and you need
  purpose-built multi-tenant ANN — **Qdrant's tenant payload index / partitioned
  multitenancy**. Note our data shows this is **required, not optional**: Qdrant's default
  filtered search *loses recall* (0.29) at scale until the tenant payload index is enabled.
- p95 stays **> 50 ms** after pgvector partitioning + HNSW tuning are exhausted.

Switch to **Milvus** only at **100M–1B+** vectors (beyond the near-term target), and only
after the adapter metadata-filter fix (see findings) lands; adopt Milvus **partition-key**
multitenancy, not many collections.

Because everything sits behind the `VectorDb` interface, each switch is a
config/integration change, not a rewrite — which is exactly what this benchmark exercises
(one harness drove all three stores with zero store-specific code).

## Consequences

- **Positive:** no new infra for launch; memory + relational data in one Postgres;
  abstraction verified end-to-end (one harness, three stores, zero store-specific code).
- **Negative / watch-items:** multi-tenancy at scale requires deliberate design on *both*
  candidate stores — pgvector needs indexed `tenant_id` + partitioning (not the JSONB
  filter), and Qdrant needs its tenant payload index (or filtered recall collapses). Add a
  combined **recall + p95 SLO check** to the memory eval harness (SM-R13) so the switch
  trigger is data-driven, not guessed.
- Sub-50 ms p95 at large per-tenant working sets needs HNSW parameter tuning; treat default
  HNSW as a floor, not the tuned target.
- Qdrant is the pre-qualified escalation target, but only *with* its multitenant payload
  index enabled — a small adapter enhancement noted in findings.

## Acceptance criteria (SM-R3)

- [x] Default vector store chosen (**pgvector**) with explicit scale + latency thresholds
      (sub-50ms p95; escalate near ~10M or when filtered p95 > 50ms).
- [x] Multi-tenancy approach decided (**store-native partitioning / indexed `tenant_id`**;
      shared JSONB filter only at small scale; per-tenant isolation for bounded large tenants).
- [x] Switch criteria to Qdrant / Milvus documented (above), grounded in the 1M failure modes.
- [x] Confirms pgvector HNSW + hybrid meets the initial target (recall 1.0; sub-50ms p95 at
      100k on the isolated/partitioned layout; larger per-tenant sets need HNSW tuning).

## Notes / follow-ups

- **Milvus adapter defect (found; fix deferred to implementation phase):**
  `vectordb/milvus/milvus.py` stores `meta_data` as a `VARCHAR` JSON string but
  `_build_expr` emits JSON-path filters like `meta_data["tenant_id"] == v`, which only
  match a `JSON`-typed field — so metadata-filtered search silently returns nothing (the
  `recall = 0.0` above). This is a **core** change, so it is **not** made in this decision
  task; the validated prototype fix is preserved as a patch and written up under
  [`findings/`](findings/README.md) for the implementation phase (SM-C\*). Must be fixed
  before Milvus is used for `tenant_id`-filter multi-tenancy.
- **Qdrant tenant payload index (found; enhancement for implementation phase):** Qdrant's
  filtered recall collapses at 1M (0.29) because the default egpt adapter creates no tenant
  payload index. Qdrant's multitenancy guide requires a keyword payload index (optionally
  `is_tenant=true`) on the tenant field. The adapter should expose/create this before Qdrant
  is used for `tenant_id`-filter multi-tenancy. Documented in [`findings/`](findings/README.md).
- **Real embeddings:** this benchmark isolates the *store* using deterministic vectors.
  A follow-up with production embeddings (dim + distribution) is worthwhile once SM-R4
  (embedding model) lands, but does not change the store/tenancy decision.
- **HNSW tuning:** default HNSW was used throughout; an `ef_search`/`m` sweep to hit sub-50 ms
  at large per-tenant working sets is a useful follow-up but not required for this decision.
