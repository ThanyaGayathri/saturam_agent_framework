# TEST_LOG — memory_bitemporal (SM-R10)

## Measurement status — what is and is not validated

The invalidation rule and the query semantics live **once**, in [store.py](store.py). The
reference / Postgres / Neo4j adapters implement storage primitives only and inherit that rule.
Those three agreeing is therefore **one shared rule over three storage layers** — not three
independent derivations of the semantics. The wording "three independent backends agree" has been
removed from this folder for that reason.

The artifacts SM-G4/G5/X11 actually ship — SQL-native `bt_upsert_edge()` and the 5a/5b SQL/Cypher
predicates — are now driven end-to-end by dedicated native adapters
([sql_native_store.py](adapters/sql_native_store.py),
[cypher_native_store.py](adapters/cypher_native_store.py)) and scored against the same probes as
an equivalence test, with `run.py` comparing **per-probe return values** against the reference and
recording `native_paths_measured` in the results JSON.

**Every path the review asked about is now measured** against live Postgres 16 and Neo4j 5
(run `20260729T061116Z`). Two §9.1 items are **not** — they are listed in the same table rather
than in a footnote, because a status table that only lists successes is how unvalidated work gets
mistaken for validated work:

| Path | Status |
|---|---|
| Python rule, in-memory reference | **measured** — 30/30 |
| Python rule over real Postgres storage | **measured** — 30/30, and 30/30 probes identical to reference |
| Python rule over real Neo4j storage | **measured** — 30/30, and 30/30 probes identical to reference |
| **SQL-native `bt_upsert_edge()` + 5a/5b SQL predicates** | **measured — 30/30, equivalent to the Python reference on every probe** |
| **Cypher-native R1/R2 + 5a/5b Cypher predicates** | **measured — 30/30, equivalent to the Python reference on every probe** |
| 5c vector-tier filter | **measured** — 10/10 on reference *and* on real SQL |
| Append-only guardrail (SPEC §6.1 trigger) | **measured** — 8/8 |
| Rule-based valid-time extraction | **measured** — 6/6 |
| **§9.1 vector-tier consistency — failure path** | **NOT MEASURED.** Only the propagation is scored (`vec_derived_valid_to`). Drift, partial write, re-sync convergence and the case-1 transactional co-write are **stated models, not validated ones**. |
| **§9.1 re-sync statement** | **NOT EXECUTED, and not runnable as written** — its `m.memory_id = e.edge_id` join needs an edge→memory provenance link this spec does not define. Blocking unknown for SM-X11. |

So the equivalence question raised in review is settled: the artifacts SM-G4/G5/X11 actually ship
return **exactly** what the Python reference returns, on all 30 probes, on both engines. That is a
stronger result than matching aggregate accuracy — the comparison is per-probe, so any divergence
would have been named rather than averaged away.

What this still does **not** claim: that the semantics were derived three times independently.
They were derived once and are now *confirmed* to be faithfully re-expressed in SQL and in Cypher.
That is the useful property for downstream (the shipped artifacts are trustworthy), and it is what
the numbers now support.

---

## run.py — bi-temporal scenario benchmark

**Status:** PASS (all six backends)

**Command:** `PYTHONPATH=libs/agno .venvs/bitemporal/bin/python -m benchmarks.memory_bitemporal.run`

**Environment:** venv `.venvs/bitemporal` (psycopg 3.2.13, neo4j 5.28.4); Python 3.9; Docker
29.6.2; Postgres 16 container `smr10-bitemporal-pg` (host port 5533); Neo4j 5 container
`smr10-bitemporal-neo4j` (host ports 7688 bolt / 7475 http).

**Description:** 11 scenarios / 30 point-in-time probes with timeline-derived ground truth
(insert, supersede, NOOP, three-way chain, back-dated "since Jan", error retraction, predicate &
tenant isolation, belief-evolution, **same-`t_valid` tie-break**, **deep re-correction**).

**Result (run `20260729T061116Z`):**

| Backend | What it exercises | Accuracy | Equivalence vs reference |
|---|---|---|---|
| reference (Option A) | Python rule, in-memory | 100.0% (30/30) | baseline |
| postgres | Python rule over real SQL storage | 100.0% (30/30) | **30/30 identical** |
| neo4j | Python rule over real Cypher storage | 100.0% (30/30) | **30/30 identical** |
| **postgres_sql** | **`bt_upsert_edge()` + 5a/5b SQL predicates** | **100.0% (30/30)** | **30/30 identical** |
| **neo4j_cypher** | **graph_schema R1/R2 + 5a/5b Cypher predicates** | **100.0% (30/30)** | **30/30 identical** |
| single_time (Option B) | destructive overwrite baseline | 56.7% (17/30) | 17/30 (diverges, as designed) |

