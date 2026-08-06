# SM-R10 — Phase 1 Foundation (seed verification + edge field/query semantics)

> **SUPERSEDED SNAPSHOT — do not implement from this file. [SPEC.md](../SPEC.md) is the
> artifact.** This is the P1 design trail, kept for provenance. Several things below were
> later corrected by measurement and are wrong as written:
>
> | Below | Corrected in SPEC.md |
> |---|---|
> | The 5c predicate `valid_from <= :T` (§5c) | **Wrong** — needs `COALESCE` on the *lower* bound too, else every pre-migration row (`valid_from IS NULL`) silently drops out of recall. SPEC §7, measured 7/10 → 10/10. |
> | 5c using `'infinity'::timestamptz` | The vector tier is epoch `BIGINT`; the sentinel is `9223372036854775807`. |
> | "all three bi-temporal backends … 21-probe set … 57.1%" (§4) | Superseded numbers: 30 probes, five backends, baseline 56.7%. |
> | "three bi-temporal backends score 100%" as independence evidence | Retracted framing — it is **one shared rule over three storage layers**. SPEC top note. |
> | No same-`t_valid` tie-break rule | SPEC §6 **R1-TIE**. |
> | No mutation-path constraint | SPEC §6.1 requires expire-only, enforced by a DB trigger. |

Status: **CHECKPOINT — awaiting approval before prototype (P2).**
Issue: Saturam-Inc/aicoe-tracker#85. Depends on: SM-R1 (#76, ratifies provisional targets).
Feeds: SM-G4 (bi-temporal edges), SM-G5 (invalidation), SM-X11.

This document pins the design foundation. Nothing here is prototyped yet; P2/P3 will
execute it for real on Postgres + Neo4j and against a zero-dependency reference store.

---

## 1. Seed-context verification & disposition

Every seed surface the issue cites, checked against **current** (post-rebrand) code in
this tree. No orphans.

| Seed surface (issue) | Verified location (current) | Fact confirmed? | Disposition |
|---|---|---|---|
| `db/schemas/memory.py:9` `UserMemory` — created_at/updated_at only | [libs/agno/egpt/db/schemas/memory.py:9](../../../libs/agno/egpt/db/schemas/memory.py) | **Yes.** Fields `created_at: Optional[int]`, `updated_at: Optional[int]` — **epoch seconds (int)**, single system-time axis. No valid time, no invalidation. | **EXTEND (additive, spec only — core untouched here).** Vector/episodic tier gains optional `valid_from`/`valid_to` filter columns. Do **not** fork; do **not** add graph-only fields here. |
| Memory persistence table (`created_at`/`updated_at` epoch) | [libs/agno/egpt/db/postgres/schemas.py:40](../../../libs/agno/egpt/db/postgres/schemas.py) `MEMORY_TABLE_SCHEMA` — `created_at BigInteger NOT NULL`, `updated_at BigInteger NULL` | **Yes.** Confirms epoch-seconds (BigInteger) convention across schema + table. | **EXTEND (additive migration).** Two nullable `BigInteger` columns; back-compat (NULL = unknown validity). SM-X11 owns applying it. |
| Temporal graph edge model (SM-G*) | No existing bi-temporal edge schema in `libs/` (grep for edge/`t_valid`/`MEMORY_EDGE` → only false positives: RemoteAgent, db adapter text). | **Yes — greenfield.** | **NEW.** The 4-timestamp edge is net-new; nothing to fork. SM-G4 builds from §3–§5 here. |
| Graph substrate present today | [libs/agno/egpt/vectordb/lightrag/lightrag.py](../../../libs/agno/egpt/vectordb/lightrag/lightrag.py) (graph-RAG); pgvector | n/a | **OUT-OF-SCOPE (note only).** Backend/engine choice → SM-R2/SM-G*. This spec is engine-agnostic; prototype proves it on Postgres + Neo4j. |
| Epoch conversion utils | [libs/agno/egpt/utils/dttm.py:44](../../../libs/agno/egpt/utils/dttm.py) `now_epoch_s()`, `:48` `to_epoch_s()` | **Yes.** | **REUSE.** Python edge model uses these for epoch normalization (mirrors `UserMemory.__post_init__`). |

---

## 2. Options evaluated (both exercised in P3, not decided from prose)

| Option | Behavior | Point-in-time? | Correction | Provenance | Verdict |
|---|---|---|---|---|---|
| **A. Bi-temporal** (valid + transaction, invalidate-not-delete) | 4 timestamps/edge; contradiction sets `t_invalid`+`expired_at`, keeps row | **Yes** (reconstruct "true at T") | Non-destructive | Full (audit both axes) | **RECOMMENDED** |
| **B. Single-time** (created/updated only) — current seed | 2 timestamps; correction = in-place update or delete | **No** | Destructive (data loss) | None | Rejected — baseline |

