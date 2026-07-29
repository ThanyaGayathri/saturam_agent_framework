# TEST_LOG - SM-R12 memory data model

Environment: dedicated venv (`pydantic`, `pgserver`, `psycopg[binary]`, `pgvector`).
Backend: embedded **PostgreSQL 16.2 + pgvector 0.6.2** via `pgserver` (no Docker; unix socket in
a unique per-run data dir). Each result confirmed by **two independent runs** on fresh instances.

### model.py - record model unit checks

**Status:** PASS

**Description:** Construct one of every tier; assert `tenant_id` required, unknown fields
rejected (`extra="forbid"`), SM-R10 `is_valid_at`/`is_current` correct across valid/invalid
windows. Cross-checked SPEC section 4.4 field-presence table against actual `model_fields` for
all 7 models (programmatic diff).

**Result:** All invariants hold; presence table matches the model exactly.

---

### ddl/schema.sql - DDL apply on real pgvector

**Status:** PASS

**Description:** `CREATE EXTENSION vector`, apply the full DDL, create per-tenant partitions,
build HNSW (`vector_cosine_ops`) + partial bi-temporal + traversal indexes.

**Result:** 6 partitioned parent tables + per-tenant partitions created; 20 indexes built on a
single-tenant apply. `vector(768)`, `PARTITION BY LIST (tenant_id)`, `TEXT[]`, `JSONB` all
accepted. "DDL APPLIED CLEANLY ON REAL pgvector".

---

### roundtrip_test.py - write -> read -> assert

**Status:** PASS (run twice, independent instances)

**Description:**
- All 6 persisted tiers round-trip **sync** and **async** through `PgMemoryStore` (embedding,
  bi-temporal, `TEXT[]`, `JSONB`, SM-R6 identity fields, SM-R7 `derived_by` all lossless;
  float4-exact fixtures). Sync pass on tenant `acme`, async on `beta` (write() is insert-only).
- **Bi-temporal via `expire()`** (SM-R10): write an open fact, `expire()` it on the **persisted
  row** (the real reuse-id UPDATE path), insert the new version; assert point-in-time "true at T"
  at each instant, that `expire()` **preserved `t_valid`** (only moved `expired_at`/`t_invalid`/
  `superseded_by`), that re-expire is a no-op, and that **both** rows survive.
- **SM-R6 episodic `expire()`** (array path): `expire()` on an `EpisodicRecord` writes a
  two-element `superseded_by` into the `TEXT[]` column via psycopg and reads back as a genuine
  Python list (`["seg_a","seg_b"]`), sync path; the async array path is covered separately. This is
  the tier the earlier bi-temporal test never exercised (it only hit the scalar `SemanticFact`).
- **Insert-only guardrail** (SM-R10): re-writing an existing id raises `UniqueViolation` and the
  original temporal fields are intact (proves `write()` can't silently mutate).
- **CHECK constraints** (SM-R10): `t_invalid < t_valid` and `expired_at < created_at` inserts are
  rejected on **all four** bi-temporal tables (episodic/fact/relation/link); NULL windows pass by
  design (`IS NULL` guards → open-ended/current rows allowed).
- **Cross-tenant isolation** (SM-R3): a row written under tenant `acme` is invisible from `beta`.
- **Seam proof:** the identical round-trip passes through `InMemoryMemoryStore` (second adapter,
  also insert-only) with zero call-site changes.

**Result:** `ALL ROUND-TRIP CHECKS PASSED` on both runs. sync 6/6, async 6/6, bi-temporal
expire()+point-in-time OK, insert-only guardrail OK, CHECK constraints OK, isolation OK,
in-memory adapter 6/6 sync + 6/6 async.

---

### Finalize gates (format / validate / core-untouched)

**Status:** PASS

**Description:** Applied the repo's ruff/mypy gates to the deliverable folder (the repo's own
`scripts/format.sh` / `validate.sh` target `agno`/`egptctl`/`cookbook`, not `benchmarks/`, so
they were applied equivalently here). Tooling matched to the repo pins where installable.

**Result:**
- `ruff format --check` (ruff **0.15.20**, the repo pin) -> "4 files already formatted".
- `ruff check` -> "All checks passed!" (confirmed the pinned ruff is required: under ruff 0.16.0
  even the seed `db/schemas/memory.py` reports 16 UP/FA errors; under 0.15.20 it is clean).
- `mypy` (repo config, pydantic plugin) -> "Success: no issues found in 2 source files"
  (`model.py`, `store.py`; the test harness is excluded like the repo excludes `tests*`).
- **Core untouched:** `git diff origin/main -- libs/` is **EMPTY**; all new files confined to
  `benchmarks/memory_datamodel/`.
- Round-trip re-run after formatting: still `ALL ROUND-TRIP CHECKS PASSED` (behavior unchanged).

---

### Known gaps / notes

- Docker unavailable in this environment -> used `pgserver`'s embedded real Postgres+pgvector
  (a genuine backend, not a mock). Same engine SM-R3 targets. See `findings/`.
- Scale (10M vectors) and latency were **not** re-measured here (that is SM-R3's job); this task
  validates schema correctness/round-trip fidelity, not retrieval performance.
- `embedding_dim=768` (SM-R4 Mode A) used in the DDL/test; Mode B swaps to `vector(1024)`.
