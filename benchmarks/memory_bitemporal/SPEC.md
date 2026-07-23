# SM-R10 — Bi-temporal Model Spec

**Status:** Proposed (provisional targets pending SM-R1 ratification)
**Issue:** [Saturam-Inc/aicoe-tracker#85](https://github.com/Saturam-Inc/aicoe-tracker/issues/85) — "Design: bi-temporal model spec"
**Depends on:** SM-R1 (#76, ADR — ratifies provisional targets)
**Feeds:** SM-G4 (bi-temporal edges), SM-G5 (invalidation), SM-X11 (persistence/migration)
**Task type:** Design/Spec → the deliverable is an **implementable artifact** (fields, rules, query predicates, migration) precise enough that SM-G4/G5/X11 build from it without re-deciding, backed by a runnable prototype.

This spec is **testable**: every field, rule, and predicate below is exercised by the prototype
in this folder against three backends (in-memory reference, real Postgres, real Neo4j). See
§10 to reproduce.

---

## 1. Acceptance criteria (verbatim from #85)

- [x] **Four-timestamp edge model specified** — §5, [schema.sql](schema.sql), [models.py](models.py).
- [x] **Invalidate-not-delete rule defined (contradiction → set t_invalid)** — §6 (final rule is
      expire-and-reinsert; `t_invalid` is set on the reinserted bounded version, nothing deleted).
- [x] **Point-in-time ("true at T") query semantics defined** — §7 (5a/5b, SQL + Cypher).
- [x] **Valid-time extraction approach defined** — §8, [extraction.py](extraction.py) (6/6 measured).
- [x] **Decision on whether the episodic/vector tier carries valid-time** — §9 (yes: filter-only).

Evidence: all three bi-temporal backends score **100% (21/21)** point-in-time reconstruction;
the single-time baseline scores **57.1%**. See [results/](results/) and [TEST_LOG.md](TEST_LOG.md).

---

## 2. Questions to answer (verbatim from #85)

| Question | Answer |
|---|---|
| Four timestamps per edge (`t_valid`, `t_invalid`, `created_at`, `expired_at`) — valid vs transaction axes (Graphiti)? | **Yes.** Valid axis = `t_valid`/`t_invalid` (world truth); transaction axis = `created_at`/`expired_at` (system belief). Exact types/nullability/defaults in §5. |
| Contradiction handling: set `t_invalid` (invalidate) rather than delete? | **Yes, invalidate-not-delete.** Refined to **expire-and-reinsert (SCD-2)**: the superseded version is expired on the transaction axis and a bounded copy carrying `t_invalid` is inserted. Nothing is deleted or mutated in place. §6. |
| Point-in-time semantics: "true at T" = `t_valid <= T < t_invalid`? | **Yes**, with `COALESCE(t_invalid, ∞)` for open intervals and `expired_at IS NULL` to select current belief (5a). Full bi-temporal "as believed at TT" adds the transaction predicate (5b). §7. |
| How is valid-time extracted from content when stated ("since Jan")? | Pluggable `ValidTimeExtractor`: deterministic rule-based default (regex + month map; "since Jan" → that year's Jan 1; fallback to ingest time) + optional LLM adapter (`OpenAIResponses` gpt-5.5 / any egpt model). §8. |
| Does the vector/episodic tier also carry valid-time, or only the graph? | **Graph is the bi-temporal system-of-record.** The vector/episodic tier carries **filter-only** `valid_from`/`valid_to` (no invalidation authority). §9. |

---

## 3. Options evaluated (verbatim from #85, both exercised)

| Option | Exercised as | Point-in-time accuracy (21 probes) | Verdict |
|---|---|---|---|
| **Bi-temporal** (valid + transaction, invalidate-not-delete) — *recommended* | `ReferenceEdgeStore` + real Postgres + real Neo4j | **100% / 100% / 100%** | **CHOSEN** |
| **Single-time** (created/updated only) — current seed behavior | `SingleTimeEdgeStore` | **57.1%** | Rejected (baseline) |

The 9 single-time failures are exactly the point-in-time / correction / belief-evolution probes:
a single-time store cannot answer "what was true before the correction" or "what did we believe
at time TT". This measured gap — not prose — is the justification. Bi-temporal costs one extra
timestamp pair per edge and an expire-and-reinsert on contradiction; it buys point-in-time
reconstruction, provenance, and non-destructive correction.

---

## 4. Seed-context verification & disposition

Cited seed facts confirmed against **current** (post-rebrand) code; every surface has a disposition.

| Seed (issue) | Current location | Confirmed | Disposition |
|---|---|---|---|
| `db/schemas/memory.py:9` `UserMemory` — created/updated only | [libs/agno/egpt/db/schemas/memory.py:9](../../libs/agno/egpt/db/schemas/memory.py) | Yes — `created_at`/`updated_at` epoch-seconds `int`; no valid time, no invalidation | **EXTEND** (additive `valid_from`/`valid_to`, §9; spec-only, core untouched here) |
| Memory table | [libs/agno/egpt/db/postgres/schemas.py:40](../../libs/agno/egpt/db/postgres/schemas.py) `MEMORY_TABLE_SCHEMA` | Yes — `created_at BigInteger NOT NULL`, `updated_at BigInteger NULL` | **EXTEND** (additive migration §10; SM-X11 applies) |
| Temporal graph edge model (SM-G*) | none in `libs/` (greenfield) | Yes — no existing bi-temporal edge | **NEW** (this spec defines it) |
| Graph substrate | [libs/agno/egpt/vectordb/lightrag/](../../libs/agno/egpt/vectordb/lightrag) + pgvector | Yes | **OUT-OF-SCOPE note** — engine choice → SM-R2/SM-G*; spec is engine-agnostic (proven on pg + Neo4j) |
| Epoch utils | [libs/agno/egpt/utils/dttm.py:44](../../libs/agno/egpt/utils/dttm.py) `now_epoch_s`/`to_epoch_s` | Yes | **REUSE** (Python model normalization) |

---

## 5. The four-timestamp edge model (the implementable field list)

Two independent axes. **Valid time** = when the fact was true in the world
(`t_valid`/`t_invalid`). **Transaction time** = when the system held the row as belief
(`created_at`/`expired_at`).

| Field | Postgres | Neo4j prop | Python (`BiTemporalEdge`) | Null | Default | Meaning |
|---|---|---|---|---|---|---|
| `t_valid` | `timestamptz NOT NULL` | `datetime` | `int` (epoch s) | No | extracted valid-time, else ingest time | valid-from |
| `t_invalid` | `timestamptz` | `datetime`\|null | `Optional[int]` | Yes | `NULL` (open) | valid-to |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | `datetime` | `int` | No | now() at insert | transaction-from (immutable) |
| `expired_at` | `timestamptz` | `datetime`\|null | `Optional[int]` | Yes | `NULL` (current belief) | transaction-to |

Identity / payload carried by every edge (consumed by the rule and SM-G4): `edge_id` (PK),
`subject_id`, `predicate`, `object_id`, `user_id` (tenant scope), optional `fact`, optional
`embedding`. CHECK constraints: `t_invalid >= t_valid`, `expired_at >= created_at`.

**Type mapping:** Postgres `timestamptz` (native interval math, partial/btree index);
Neo4j native `datetime`; Python epoch-seconds `int` mirroring `UserMemory`/`MEMORY_TABLE_SCHEMA`
via `to_epoch_s`. The three are order-equivalent; adapters round-trip losslessly (verified — all
backends agree 100%). Open intervals are `NULL` upper bounds, coalesced to `+∞`.

**`t_invalid` vs `expired_at` (load-bearing):** `t_invalid` = the fact became false *in the
world* (dated real-world change; the row stays true for `[t_valid, t_invalid)`). `expired_at` =
the system stopped holding this *version* of the row (superseded by a newer version, or retracted
as an error). Distinct axes → both 5a and 5b reconstruct.

---

## 6. Invalidation rule (invalidate-not-delete; final = expire-and-reinsert / SCD-2)

Non-destructive. Nothing is ever deleted; the only mutation is setting `expired_at` once.
Implemented once in [store.py](store.py) `upsert_edge` over abstract primitives, so every backend
shares identical semantics.

**R1 — SUPERSEDE (dated real-world change).** `E_new` conflicts with a currently-believed
`E_old` when: same `(subject_id, predicate, user_id)`, different `object_id`, `E_old.expired_at
IS NULL`, and **`E_old`'s valid window contains `E_new.t_valid`** (`E_old.t_valid <= E_new.t_valid
< COALESCE(E_old.t_invalid, ∞)`). Then:
1. `E_old.expired_at := E_new.created_at` — expire the old **version** (transaction axis).
2. Insert a **bounded version** of the old fact: same identity + `t_valid = E_old.t_valid`,
   `t_invalid = E_new.t_valid`, `created_at = E_new.created_at`, `expired_at = NULL`.
3. Insert `E_new` (`expired_at = NULL`; `t_invalid` = back-date bound if any, else `NULL`).

Transaction-time versioning (not in-place `t_invalid`) is required: the P3 scenario eval proved
in-place mutation fails both the multi-step chain (5a — a third fact clobbers an already-set
bound) and belief-evolution (5b — past *beliefs about validity* aren't reconstructable). This is
the canonical Graphiti/SCD-2 model.

**R2 — RETRACT (transaction-time error correction).** The row was recorded in error (not a
real-world change): `E.expired_at := now()`; leave `t_valid`/`t_invalid` for audit. Drops from
current belief (5a) but remains visible "as believed before the retraction" (5b).

**Back-dated insert.** If a conflicting current fact starts *after* `E_new`, bound the new edge:
`E_new.t_invalid := min(E_new.t_invalid, earliest later conflict's t_valid)`.

**NOOP.** An incoming fact identical to a currently-believed one over a window covering its
`t_valid` is a no-op (no row, no invalidation).

**API for SM-G5** (sync + async): `upsert_edge(edge) → EdgeOutcome{INSERT|SUPERSEDE|RETRACT|NOOP}`,
`invalidate_edge(edge_id, t_invalid, at=None)`, `retract_edge(edge_id, expired_at)`. SQL-native
equivalents `bt_upsert_edge()` / `bt_retract_edge()` ship in [schema.sql](schema.sql) (validated).

---

## 7. Point-in-time query semantics (actual predicates)

### 5a — "true at world-time T" (current belief) — the primary query
```sql
-- Postgres
SELECT * FROM memory_edge
WHERE user_id=:u AND subject_id=:s AND predicate=:p
  AND t_valid <= :T AND :T < COALESCE(t_invalid, 'infinity'::timestamptz)
  AND expired_at IS NULL;
```
```cypher
// Neo4j
MATCH (s:Entity {id:$s})-[e:REL {user_id:$u, predicate:$p}]->(o:Entity)
WHERE e.t_valid <= datetime($T)
  AND datetime($T) < coalesce(e.t_invalid, datetime('9999-12-31T00:00:00Z'))
  AND e.expired_at IS NULL
RETURN o.id, e;
```

### 5b — "as believed at transaction-time TT, about world-time T" (audit / provenance)
```sql
-- Postgres: add the transaction predicate; drop the expired_at IS NULL filter
  AND t_valid    <= :T  AND :T  < COALESCE(t_invalid,  'infinity'::timestamptz)
  AND created_at <= :TT AND :TT < COALESCE(expired_at, 'infinity'::timestamptz);
```
Setting `:TT = now()` with `expired_at IS NULL` reduces 5b to 5a. Cypher form in
[graph_schema.cypher](graph_schema.cypher).

### 5c — vector/episodic tier (filter-only)
```sql
  AND valid_from <= :T AND :T < COALESCE(valid_to, 9223372036854775807)  -- on top of similarity
```

---

## 8. Valid-time extraction

`ValidTimeExtractor` (sync + async), [extraction.py](extraction.py):
- **`RuleBasedValidTimeExtractor`** (default, offline, deterministic): regex + month map →
  `ValidTimeSpan(t_valid, t_invalid, confidence, source)`. Handles `since Jan`, `since <Mon> <Year>`,
  `in <Year>`, `from … until …`, ISO dates; **fallback = ingest time** (`source="ingest_fallback"`).
  Measured **6/6** on the committed `EXTRACTION_CASES`.
- **`LLMValidTimeExtractor`** (optional): egpt `Agent` + `output_schema`, default
  `OpenAIResponses(id="gpt-5.5")`; any egpt model injectable (Azure, etc.). Agent built once and
  reused. Never required for CI/tests.

Extractor output populates `BiTemporalEdge.t_valid` (and `t_invalid` when a valid-to is stated).

---

## 9. Vector/episodic-tier valid-time decision

**Decision:** the **graph is the bi-temporal system-of-record** (all four timestamps + sole
invalidation authority). The **vector/episodic tier carries filter-only `valid_from`/`valid_to`**
(additive nullable `BIGINT` epoch columns) used to filter recall (5c) — **no `t_invalid`/
`expired_at`, no invalidation authority**. On graph invalidation, the same consolidation write
updates the vector copy's `valid_to`.

Rationale: (1) recall needs cheap point-in-time filtering without the 4-timestamp machinery;
(2) single-homing invalidation on the graph avoids split-brain contradiction resolution;
(3) additive nullable columns are backward-compatible and **extend** `UserMemory` /
`MEMORY_TABLE_SCHEMA` rather than forking them.

---

## 10. Couplings & handoff (what each downstream task consumes)

- **SM-G4 (bi-temporal edges):** the §5 field list + `BiTemporalEdge` model + [schema.sql](schema.sql)
  DDL + [graph_schema.cypher](graph_schema.cypher). Program against the `BiTemporalEdgeStore`
  abstraction ([store.py](store.py)); pick a backend adapter by config.
- **SM-G5 (invalidation):** the §6 rule + `upsert_edge`/`invalidate_edge`/`retract_edge` (sync+async)
  and SQL `bt_upsert_edge()`/`bt_retract_edge()`.
- **SM-X11 (persistence/migration):** the §5 table + these **additive** migrations (nullable →
  backward compatible; core owned by the rebrand teammate, so applied by SM-X11, not here):
  ```sql
  ALTER TABLE memories ADD COLUMN IF NOT EXISTS valid_from BIGINT;  -- epoch s; NULL = unknown
  ALTER TABLE memories ADD COLUMN IF NOT EXISTS valid_to   BIGINT;  -- epoch s; NULL = open
  ```
  and the new `memory_edge` table (§5) for the graph-lite/relational tier.
- **Quality metric** (for downstream regression): point-in-time reconstruction accuracy over the
  scenario set — target **1.00** for the bi-temporal implementation.

---

## 11. Out of scope & boundaries

- **Graph engine / vector-store choice** → SM-R2 / SM-G* (spec is engine-agnostic; proven on pg + Neo4j).
- **Retrieval fusion / ranking / decay** → SM-R3 (vector), SM-R5 (decay). This spec only adds the
  valid-time *filter* columns, not ranking.
- **Consolidation / extraction scheduling** (async vs inline) → the consolidation-loop tasks.
- **Scale / latency targets** → SM-R1 (provisional here). Neo4j per-op latency in the prototype
  (~0.7–4.7 s for the whole set) is a naive-session artifact, not a tuned number.

---

## 12. Consistency with dependencies & siblings

- **SM-R1 (#76, ADR):** not yet ratified — provisional targets (scale, latency) flagged for it.
- **Sibling decisions:** consistent with the neuro-symbolic hybrid (vector + temporal KG both
  first-class) locked in the research doc; the vector tier here matches SM-R3's pgvector direction
  (adds only filter columns); the graph bi-temporal model matches the research doc's Graphiti
  reference. No sibling's decision is pre-empted.

---

## 13. Grounding & reproducibility

- **Grounded in** `MEMORY-RESEARCH-episodic-and-neurosymbolic.md` §"Bi-temporal modeling"
  (Graphiti's 4 timestamps per edge; contradiction invalidates rather than deletes; point-in-time
  = valid at T).
- **Reproduce:** see [README.md](README.md). Start the two containers (unique names/ports), then
  `PYTHONPATH=libs/agno .venvs/bitemporal/bin/python -m benchmarks.memory_bitemporal.run`. Results
  land in [results/](results/); latest committed run in [TEST_LOG.md](TEST_LOG.md).
- **Core untouched:** `git diff origin/main -- libs/` is empty (this task is decision/spec only).
