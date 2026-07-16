# SM-R3 findings — core changes deferred to the implementation phase

SM-R3 is a **decision** task: it produces benchmarks + a decision document and does **not**
modify framework code (`libs/agno/egpt/**`). Core is owned by the ongoing refactor/rebrand
work and is changed only in the later **implementation (SM-C\*)** tasks.

Benchmarking surfaced one genuine core defect. It is recorded here as a finding, with a
**prototype fix preserved as a patch** so the implementation phase doesn't have to
re-derive it. **Do not apply the patch as part of SM-R3.**

## Finding 1 — Milvus adapter cannot filter by metadata (breaks `tenant_id` multi-tenancy)

- **Where:** `libs/agno/egpt/vectordb/milvus/milvus.py`
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

- **Where:** `libs/agno/egpt/vectordb/qdrant/qdrant.py`
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