P3 runs the identical insert→correct→query scenario set against both. The metric
(**point-in-time reconstruction accuracy**, §6) is expected to be 100% for A and to
**fail on every correction scenario for B** (B cannot answer "what was true before the
correction"). That measured gap is the evidence for the decision — not this table.

---

## 3. The four-timestamp edge model (exact fields)

Two independent axes (Graphiti model):

- **Valid time** = when the fact was true *in the world*.
  - `t_valid` — world-time the fact became true (valid-from).
  - `t_invalid` — world-time the fact stopped being true (valid-to); `NULL` = still true (open interval).
- **Transaction time** = when the *system* held the row as current belief.
  - `created_at` — transaction-time the edge row was written (immutable).
  - `expired_at` — transaction-time the row ceased to be current belief; `NULL` = currently believed.

### Field table

| Field | Postgres | Neo4j (edge prop) | Python model (`BiTemporalEdge`) | Nullable | Default | Meaning |
|---|---|---|---|---|---|---|
| `t_valid` | `timestamptz NOT NULL` | `datetime` | `t_valid: int` (epoch s) | No | = extracted valid-time, else ingest time | valid-from |
| `t_invalid` | `timestamptz NULL` | `datetime` \| null | `t_invalid: Optional[int]` | Yes | `NULL` (open) | valid-to |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | `datetime` | `created_at: int` | No | `now()` at insert | transaction-from |
| `expired_at` | `timestamptz NULL` | `datetime` \| null | `expired_at: Optional[int]` | Yes | `NULL` (current) | transaction-to |

Plus the relation identity carried by every edge (needed by the invalidation rule and
by SM-G4): `edge_id` (PK), `subject_id`, `predicate` (relation type), `object_id`,
`user_id` (tenant scope), optional `fact` (raw text) + `embedding` (for hybrid recall).

**Type mapping decision (per Gate-1 answer):** Postgres = `timestamptz` (native interval
math + BRIN/btree indexable); Neo4j = native `datetime()`; Python model = epoch-seconds
`int` mirroring `UserMemory`. Boundary conversion via reused `to_epoch_s` /
`now_epoch_s`. The three representations are equivalent orderings; the mapping is
documented so adapters round-trip losslessly.

**Why `t_invalid` ≠ `expired_at` (the load-bearing distinction):**
`t_invalid` says the fact *became false in the world* at that time (a dated real-world
change; the row stays accurate for its `[t_valid, t_invalid)` window). `expired_at` says the
*system stopped holding this row at all* — reserved for R2 error correction (the row's
content was wrong). A real-world change sets `t_invalid` and leaves `expired_at = NULL`; only
an error sets `expired_at`. Keeping them separate is what makes both "what was true at
world-time T" (5a) **and** "what the system believed at transaction-time TT" (5b)
reconstructable — see §4 R1 refinement.

---

## 4. Invalidation rule (invalidate-not-delete)

Two precise sub-rules. Both are **non-destructive** (no `DELETE`).

**R1 — SUPERSEDE (dated real-world change), FINAL model (transaction-time versioned / SCD-2).**
A new edge `E_new` contradicts a currently-believed `E_old` when: same `(subject_id,
predicate, user_id)`, conflicting `object_id`, `E_old.expired_at IS NULL`, and `E_old`'s valid
window **contains** `E_new.t_valid` (`E_old.t_valid <= E_new.t_valid < COALESCE(E_old.t_invalid, ∞)`).
Then:
1. `E_old.expired_at := E_new.created_at`  — expire the old *version* on the transaction axis.
2. Insert a **bounded version** of the old fact: same identity + `t_valid = E_old.t_valid`,
   `t_invalid = E_new.t_valid`, `created_at = E_new.created_at`, `expired_at = NULL`.
3. Insert `E_new` (`t_invalid = NULL`/back-date bound, `expired_at = NULL`).

> **Evolution of this rule (two corrections, both caught by tests — the process working):**
> The P1 draft set `E_old.expired_at := now()` on supersede. P2 smoke suggested dropping that
> (mutate `t_invalid` in place, keep `expired_at` for R2 only) — which fixes 5a but the **P3
> scenario eval proved insufficient**: (a) `three_way_chain` failed 5a because in-place
> `t_invalid` writes clobber an already-bounded edge when a third fact arrives; (b)
> `belief_evolution` failed 5b because reconstructing a *past belief about validity* requires
> the validity bound itself to be transaction-time-versioned. The correct model is
> **expire-and-reinsert with a containment guard** (above) — the canonical bi-temporal / SCD-2
> approach. Edges are never mutated in place except setting `expired_at` once. Verified: all
> three bi-temporal backends (reference/pg/neo4j) score 100% on the 21-probe set; the
> single-time baseline scores 57.1%.
>
> *(Numbers superseded: the set is now 30 probes across five backends, baseline 56.7%. And the
> "three backends agree" framing was retracted — they share one rule over three storage layers;
> the independent check is the SQL/Cypher-native equivalence. See SPEC.md and TEST_LOG.md.)*

**Back-dated insert.** If a conflicting current fact starts *after* `E_new`, do not supersede;
bound the new edge: `E_new.t_invalid := min(E_new.t_invalid, earliest later conflict's t_valid)`.

**R2 — RETRACT (transaction-time correction of an error; fact was never true).**
When we learn `E_old` was mistaken (not a real-world change):
1. `E_old.expired_at := now()`  — drop from current belief.
2. Leave `t_valid`/`t_invalid` as recorded (audit of what we *believed we knew*).

Idempotency: an incoming edge identical to a currently-believed edge (same identity +
overlapping valid interval, no conflict) is a NOOP — no new row, no invalidation.
`t_invalid`/`expired_at` are only ever set once (monotonic); a row with `expired_at` set is
frozen.

Public API this exposes to SM-G5 (both **sync + async** variants):
`invalidate_edge(edge_id, t_invalid, expired_at=now)` and the higher-level
`upsert_edge(edge)` that applies R1/R2/NOOP.

---

## 5. Point-in-time query semantics (actual predicates)

### 5a. Current-belief point-in-time — "true at world-time T"
The primary "true at T" query SM-G4/X11 consume.

**Postgres:**
```sql
SELECT * FROM memory_edge
WHERE subject_id = :s AND predicate = :p AND user_id = :u
  AND t_valid <= :T
  AND :T < COALESCE(t_invalid, 'infinity'::timestamptz)   -- open interval
  AND expired_at IS NULL;                                  -- current belief only
```
(Equivalent explicit form: `AND (t_invalid IS NULL OR :T < t_invalid)`.)

**Neo4j / Cypher:**
```cypher
MATCH (a {id:$s})-[e:REL {user_id:$u}]->(b)
WHERE e.predicate = $p
  AND e.t_valid <= $T
  AND ($T < e.t_invalid OR e.t_invalid IS NULL)
  AND e.expired_at IS NULL
RETURN e;
```
(COALESCE form: `AND $T < coalesce(e.t_invalid, datetime('9999-12-31T00:00:00Z'))`.)

### 5b. Full bi-temporal — "what the system believed at transaction-time TT, about world-time T"
Audit/provenance query (both axes); this is the capability Option B cannot provide.

**Postgres:**
```sql
WHERE t_valid    <= :T  AND :T  < COALESCE(t_invalid,  'infinity'::timestamptz)
  AND created_at <= :TT AND :TT < COALESCE(expired_at, 'infinity'::timestamptz);
```
Setting `:TT = now()` and requiring `expired_at IS NULL` reduces 5b to 5a.

### 5c. Vector/episodic tier (filter-only, per Gate-1 decision)
The vector tier carries `valid_from`/`valid_to` only, for **recall filtering** — no
invalidation authority. Predicate applied as a metadata filter on top of similarity:
```sql
-- WRONG AS DRAFTED -- kept to show what was corrected. Do not copy.
-- Two defects: (1) no COALESCE on the LOWER bound, so every pre-migration row
-- (valid_from IS NULL) evaluates to NULL and silently vanishes from recall;
-- (2) the vector tier is epoch BIGINT, not timestamptz.
-- Ratified form (SPEC.md SS7), measured 10/10 vs 7/10 for the draft below:
--   AND COALESCE(valid_from, -9223372036854775808) <= :T
--   AND :T < COALESCE(valid_to,  9223372036854775807)
AND valid_from <= :T AND :T < COALESCE(valid_to, 'infinity'::timestamptz)
```
Contradiction handling and `t_invalid`/`expired_at` live **only on the graph**
(system-of-record). Rationale finalized in P4/SPEC.

---

## 6. Quality metric (how the design is judged)

**Point-in-time reconstruction accuracy** = fraction of `(subject, predicate, T)` probes
whose returned currently-believed fact equals the ground-truth fact valid at `T`, across a
scenario set of insert / correct(supersede) / retract / open-interval / "since Jan" cases.
Target: **1.00** for Option A on every scenario; Option B measured for contrast (expected
to fail all correction probes). Full definition + scenario table lands with P3.

---

## 7. Valid-time extraction (approach, prototyped in P4)

`ValidTimeExtractor` abstraction (both sync + async), two adapters:
- **RuleBasedExtractor** (default, offline, deterministic): parses explicit cues
  ("since Jan", "in 2019", "until last March") → `t_valid`/`t_invalid`; falls back to
  ingest time when absent. This is what CI runs.
- **LLMExtractor** (optional): `OpenAIResponses` (gpt-5.5) or the Azure Responses backend
  per `.env`; used only when configured. Never required for the prototype/tests to pass.

Decision (Gate-1): the **graph** is the bi-temporal system-of-record; the **vector/episodic
tier carries valid-time as filter-only columns**. Full rationale in SPEC.md (P5).

---

## 8. Open items carried into later phases
- P3 defines the full scenario table + runs A vs B on Postgres + Neo4j + reference store.
- Provisional: any scale/index-strategy target is flagged for **SM-R1 (#76)** to ratify.
- Migration DDL (additive columns) is spec-only here; SM-X11 applies it (core untouched).