The single-time baseline fails 13/30 — the point-in-time, correction and belief-evolution probes.
Committed output: [results/results_20260729T061116Z.json](results/results_20260729T061116Z.json),
[results/summary_20260729T061116Z.md](results/summary_20260729T061116Z.md).

Per-op latency, whole 30-probe set: postgres ~31–39 ms, neo4j ~213–270 ms (naive session per
operation, not a tuned number; latency is not this task's metric — deferred to SM-R1).

**Superseded run:** [results/results_20260723T162455Z.json](results/results_20260723T162455Z.json)
(9 scenarios / 21 probes, before the three new scenarios and the native adapters). Kept for the
trail; its numbers do not describe the current probe set.

---

## New coverage added this round

### same_t_valid_tiebreak — simultaneous conflicting asserts

**Status:** PASS (4/4, reference)

**Description:** NYC and LA both asserted valid-from Jun, written at Jun and Jul. Exercises the
previously-undefined tie-break, now specified as SPEC §6 **R1-TIE** (last writer wins on the
transaction axis; loser retained with a zero-length valid interval `[Jun, Jun)`).

**Result:** 5a at Jun and Dec → LA; 5b `t=Jul, tt=Jun` → NYC (loser auditable before the
tie-break); 5b `t=Jun, tt=Sep` → LA. Behaviour was already deterministic; it is now specified and
covered rather than incidental.

### deep_recorrection — correcting an already-superseded belief

**Status:** PASS (5/5, reference)

**Description:** NYC→LA, then SF corrects the *middle* of the already-bounded NYC window — a
second-level SCD-2 version. World-time March therefore has three different answers depending on
transaction time.

**Result:** 5a Feb→NYC, Mar→SF, Jul→LA; 5b `t=Mar, tt=Jun`→NYC and `t=Mar, tt=Sep`→SF. Multi-level
belief evolution reconstructs correctly.

### 5c vector/episodic-tier filter — SPEC §9

**Status:** PASS (10/10 ratified predicate) — **and found a real defect in the previous predicate**

**Description:** 4 scenarios / 10 filter probes over [vector_tier.py](vector_tier.py): open
interval, `valid_to` propagated from a graph invalidation, tenant isolation, and pre-migration
rows with `valid_from IS NULL`. §9 was previously a decision with no probe behind it.

**Result:** ratified predicate **10/10 on the in-memory reference and 10/10 on real Postgres** —
so this is measured against actual SQL `COALESCE` / `NULL` semantics, not just a Python model of
them. The predicate SPEC §7 previously specified — `valid_from <= :T` with no `COALESCE` on the
lower bound — scores **7/10 on both**, failing all three `vec_legacy_null_valid_from` probes
identically in Python and in SQL:

| Probe | Expected | Bare predicate returned |
|---|---|---|
| `u1@Dec-2023` | `[m_legacy]` | `[]` |
| `u1@Feb` | `[m_legacy, m_nyc]` | `[m_nyc]` |
| `u1@Dec` | `[m_legacy, m_nyc]` | `[m_nyc]` |

Because the §9 migration is additive, **every row written before it has `valid_from IS NULL`**, so
the bare predicate would have silently dropped the entire pre-migration corpus from recall on the
day the filter shipped — no error, no failing query. Both bounds are now `COALESCE`'d in SPEC §7
and [schema.sql](schema.sql), and the bare form is retained as a **scored counterexample** so the
regression cannot come back unnoticed.

### Append-only guardrail — SPEC §6.1

**Status:** PASS (8/8, real Postgres)

**Description:** §6.1 requires that expire-and-reinsert is the *only* mutation path, enforced by
the `memory_edge_append_only` trigger. A documented-but-unenforced rule is precisely the failure
mode this spec exists to avoid, so [guardrail.py](guardrail.py) scores the trigger instead of
asserting it. Each case runs in its own transaction — a rejected statement aborts its transaction,
so sharing one would mask every case after the first.

| Case | Required | Result |
|---|---|---|
| in-place `t_invalid` rewrite | rejected | rejected |
| in-place `t_valid` rewrite | rejected | rejected |
| in-place `created_at` rewrite | rejected | rejected |
| in-place `object_id` rewrite | rejected | rejected |
| `ON CONFLICT DO UPDATE SET <every column>` | rejected | rejected |
| `expired_at` second write (write-once) | rejected | rejected |
| legitimate expire (`expired_at` on an open row) | allowed | allowed |
| payload column (`fact`) update | allowed | allowed |

The `ON CONFLICT DO UPDATE SET <every column>` case is the exact shape a sibling storage layer
shipped. That the legitimate-expire case still passes is what makes the trigger safe to mandate —
and it is independently confirmed by `postgres` and `postgres_sql` both scoring 30/30, since the
whole SCD-2 flow depends on that one permitted `UPDATE`.

---

## Defects found and fixed in the prototype itself

### `bt_upsert_edge()` hardcoded `now()` as the transaction time

Backfill, replay and every 5b reconstruction require asserting a fact whose transaction time is
not the wall clock ("learned in July that NYC held since January"). With `now()` hardcoded, those
scenarios were **inexpressible through the SQL path**, which is part of why that path had never
been scored. Fixed: `p_created_at TIMESTAMPTZ DEFAULT now()` (backward-compatible), threaded
through the expire, the reinserted bounded version, and the new row. The prior 7-argument
signature is explicitly dropped so replacing it cannot leave an ambiguous overload.

### Neo4j backend crashed instead of skipping when unreachable

`GraphDatabase.driver()` connects lazily, so `run.py`'s reachability probe succeeded with no
server running and the benchmark then died mid-run with `ServiceUnavailable` — contradicting the
documented "skips pg/neo4j automatically if unreachable". Fixed with `verify_connectivity()` in
`Neo4jEdgeStore.__init__`. This only stayed hidden because previous runs always had the containers
up.

### `LLMValidTimeExtractor` failed mypy under the repo's pinned toolchain

`self._agent = None` inferred type `None` and was then assigned an `Agent`, and
`self._model or OpenAIResponses(...)` widened to `object` against `Agent`'s `model` parameter — 2
errors under mypy 1.19.1. Pre-existing (that file is otherwise untouched by this change set) and
fixed with two annotations, so the folder's mypy gate is genuinely clean rather than clean-by-
omission.

### Equivalence probe keys collided across tenants

The per-probe key omitted `user_id`, so `tenant_isolation`'s two probes (same subject, predicate
and time, different tenant) collapsed onto one key and one probe silently vanished from the
comparison — 30 probes, 29 keys. Fixed; the equivalence report now covers all 30.

