# SM-R3 findings — core changes deferred to the implementation phase

SM-R3 is a **decision** task: it produces benchmarks + a decision document and does **not**
modify framework code (`libs/agno/egpt/**`). Core is owned by the ongoing refactor/rebrand
work and is changed only in the later **implementation (SM-C\*)** tasks.

Benchmarking surfaced **several** genuine core defects. They are recorded here as findings,
with a **prototype fix preserved as a patch** where one was needed to measure. **Do not
apply the patches as part of SM-R3.** Findings 3 and 5 are the most important — they mean
pgvector, as shipped, silently runs exact scans instead of HNSW.

## Finding 1 — Milvus adapter cannot filter by metadata (breaks `tenant_id` multi-tenancy)

- **Where:** `libs/agno/egpt/integrations/vectordb/milvus/milvus.py`
- **Symptom:** any metadata-filtered search (e.g. `filters={"tenant_id": t}`) returns 0
  rows. In the benchmark, Milvus `filter` tenancy scores **recall@10 = 0.0**; per-collection
  works (recall 1.0).
- **Root cause:** `meta_data` is stored as a `VARCHAR` JSON *string* (schema field is
  `DataType.VARCHAR`; inserts `json.dumps(meta_data)`), but `_build_expr` emits JSON-path
  filter expressions like `meta_data["tenant_id"] == v`, which only evaluate against a
  `DataType.JSON` field. A VARCHAR string can't be filtered by JSON path → no matches.
- **Prototype fix (validated, not shipped here):** store `meta_data` as a native JSON
  object across insert / upsert / `update_metadata`, and declare the hybrid-schema
  `meta_data` field as `DataType.JSON`. With this change a live Milvus returned only the
  matching tenant's rows, and the adapter's unit tests were updated to encode the new
  contract (35/35 passing).
- **Patch:** [`milvus-metadata-json.patch`](milvus-metadata-json.patch) (apply from repo
  root with `git apply benchmarks/memory_vectordb/findings/milvus-metadata-json.patch`).
- **Owner / when:** implementation phase (SM-C\*), coordinated with whoever owns the core
  refactor — Milvus is only a switch target at 100M+ scale, so this is not urgent for the
  initial pgvector-default decision, but it **must** be fixed before Milvus is used for
  `tenant_id`-filter multi-tenancy.

## Finding 2 — Qdrant adapter creates no tenant payload index (filtered recall collapses at scale)

- **Where:** `libs/agno/egpt/integrations/vectordb/qdrant/qdrant.py`
- **Symptom:** at 1M vectors / 50 tenants, Qdrant `tenant_id`-filtered search scores
  **recall@10 = 0.29** (vs 1.0 at 100k and 1.0 for per-collection at 1M).
- **Root cause (expected Qdrant behaviour, not a bug):** Qdrant filters *inside* HNSW. At
  low selectivity (each tenant ≈ 2 % of the data) the graph traversal is pruned away from
  true neighbours unless a **payload index** exists on the filtered field. The egpt adapter
  does not create one. Qdrant's multitenancy guide requires a keyword payload index on the
  tenant field (optionally `is_tenant=true`).
- **Fix (enhancement, implementation phase — no patch prototyped here):** have the Qdrant
  adapter create a keyword payload index on configured tenant/filter fields at collection
  creation (e.g. `create_payload_index(field_schema=models.KeywordIndexParams(is_tenant=True))`),
  and expose which fields to index.
- **Owner / when:** implementation phase (SM-C\*), before Qdrant is used as the multi-tenant
  escalation target.

## Finding 3 — pgvector runs EXACT SCANS: `create()` builds no ANN index (high impact)

- **Where:** `libs/agno/egpt/integrations/vectordb/pgvector/pgvector.py`
- **Symptom:** the whole v1 benchmark (PR #7) reported pgvector recall@10 = 1.0 with
  scan-level latency. Root cause: `PgVector.create()` only creates the **table**; the HNSW
  index is built by a *separate* `optimize()` call. Nothing in normal insert/search calls it,
  so pgvector queries fall back to an **exact sequential scan** — always recall 1.0, latency =
  scan cost (why v1 showed 134 ms where real HNSW is ~15 ms).
- **Fix (implementation phase):** build the vector index as part of the create/ingest
  lifecycle (or document that callers must call `optimize()` after load), so pgvector actually
  uses HNSW. The v2 benchmark builds the index via raw SQL to measure true HNSW behaviour.
- **Owner / when:** implementation phase (SM-C\*) — this materially affects every pgvector
  deployment, not just the benchmark.

## Finding 4 — pgvector `optimize()` is itself broken (bind param in `SET`)

- **Where:** `libs/agno/egpt/integrations/vectordb/pgvector/pgvector.py` (`_create_vector_index` /
  `optimize()` path)
- **Symptom:** calling `optimize()` (the intended way to build the index for Finding 3)
  raises `ProgrammingError: syntax error at or near "$1"` — it runs
  `SET maintenance_work_mem = %(value)s`, but Postgres does **not** accept bind parameters in
  `SET`. So even code that *does* call `optimize()` fails to build the index.
- **Fix (implementation phase):** interpolate the value as a literal (validated) instead of a
  bind parameter, e.g. `SET maintenance_work_mem = '2GB'`.
- **Owner / when:** implementation phase (SM-C\*), together with Finding 3.

## Finding 5 — Milvus `_build_expr` expression injection [PR review #7]

- **Where:** `libs/agno/egpt/integrations/vectordb/milvus/milvus.py` `_build_expr`
- **Symptom:** filter expressions interpolate keys and string values unescaped, e.g.
  `f'meta_data["{k}"] == "{v}"'`. Safe in this benchmark only because `tenant_id`s are
  integers, but a string value/key containing a quote breaks or injects into the expression.
- **Fix (implementation phase):** escape/parameterize keys and string values when building
  Milvus filter expressions (and add a test with quote-containing values).
- **Owner / when:** implementation phase (SM-C\*), alongside the Milvus metadata-JSON fix
  (Finding 1).
