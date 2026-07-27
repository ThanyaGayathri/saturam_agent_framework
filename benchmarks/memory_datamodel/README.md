# SM-R12 - Memory data model / schemas

Design/spec deliverable for [aicoe-tracker#87](https://github.com/Saturam-Inc/aicoe-tracker/issues/87)
("Design: memory data model / schemas"). The record model is **executable and round-trip
tested against real pgvector**, so SM-C3 can implement it as core schemas + migrations without
re-deciding anything.

## What's here

| File | What |
|---|---|
| [`SPEC.md`](SPEC.md) | The spec: record model, per-tier variations, seed dispositions, KB-vs-memory separation, issue Q&A, acceptance-criteria map. |
| [`model.py`](model.py) | The implementable record model (pydantic v2): shared spine + 7 tier models. |
| [`ddl/schema.sql`](ddl/schema.sql) | Physical schema: 6 tenant-partitioned tables + HNSW / bi-temporal / traversal indexes. |
| [`ddl/MIGRATION.md`](ddl/MIGRATION.md) | Migration from the seed `UserMemory` + `learn` schemas (incl. the two seed time formats). |
| [`store.py`](store.py) | Plug-and-play store seam: abstract `MemoryStore` + `PgMemoryStore` (real) + `InMemoryMemoryStore` (proves swap). Sync **and** async. |
| [`roundtrip_test.py`](roundtrip_test.py) | write -> read -> assert on real pgvector; bi-temporal + isolation + seam checks. |
| [`TEST_LOG.md`](TEST_LOG.md) | Run log. |
| [`findings/`](findings/) | Notes for the implementation task (SM-C3) and cross-task couplings. |

## The model in one picture

```
TenantScoped (id, tenant_id*, scope ids, created_at, updated_at, metadata)   * = SM-R3 partition key
  + BiTemporal  (t_valid, t_invalid, expired_at, superseded_by)               SM-R10 four-timestamp
  + Embeddable  (embedding, embedding_dim, embedding_version)                 SM-R4
  + Decaying    (importance, strength, last_accessed_at)                      SM-R5
        |
        +-- WorkingMemory     (working, transient -> session store, no table)
        +-- EpisodicRecord    (episodic; content + when/where/why + decay + embedding)
        +-- SemanticFact      (semantic/fact; SPO + provenance + bi-temporal + embedding)
        +-- SemanticEntity    (semantic/entity; graph node, embeddable)
        +-- SemanticRelation  (semantic/relation; graph edge, bi-temporal)
        +-- ProceduralRecord  (procedural; rule/skill/prompt fragment)
        +-- MemoryLink        (typed cross-tier edge, bi-temporal)
```

## Run it (no Docker needed)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r benchmarks/memory_datamodel/requirements.txt
python -m benchmarks.memory_datamodel.roundtrip_test
```

`pgserver` starts a private PostgreSQL 16 + pgvector 0.6.2 on a unix socket in a unique
per-run data dir (no shared port, nothing to clean up). Expected tail:

```
Backend: PostgreSQL + pgvector -> 16.2 | 0.6.2
  [pg] sync round-trip: 6/6 tiers OK
  [pg] async round-trip: 6/6 tiers OK
  [pg] bi-temporal invalidate-not-delete + point-in-time OK
  [pg] cross-tenant isolation OK (0 leakage)
  [in-memory] sync round-trip: 6/6 tiers OK
  [in-memory] async round-trip: 6/6 tiers OK
ALL ROUND-TRIP CHECKS PASSED
```

## Scope

Decision/spec task: **no core code is touched** (`libs/agno/egpt/**` is owned by the rebrand
teammate). This folder is the whole deliverable; SM-C3 promotes it into core schemas +
migrations. Provisional targets (scale/dim) are flagged for SM-R1 to ratify. See `SPEC.md` for
dependency/sibling consistency (SM-R1/R3/R4/R5/R7/R10).
