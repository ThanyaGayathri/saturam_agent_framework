# SM-R10 — Bi-temporal Model Spec

**Status:** Proposed (provisional targets pending SM-R1 ratification)
**Issue:** [Saturam-Inc/aicoe-tracker#85](https://github.com/Saturam-Inc/aicoe-tracker/issues/85) — "Design: bi-temporal model spec"
**Depends on:** SM-R1 (#76, ADR — ratifies provisional targets)
**Feeds:** SM-G4 (bi-temporal edges), SM-G5 (invalidation), SM-X11 (persistence/migration)
**Task type:** Design/Spec → the deliverable is an **implementable artifact** (fields, rules, query predicates, migration) precise enough that SM-G4/G5/X11 build from it without re-deciding, backed by a runnable prototype.

This spec is **testable**: every field, rule, and predicate below is exercised by the prototype
in this folder. See §10 to reproduce.

**What the evidence covers — read this before citing the numbers.** The invalidation rule and the
query semantics live *once*, in [store.py](store.py), and the reference / Postgres / Neo4j
adapters implement only storage primitives. So a matching score across those three is
**one shared rule over three storage layers**, not three independent derivations of the semantics.

The separately-shipped artifacts — the SQL-native `bt_upsert_edge()` and the 5a/5b SQL/Cypher
predicates in §7, which are what SM-G4/G5/X11 actually consume — are exercised by dedicated native
adapters ([sql_native_store.py](adapters/sql_native_store.py),
[cypher_native_store.py](adapters/cypher_native_store.py)) as an **equivalence test** against the
Python reference. **Measured: both return exactly what the reference returns on all 30 probes**
(per-probe comparison, not just matching aggregate accuracy). So the shipped SQL and Cypher are
confirmed faithful re-expressions of the rule — which is the property downstream needs — while the
semantics themselves remain singly-derived. [TEST_LOG.md](TEST_LOG.md) records the per-path status
of each run.

---

## 1. Acceptance criteria (verbatim from #85)

- [x] **Four-timestamp edge model specified** — §5, [schema.sql](schema.sql), [models.py](models.py).
- [x] **Invalidate-not-delete rule defined (contradiction → set t_invalid)** — §6 (final rule is
      expire-and-reinsert; `t_invalid` is set on the reinserted bounded version, nothing deleted).
- [x] **Point-in-time ("true at T") query semantics defined** — §7 (5a/5b, SQL + Cypher).
- [x] **Valid-time extraction approach defined** — §8, [extraction.py](extraction.py) (6/6 measured).
- [x] **Decision on whether the episodic/vector tier carries valid-time** — §9 (yes: filter-only,
      and now *measured* by the 5c probes rather than asserted).

Evidence: the bi-temporal model scores **100% (30/30)** point-in-time reconstruction across all
five bi-temporal backends — including the SQL-native and Cypher-native paths, which are
**equivalent to the Python reference on every probe**. The single-time baseline scores
**56.7% (17/30)**. The 5c vector-tier filter scores **10/10** on both the reference and real SQL,
and the §6.1 append-only guardrail **8/8**. See [results/](results/) and
[TEST_LOG.md](TEST_LOG.md).

---

## 2. Questions to answer (verbatim from #85)

| Question | Answer |
|---|---|
| Four timestamps per edge (`t_valid`, `t_invalid`, `created_at`, `expired_at`) — valid vs transaction axes (Graphiti)? | **Yes.** Valid axis = `t_valid`/`t_invalid` (world truth); transaction axis = `created_at`/`expired_at` (system belief). Exact types/nullability/defaults in §5. |
| Contradiction handling: set `t_invalid` (invalidate) rather than delete? | **Yes, invalidate-not-delete.** Refined to **expire-and-reinsert (SCD-2)**: the superseded version is expired on the transaction axis and a bounded copy carrying `t_invalid` is inserted. Nothing is deleted or mutated in place — enforced by a DB trigger, §6.1. Simultaneous conflicting asserts resolve last-writer-wins (R1-TIE). §6. |
| Point-in-time semantics: "true at T" = `t_valid <= T < t_invalid`? | **Yes** — and **both bounds are required** (§7 pitfall callout), with `COALESCE(t_invalid, ∞)` for open intervals and `expired_at IS NULL` to select current belief (5a). Full bi-temporal "as believed at TT" adds the transaction predicate (5b). §7. |
| How is valid-time extracted from content when stated ("since Jan")? | Pluggable `ValidTimeExtractor`: deterministic rule-based default (regex + month map; "since Jan" → that year's Jan 1; fallback to ingest time) + optional LLM adapter (`OpenAIResponses` gpt-5.5 / any egpt model). §8. |
| Does the vector/episodic tier also carry valid-time, or only the graph? | **Graph is the bi-temporal system-of-record.** The vector/episodic tier carries **filter-only** `valid_from`/`valid_to` (no invalidation authority). §9. |

---

## 3. Options evaluated (verbatim from #85, both exercised)

| Option | Exercised as | Point-in-time accuracy (30 probes) | Verdict |
|---|---|---|---|
| **Bi-temporal** (valid + transaction, invalidate-not-delete) — *recommended* | `ReferenceEdgeStore`; the same rule over real Postgres + real Neo4j; and the SQL-native / Cypher-native shipped artifacts | **100%** (all five) | **CHOSEN** |
| **Single-time** (created/updated only) — current seed behavior | `SingleTimeEdgeStore` | **56.7%** | Rejected (baseline) |

The 13 single-time failures are exactly the point-in-time / correction / belief-evolution probes:
a single-time store cannot answer "what was true before the correction" or "what did we believe
at time TT". This measured gap — not prose — is the justification. Bi-temporal costs one extra
timestamp pair per edge and an expire-and-reinsert on contradiction; it buys point-in-time
reconstruction, provenance, and non-destructive correction.

The Postgres and Neo4j rows are **storage-layer** confirmations: they run the same `store.py`
rule against real engines, which proves the four-timestamp model round-trips losslessly through
`timestamptz` and Neo4j `datetime`. The SQL-native and Cypher-native rows are the stronger claim —
they bypass `store.py` entirely and are confirmed **probe-for-probe identical** to the reference.
None of these is claimed as an independent re-derivation of the semantics; see the equivalence
note at the top of this document.

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

**R1-TIE — simultaneous conflicting asserts at the same `t_valid`.** Two contradictory facts may
claim the *same* valid-from (concurrent extraction from one turn, or a re-assert during replay).
Resolution: **last writer wins on the transaction axis** — R1 applies unchanged, because
`valid_at` is half-open (`t_valid <= t < t_invalid`) and therefore *contains* its own left
endpoint. The earlier fact is expired and reinserted bounded at `t_invalid = t_valid`, i.e. with
a **zero-length valid interval** `[t_valid, t_valid)`. That row is never current-valid at any `T`
(so 5a has exactly one answer) but stays fully auditable through 5b at any transaction time
before the tie-break. The `t_invalid >= t_valid` CHECK admits the degenerate interval by design.

This is deterministic given ingestion order, and ingestion order *is* the transaction axis — so
"whoever wrote second" is a recorded fact, not a race. Consumers that need a different policy
(e.g. confidence-weighted) must implement it **before** calling `upsert_edge`, by choosing which
assertion to submit; the store does not arbitrate on payload. Measured by the
`same_t_valid_tiebreak` scenario (4 probes, including both 5b directions).

**API for SM-G5** (sync + async): `upsert_edge(edge) → EdgeOutcome{INSERT|SUPERSEDE|RETRACT|NOOP}`,
`invalidate_edge(edge_id, t_invalid, at=None)`, `retract_edge(edge_id, expired_at)`. SQL-native
equivalents `bt_upsert_edge()` / `bt_retract_edge()` ship in [schema.sql](schema.sql).

`bt_upsert_edge()` takes `p_created_at TIMESTAMPTZ DEFAULT now()`. The transaction time **must**
be injectable, not hardcoded to `now()`: backfill, replay, and every 5b reconstruction depend on
asserting a fact whose transaction time is not the wall clock ("learned in July that NYC held
since January"). An earlier revision of this spec hardcoded `now()`, which made those cases
inexpressible through the SQL path.

### 6.1 Mutation path — expire-only (REQUIRED of any implementation)

The rule above is only non-destructive if the storage layer *cannot* mutate history. Any SQL
implementation **must** make expire-and-reinsert the only mutation path:

- The **only** column an `UPDATE` may ever write is `expired_at`, and only on a row where it is
  currently `NULL` (write-once).
- `t_valid`, `t_invalid`, `created_at` and the identity columns are **immutable after insert**.
- Correcting a fact means *inserting a new version*, never rewriting the existing row.

This is not caller discipline — it is enforced in the database. [schema.sql](schema.sql) §2b ships
the `memory_edge_append_only` trigger, which rejects any in-place change to the identity columns
or either time axis, and [guardrail.py](guardrail.py) **scores** it (8/8 measured: all six illegal
mutations rejected, both legal ones allowed) rather than assuming it works. Reason it is mandatory
rather than advisory: a blanket
`INSERT ... ON CONFLICT DO UPDATE SET <every column>` (the default shape most ORMs and upsert
helpers generate) silently rewrites `t_valid`/`t_invalid` in place, which destroys exactly the
history SCD-2 exists to preserve — 5b stops reconstructing and the audit trail is gone, with no
error and no failing query. A sibling storage layer in this programme already shipped that write
path, so SM-X11 and SM-G4 should treat the trigger as part of the DDL, not an optional extra.

**Neo4j has no equivalent enforcement — SM-G4 must close this explicitly.** Neo4j 5 constraints
cover existence, uniqueness and property type; there is **no property-immutability constraint**,
so the Postgres trigger has no direct counterpart. `CypherNativeEdgeStore` never rewrites a time
axis, but that is discipline, not enforcement: any consumer can issue `SET e.t_valid = …` and
nothing stops it. Pick one of these — this is a required decision, not a nice-to-have:

1. **APOC trigger** (`apoc.trigger.install`, needs `apoc.trigger.enabled=true`): abort on any
   transaction whose `assignedNodeProperties`/`assignedRelationshipProperties` touch `t_valid`,
   `t_invalid` or `created_at` on a `:REL`. Closest to parity with the Postgres trigger; costs an
   APOC dependency, which SM-R2's engine choice may or may not permit.
2. **Write-through repository + scheduled invariant audit**, if APOC is unavailable. Route every
   write through the `BiTemporalEdgeStore` seam, and run this as a scheduled check — it detects
   the *damage* in-place mutation causes, even though it cannot prevent the write:
   ```cypher
   // Any current edge whose valid interval is inverted, or two current versions of one identity
   // whose valid windows overlap -- both are impossible under expire-and-reinsert.
   MATCH (s:Entity)-[e:REL]->(o:Entity) WHERE e.expired_at IS NULL
     AND e.t_invalid IS NOT NULL AND e.t_invalid < e.t_valid
   RETURN e.edge_id AS violation, 'inverted interval' AS why
   UNION
   MATCH (s:Entity)-[a:REL]->(:Entity), (s)-[b:REL]->(:Entity)
   WHERE a.expired_at IS NULL AND b.expired_at IS NULL AND a.edge_id < b.edge_id
     AND a.user_id = b.user_id AND a.predicate = b.predicate
     AND a.t_valid < coalesce(b.t_invalid, datetime('9999-12-31T00:00:00Z'))
     AND b.t_valid < coalesce(a.t_invalid, datetime('9999-12-31T00:00:00Z'))
   RETURN a.edge_id AS violation, 'overlapping current versions' AS why;
   ```
   Both queries must return **zero rows** on a healthy store. Note this is detection after the
   fact, strictly weaker than the Postgres trigger — record that asymmetry wherever the engine
   choice is ratified.

**The audit query above is verified** (unlike the §9.1 re-sync statement): run against the live
Neo4j 5 container it returns zero rows on a healthy store, and after injecting
`SET e.t_valid = e.t_invalid + duration({days:30})` on a bounded edge it reports that edge as
`inverted interval`. That injection also demonstrates the asymmetry concretely — **Neo4j accepted
the in-place rewrite silently**, where Postgres rejects the identical mutation via the trigger
(`guardrail.py` case `in_place_t_valid`). What is *not* covered: option 1's APOC trigger is
unverified, and the guardrail suite ([guardrail.py](guardrail.py), 8/8) is Postgres-only because
there is no native Neo4j mechanism to test.

---

## 7. Point-in-time query semantics (actual predicates)

> **Common pitfall — the validity test is TWO-SIDED. Implement both bounds.**
>
> "True at `T`" is `t_valid <= T < COALESCE(t_invalid, ∞)`. Both comparisons are load-bearing and
> an implementation that keeps only one is wrong in a way that **passes most tests**:
>
> | Omitted | Symptom | Why tests miss it |
> |---|---|---|
> | the **lower** bound (`t_valid <= T`) | a fact leaks at times *before it was ever true* — including future-dated / back-dated records surfacing in past queries | only shows up when a probe asks about a `T` earlier than a known `t_valid`; a suite that mostly probes "now" and "recently" never asks |
> | the **upper** bound (`T < t_invalid`) | superseded facts never disappear | caught by almost any correction probe, so this one rarely ships |
>
> The asymmetry is the trap: the upper bound is the one everybody remembers to write, because
> forgetting it breaks the headline "invalidate on contradiction" demo. Forgetting the *lower*
> bound breaks nothing visible until someone queries the past. A sibling implementation of these
> semantics shipped with `t_invalid`-only filtering for exactly this reason.
>
> The same two-sidedness applies on the transaction axis in 5b
> (`created_at <= TT < COALESCE(expired_at, ∞)`) and to the 5c filter below. Any conformance
> suite for this spec **must** include at least one probe at a `T` strictly before the earliest
> `t_valid` — `simple_insert` and `supersede_relocation` both carry one (expected answer: no fact),
and those probes are among the 30 the SQL and Cypher predicates were verified against.

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
-- on top of similarity; BOTH bounds COALESCE'd
  AND COALESCE(valid_from, -9223372036854775808) <= :T
  AND :T < COALESCE(valid_to,  9223372036854775807)
```

**The `COALESCE` on the lower bound is load-bearing, not defensive.** The §9 migration is
additive, so every row written before it has `valid_from IS NULL`. Under a bare
`valid_from <= :T`, that comparison evaluates to `NULL` and the row is filtered out — so applying
the filter would **silently drop every memory that predates the migration**, with no error. `NULL
valid_from` means "unknown, assume always valid", which is what `-infinity` encodes. Measured on
both the in-memory reference **and real Postgres**: the ratified predicate scores **10/10** on
each, the bare predicate **7/10** on each, failing exactly the three `vec_legacy_null_valid_from`
probes — the same three in Python and in SQL, so this is real `NULL`-comparison behaviour, not a
modelling artifact. The bare form is kept in [vector_tier.py](vector_tier.py) as a scored
counterexample so the regression cannot return.

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

### 9.1 Consistency model for the derived `valid_to` (the two writes are not atomic)

Invalidation *authority* is single-homed on the graph, which avoids split-brain. But the vector
tier's `valid_to` is a **second, derived write**, and "the same consolidation write updates it" is
not an atomicity guarantee: if the graph commit succeeds and the vector update lags, fails, or is
retried into a different transaction, the 5c filter will surface a **superseded memory**. The
model is therefore stated explicitly rather than left implicit:

1. **Co-write transactionally when the two tiers share a database.** For the graph-lite/relational
   deployment (`memory_edge` and `memories` in one Postgres), the edge invalidation and the
   `valid_to` write go in **one transaction**. This is the default deployment and it gets real
   atomicity — no reconciliation needed.

2. **When the tiers are separate stores** (e.g. Neo4j graph + pgvector), the write **cannot** be
   atomic, so the vector copy is treated as an **eventually-consistent projection of the graph**:
   - **The graph is authoritative on read-back.** The 5c filter is an *optimisation* that keeps
     obviously-stale candidates out of the similarity search. It is **advisory**: a caller that
     needs a guaranteed-current answer resolves the surviving candidates against 5a before
     returning them. Recall may therefore be briefly over-inclusive; it is never under-inclusive,
     because a missing `valid_to` only fails to *exclude*.
   - **An idempotent re-sync repairs drift.** `valid_to` is derived, never decided locally, so
     re-deriving it from the graph is safe to run at any time and any number of times. The
     *shape* of that re-sync — not a runnable statement — is:

     ```sql
     -- ILLUSTRATIVE, NOT EXECUTED. See the two prerequisites below before using this.
     -- Re-project every bounded edge's valid-time end onto its vector copy.
     UPDATE memories m
        SET valid_to = EXTRACT(EPOCH FROM e.t_invalid)::bigint
       FROM memory_edge e
      WHERE e.expired_at IS NULL
        AND e.t_invalid IS NOT NULL
        AND m.memory_id = e.edge_id            -- ASSUMED provenance link; does not exist yet
        AND m.valid_to IS DISTINCT FROM EXTRACT(EPOCH FROM e.t_invalid)::bigint;
     ```

     **Two things this statement assumes that this spec does not deliver**, both owned by whoever
     implements the consolidation write (SM-X11):

     1. **The join key does not exist in this spec.** `m.memory_id = e.edge_id` presumes an
        edge→memory provenance link written at consolidation time. No such column is specified in
        §5 or §9, and the additive migration does not add one. A real implementation must first
        decide how a vector row points back at the edge it was derived from — one edge can produce
        several memories and one memory can cite several edges, so this may well need a link table
        rather than a column. Until that is settled, the `WHERE` clause above is a placeholder.

        **Cross-reference — SM-R12 (#18) owns the schema and already has a candidate.** Its
        `SemanticFact` and `SemanticRelation` models carry `source_episode_ids[]` (+ `derived_by`)
        provenance, which points relation→episodes — the direction this re-sync needs. That is a
        plausible basis for the join (`... WHERE m.memory_id = ANY(e.source_episode_ids)`), but it
        lives on SM-R12's `SemanticRelation`, **not** on this spec's `memory_edge`, and it has not
        been reconciled against this use. SM-X11 should settle it with SM-R12 rather than
        inventing a third link; flagged so it is not rediscovered independently.
     2. **It has never been run.** Unlike the 5c filter, the 5a/5b predicates and the §6.1
        trigger — all of which are exercised and scored against live engines — this statement is
        untested. It is here to fix the *intent* (re-derive from the graph, compare before
        writing, safe to repeat), not to be copied into a migration.

     The properties that *are* load-bearing, and that any implementation must preserve: it reads
     the graph as the source of truth, writes only where the value actually differs
     (`IS DISTINCT FROM`, so a no-op re-run touches no rows), and is a pure function of graph
     state — so it can run on the consolidation cadence and after any partial failure without a
     re-run making things worse.

3. **Never the reverse direction.** Nothing may propagate `valid_to` from the vector tier back
   into the graph — that would hand invalidation authority to the tier that is explicitly denied
   it, and reintroduce the split-brain this decision exists to prevent.

**What is measured here vs. what is only specified.** `set_valid_to` in
[vector_tier.py](vector_tier.py) models the propagation, and the `vec_derived_valid_to` scenario
measures that a propagated bound filters future recall while leaving past recall intact — that
much is scored against both the reference and real SQL. Everything else in this section is a
**stated model, not a validated one**: the *failure* path (drift, partial write, re-sync
convergence) is unmeasured, and the re-sync statement above is illustrative with an unspecified
join key. Case 1's transactional co-write is likewise asserted rather than exercised — the
prototype never writes both tiers in one transaction.

This is the largest unmeasured area in this folder. It belongs to whoever owns the consolidation
write (SM-X11 / the consolidation-loop tasks), not to this spec, and is listed as a downstream
must-measure in §12.1(c).

---

## 10. Couplings & handoff (what each downstream task consumes)

- **SM-G4 (bi-temporal edges):** the §5 field list + `BiTemporalEdge` model + [schema.sql](schema.sql)
  DDL + [graph_schema.cypher](graph_schema.cypher). Program against the `BiTemporalEdgeStore`
  abstraction ([store.py](store.py)); pick a backend adapter by config.
- **SM-G5 (invalidation):** the §6 rule (including **R1-TIE**) + `upsert_edge`/`invalidate_edge`/
  `retract_edge` (sync+async) and SQL `bt_upsert_edge()`/`bt_retract_edge()` — note
  `bt_upsert_edge` takes an injectable `p_created_at`, required for backfill and 5b.
- **SM-X11 (persistence/migration):** the §5 table + these **additive** migrations (nullable →
  backward compatible; core owned by the rebrand teammate, so applied by SM-X11, not here):
  ```sql
  ALTER TABLE memories ADD COLUMN IF NOT EXISTS valid_from BIGINT;  -- epoch s; NULL = unknown
  ALTER TABLE memories ADD COLUMN IF NOT EXISTS valid_to   BIGINT;  -- epoch s; NULL = open
  ```
  and the new `memory_edge` table (§5) for the graph-lite/relational tier. **Ship the §6.1
  `memory_edge_append_only` trigger with the DDL** — it is part of the model, not a lint. When
  applying the 5c filter to `memories`, use the COALESCE'd predicate from §7; the bare form drops
  every pre-migration row.
- **Quality metric** (for downstream regression): point-in-time reconstruction accuracy over the
  scenario set — target **1.00** for the bi-temporal implementation. A conformance suite should
  also assert (i) at least one probe before the earliest `t_valid` (§7 pitfall), (ii) the R1-TIE
  case, and (iii) that an in-place `UPDATE` of `t_valid`/`t_invalid` is **rejected** (§6.1).

---

## 11. Out of scope & boundaries

- **Graph engine / vector-store choice** → SM-R2 (#77) / SM-G*. **SM-R2 is still OPEN and has not
  selected an engine**, so this spec's use of Neo4j is a *proof that the model is engine-agnostic*,
  not an engine recommendation — the four-timestamp model round-trips losslessly through both
  Postgres `timestamptz` and Neo4j `datetime`, and the §7 predicates are given in both dialects
  precisely so neither is load-bearing. Nothing here pre-empts SM-R2. (Stated the same way in
  SM-R9's `SPIKE.md`, whose PPR/GDS route is likewise conditional on SM-R2 picking a GDS-capable
  engine.)
- **Retrieval fusion / ranking / decay** → SM-R3 (vector), SM-R5 (decay). This spec only adds the
  valid-time *filter* columns, not ranking. **This exclusion is why PPR ranking quality over the
  time-filtered projection is not owned here** — see §12.1(b).
- **Consolidation / extraction scheduling** (async vs inline) → the consolidation-loop tasks.
- **Scale / latency targets** → SM-R1 (**provisional here, not ratified**). Neo4j per-op latency
  in the prototype (~0.7–4.7 s for the whole set) is a naive-session artifact, not a tuned number.
  The scenario set is likewise small and illustrative; its **scale is provisional pending SM-R1**.
- **Right-to-erasure / tenant data deletion** → not specified here; the model is
  invalidate-not-delete throughout (§12.1(a)).

---

## 12. Consistency with dependencies & siblings

- **SM-R1 (#76, ADR):** not yet ratified — provisional targets (scale, latency) flagged for it.
- **Sibling decisions:** consistent with the neuro-symbolic hybrid (vector + temporal KG both
  first-class) locked in the research doc; the vector tier here matches SM-R3's pgvector direction
  (adds only filter columns); the graph bi-temporal model matches the research doc's Graphiti
  reference. No sibling's decision is pre-empted.

### 12.1 Edge lifecycle across siblings (who may remove an edge, and when)

This spec governs when an edge stops being *true* (`t_invalid`) and when it stops being *believed*
(`expired_at`). Neither is deletion — under SCD-2 nothing is ever removed. But two siblings also
act on edge/row lifetime, so the boundary is stated here explicitly rather than left to collide.

**(a) SM-R5 (decay) — decay never hard-deletes; it archives/tiers.**

SM-R5 already resolved this on its own side. Quoting its
`benchmarks/memory_decay/DECISION.md` §6 verbatim:

> decay-pruning **must run after** the consolidation/reflection pass (SM-R7), never before —
> otherwise an episodic row can be pruned before its knowledge is consolidated into the semantic
> tier. And decay-pruning must **archive/tier rather than hard-delete** episodic rows, to stay
> consistent with the bi-temporal, invalidate-not-delete model (D8): set an archived/expired
> marker; do not destroy the row. Hard `DELETE` stays a consolidation-owned operation for
> conflicts.

This spec adopts that rule unchanged, and adds only what is specific to the graph tier:

- **Decay has no authority over bi-temporal graph edges at all.** SM-R5 is a *ranking* model over
  the episodic/vector tier; its prune gate acts on episodic rows. A `memory_edge` row is never
  removed by decay — the §6.1 append-only trigger makes that structurally impossible.
- **"Archive/tier rather than hard-delete" maps onto this model as `expired_at`** (R2 retract):
  the archived/expired marker SM-R5 calls for *is* the transaction-axis end. An archived row
  therefore drops out of 5a but stays reconstructable through 5b — which is precisely the
  consistency SM-R5 §6 is asking for.
- **The one sanctioned hard `DELETE`** is the consolidation-owned conflict/explicit-forget path
  SM-R5 reserves. For a bi-temporal edge it should still be expressed as R2 retract wherever the
  requirement is "stop believing this"; genuine row destruction is reserved for
  right-to-erasure-style deletion, which is a *tenant data* operation and out of scope for both
  specs. Flagged for SM-R1 to confirm whether erasure needs its own path.

Net: no conflict between SM-R5 and this spec, and the two documents now state the same rule.

**(b) SM-R9 (PPR over the time-filtered projection) — mechanism agreed, ranking quality unowned.**

PPR and bi-temporal filtering compose **mechanically**: bi-temporal validity is an **edge filter**
and PPR is a **ranking over whatever subgraph survives that filter** — adding the time predicate
is a projection filter, not a change to the PPR step. Any such projection must use the full 5a
predicate from §7, including `coalesce(e.t_invalid, ∞)` and `e.expired_at IS NULL`: dropping the
first removes every open-ended (currently-true) edge from the projection, and dropping the second
readmits superseded and retracted rows. Both change graph **topology**, which is what PPR ranks
over, so both are correctness bugs rather than cosmetic ones — see the §7 pitfall callout.

**Ranking quality over a time-filtered projection is untested, and is currently nobody's.** A
bi-temporal projection is sparser and differently-shaped than a static corpus, PPR's stationary
distribution is topology-sensitive, and degradation would be worst where edge churn is highest —
our episodic memory. **§11 of this spec explicitly excludes ranking from its scope**, so SM-R10
does not own that measurement. SM-R9's spike recommends **SM-H4 as owner, with SM-G supplying the
temporal graph**, and records that the reassignment still **needs confirming**. This spec states
it identically and does not claim the item as resolved.

**(c) Downstream must-measures** (enabled by this spec; owned elsewhere):

| Composition | Owner | Status |
|---|---|---|
| PPR ranking quality over the 5a-filtered projection | SM-H4 recommended, with SM-G supplying the graph — **needs confirming** | untested, unowned |
| Consolidation ordering (decay-prune strictly after SM-R7) | SM-R5 / consolidation-loop tasks | rule fixed in SM-R5 §6; end-to-end ordering unmeasured |
| Vector-tier `valid_to` drift + re-sync convergence (§9.1) | SM-X11 / consolidation-loop tasks | model stated here; failure path unmeasured, and the re-sync statement is illustrative — its edge→memory join key is **not specified by this spec** and must be decided first |

---

## 13. Grounding & reproducibility

- **Grounded in** `MEMORY-RESEARCH-episodic-and-neurosymbolic.md` §"Bi-temporal modeling"
  (Graphiti's 4 timestamps per edge; contradiction invalidates rather than deletes; point-in-time
  = valid at T).
- **Reproduce:** see [README.md](README.md). Start the two containers (unique names/ports), then
  `PYTHONPATH=libs/agno .venvs/bitemporal/bin/python -m benchmarks.memory_bitemporal.run`. Results
  land in [results/](results/); latest committed run in [TEST_LOG.md](TEST_LOG.md).
- **Backends that need no services:** the in-memory reference, the single-time baseline, the 5c
  vector-tier filter, and the extractor. Everything else is skipped with a printed note when its
  server is unreachable, and the summary records `native_paths_measured` so a run that could not
  exercise the SQL/Cypher artifacts cannot be mistaken for one that did.
- **Core untouched:** `git diff origin/main -- libs/` is empty (this task is decision/spec only).
