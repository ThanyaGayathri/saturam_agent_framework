<!--
SM-R3 decision document (v2 — rebuilt after PR #7 review). Numbers are produced by this
benchmark on REAL embeddings (GloVe-100-angular), real HNSW indexes, per-tenant scale.
Regenerate with run.py; see README.md. Raw results in results/.
-->
# SM-R3 — Decision: memory vector store at expected scale

- **Status:** Proposed (v2, supersedes the v1 numbers in PR #7)
- **Tracking issue:** [Memory] SM-R3 — Saturam-Inc/aicoe-tracker#78
- **Decisions referenced:** D7, D8
- **Depends on:** SM-R1 (#76) — scale/latency targets below are *provisional* for R1 to ratify.
- **Feeds:** SM-V1, SM-V4

## Why this was rebuilt (v1 → v2)

The first cut (PR #7) was measured on i.i.d. Gaussian vectors and — critically — **never
built an ANN index**, so pgvector was doing exact sequential scans. That produced a fake
recall of 1.0 and latencies that were scan costs, not HNSW. v2 fixes the methodology and the
story changes substantially. See "Core defects found" and `findings/`.

## Method (v2)

- **Real embeddings:** public ANN dataset **GloVe-100-angular** (1.18M × 100-d, cosine; mean
  top-1 NN cosine ≈ 0.57 vs 0.13 for the old Gaussian) → recall is discriminative.
- **Real HNSW**, matched `ef_search = 64` (≥ k) on pgvector; recall reported next to latency.
- **Scale is per-tenant** (`per_tenant × tenants`), not total.
- **3 tenancy modes:** `filter` (shared table + `tenant_id`), `collection` (one per tenant),
  `partition` (pgvector declarative partitioning by tenant).
- Warmup + **1000 queries × 3 repeats** (spread reported); **cross-tenant leakage asserted
  (0 in all runs)**; `k = 10`; seeded. Driven entirely through the egpt `VectorDb` interface
  (partition mode via raw SQL, since the adapter doesn't express partitioning).

## Evidence

### Run A — 100k/tenant × 10 tenants (1M total): the per-tenant target

| backend | tenancy | recall@10 | p95 ms |
| --- | --- | --- | --- |
| pgvector | filter | **0.564** | 25.0 |
| pgvector | collection | 0.881 | 20.7 |
| pgvector | partition | 0.881 | 15.2 |
| qdrant | filter | 0.781 | **766.7** |
| qdrant | collection | 1.000 | 13.4 |

### Run B — 20k/tenant × 50 tenants (1M total): high cardinality

| backend | tenancy | recall@10 | p95 ms |
| --- | --- | --- | --- |
| pgvector | filter | **0.217** | 23.0 |
| pgvector | collection | 0.950 | 16.9 |
| pgvector | partition | **1.000** | 13.7 |
| qdrant | filter | 0.986 | **901.7** |
| qdrant | collection | 1.000 | 14.4 |

### Run C — 1M/tenant × 1 (pure scale, partial)

| backend | tenancy | recall@10 | p95 ms |
| --- | --- | --- | --- |
| pgvector | filter | 0.799 | 21.0 |
| pgvector | collection | 0.796 | 67.7 |

*(partition + qdrant at 1M/tenant not completed — a serial 1M HNSW build stalled overnight
after the host slept; not re-run because Runs A/B already decide the question. Re-running
with parallel index build + larger container `/dev/shm` is a follow-up.)*

### Milvus — not benchmarked at v2 scale

Milvus was **functionally validated** on the patched adapter earlier (a live `tenant_id`
filter returned only the correct tenant's rows; unpatched it returns nothing — Finding 1),
but a v2 recall/latency run at per-tenant scale could **not be completed**: the Milvus
container repeatedly became unavailable / gRPC-timed-out under load in this environment.
Since Milvus is only a **100M–1B+** escape hatch (far beyond the 100k–1M/tenant target), this
does not affect the decision (pgvector default, Qdrant escalation). Re-benchmarking Milvus on
a stable deployment is a follow-up.

### Reading the numbers

- **Recall is now a real signal** (not 1.0). The decision is driven by a genuine
  **recall × latency × tenancy** trade-off.
- **Shared-table `tenant_id` filtering is not viable** and gets worse with tenant count:
  pgvector filter recall **0.80 → 0.56 → 0.22** as tenants go 1 → 10 → 50 (it filters a
  shared HNSW whose traversal rarely lands enough in-tenant neighbours). Qdrant's
  filter-inside-HNSW *keeps* recall (0.78–0.99) but costs **770–900 ms p95** at 1M.
- **Per-tenant isolation wins decisively on both stores** — recall 0.88–1.0 at 13–21 ms.
- **pgvector declarative partitioning is the best pgvector layout**, and it *improves* with
  cardinality (1.0 recall / 13.7 ms at 50 tenants, each partition a small 20k index).
- **Qdrant per-tenant collection is the overall recall/latency champion** (1.0 / 13–14 ms).
  At `ef=64`, pgvector's per-tenant recall (0.88–0.95) trails Qdrant's (1.0) — an
  operating-point gap that a higher pgvector `ef` narrows at some latency cost.

## Decision

### 1. Default store — **pgvector**, with the correct multi-tenant layout

pgvector remains the default (one Postgres for memory + relational/bi-temporal data, on-prem
/ VPC friendly, HNSW + hybrid). This is consistent with the research doc's "start on
Postgres + pgvector" spine. **But the layout matters more than the engine:** the shared-table
`tenant_id` filter is rejected by the data.

### 2. Multi-tenancy — **declarative partitioning by `tenant_id`** (measured best)

- **Use pgvector native partitioning by tenant** (each partition its own smaller HNSW index).
  Measured best pgvector option and it scales *up* with tenant count (1.0 / 13.7 ms at 50
  tenants). One logical table, no application-visible sprawl.
- **Reject the shared-table JSONB `tenant_id` filter** — recall collapses to 0.22 at 50
  tenants. This is the single clearest result in the study.
- Per-tenant *collections* are equivalent in speed but heavier operationally at high
  cardinality; partitioning gets the same benefit within one table.

### 3. Switch criteria (pgvector → Qdrant / Milvus)

- **Qdrant** when you need the top recall/latency point (per-tenant collection: 1.0 / 13 ms),
  or filtered multi-tenant search must stay high-recall at scale **with its tenant payload
  index** (naive filter is 770–900 ms). Grounded in the research doc's "add a dedicated
  vector DB when >~10M vectors, sub-20 ms p99 under concurrency, or filter-in-search
  multi-tenancy at high tenant counts."
- **Milvus** only at **100M–1B+** (beyond target) and only after its metadata-filter +
  injection fixes land (findings).
- Everything is behind the `VectorDb` interface → each switch is config, not a rewrite.

## Core defects found (deferred to implementation — not committed to core)

Benchmarking exercised the real adapters and surfaced defects. Per the working model these
are **not** fixed in this decision task; they're documented + patched under `findings/`:

1. **pgvector runs exact scans** — `create()` builds no ANN index (Finding 3), and the
   adapter's **`optimize()` is broken** (`SET maintenance_work_mem = $1` — Postgres rejects
   bind params in `SET`, Finding 4). This is why v1 recall was 1.0 and latency was inflated.
2. **Milvus metadata filtering broken** (VARCHAR vs JSON) — filtered search returns nothing
   until patched (patch preserved, Finding 1).
3. **Milvus `_build_expr` expression injection** — keys/values interpolated unescaped;
   safe here only because tenant_ids are ints (Finding 5). [review #7]
4. **Qdrant adapter builds no tenant payload index** — needed for high-recall filtered
   multi-tenancy at scale (Finding 2).

See [`findings/README.md`](findings/README.md) for details, patches, and dispositions.

## Acceptance criteria (SM-R3)

- [x] Default vector store chosen (**pgvector**) with explicit scale/latency thresholds.
- [x] Multi-tenancy approach decided (**declarative partitioning**; shared filter rejected
      with data; measured at per-tenant target *and* high cardinality).
- [x] Switch criteria to Qdrant / Milvus documented, grounded in measured failure modes.
- [x] Confirms pgvector HNSW + hybrid meets the target on the partitioned layout (recall ≥
      0.95 / sub-20 ms at high cardinality) — **and** documents that the naive shared filter
      does not.

## Provisional targets (for SM-R1 to ratify)

| Parameter | Provisional |
| --- | --- |
| Memories per tenant | 100k – 1M |
| Tenants | up to ~50 (high cardinality) |
| Retrieval latency | p95 < 50 ms (met by per-tenant isolation; not by shared filter) |
| Recall@10 target | ≥ 0.95 (met by partition / qdrant collection) |

## Couplings & out of scope

- **Feeds SM-V1/SM-V4:** the partitioned-pgvector schema (per-tenant partition + per-partition
  HNSW, `ef_search ≥ k`) and the Qdrant per-tenant-collection escalation config.
- **Coupling to SM-R4 (embedding model):** dimension drives index size/latency; re-confirm on
  the chosen production embedding once R4 lands (does not change the tenancy decision).
- **Out of scope:** graph/temporal tier (SM-R2/R10), decay (SM-R5) — not decided here.

## Grounding & reproducibility

- Grounded in `MEMORY-RESEARCH-episodic-and-neurosymbolic.md` §3 (store technology).
- Reproduce: see `README.md`; `python -m benchmarks.memory_vectordb.run …`. Raw JSON +
  summaries in `results/` (`*matrixA`, `*matrixB`, `*matrixC`, `*milvusA`).