---

## extraction.py — rule-based valid-time extraction

**Status:** PASS

**Description:** `RuleBasedValidTimeExtractor` over `EXTRACTION_CASES` (fixed ingest reference
2024-07-15): "since Jan", "since March 2022", "in 2019", "from Jun until Sep" (dual bound), ISO
date, and a no-cue fallback.

**Result:** 6/6 exact matches on `(t_valid, t_invalid)`; async variant confirmed.

---

## Format / validate gates

**Status:** PASS

- **My folder** held to the repo's linters (ruff, line-length 120; mypy): `ruff format --check`
  clean, `ruff check` clean, `mypy` clean.
- **`./scripts/validate.sh`:** PASS (its scope is libs/ + cookbook and does not include
  `benchmarks/`, hence the direct lint above).
- **`./scripts/format.sh`:** does not touch `benchmarks/`. It auto-reformats 6 pre-existing-drift
  files in core unrelated to this task; those edits are reverted so core stays byte-identical to
  `origin/main`.

## Known gaps / notes

- The SQL-native / Cypher-native equivalence — the headline open item from review — is now
  **closed**: 30/30 identical on both engines. See the status table at the top.
- The vector-tier consistency model (SPEC §9.1) is the largest unmeasured area in this folder.
  Only the propagation itself is scored (`vec_derived_valid_to`, on reference and real SQL).
  **Not measured:** the failure path (drift, partial write, re-sync convergence) and the
  transactional co-write. The §9.1 re-sync statement is explicitly marked **illustrative, not
  executed** — its `m.memory_id = e.edge_id` join assumes an edge→memory provenance link that
  this spec does not define, and which may need a link table rather than a column (one edge can
  yield several memories, one memory can cite several edges). SM-X11 must settle that before the
  statement means anything. Owned by whoever owns the consolidation write.
- PPR ranking quality over the 5a-filtered projection is untested and currently unowned
  (SPEC §12.1(b)) — SM-R9's spike recommends SM-H4, pending confirmation.
- Neo4j per-op latency is a naive-session artifact, not a tuned number; latency is not this task's
  metric (deferred to SM-R1).
- Scenario set is small and single-authored; ground truth is mechanically timeline-derived
  (objective, not subjective). Scale is provisional for SM-R1.
- **Engine asymmetry (actionable, not just noted).** The guardrail is Postgres-only: Neo4j 5 has
  no property-immutability constraint, so the trigger has no native counterpart. Demonstrated
  rather than assumed — injecting `SET e.t_valid = e.t_invalid + duration({days:30})` on a bounded
  edge was **accepted silently by Neo4j**, while Postgres rejects the identical mutation
  (`guardrail.py` case `in_place_t_valid`). SPEC §6.1 now names two concrete options for SM-G4 —
  (1) an APOC trigger aborting on writes to `t_valid`/`t_invalid`/`created_at`, or (2) a
  write-through repository plus a scheduled invariant audit — rather than just recording that a
  gap exists. The §6.1 audit query is **verified**: zero rows on a healthy store, and it reported
  the injected edge as `inverted interval`. The APOC option remains unverified.
- Core untouched: `git diff origin/main -- libs/` empty.
