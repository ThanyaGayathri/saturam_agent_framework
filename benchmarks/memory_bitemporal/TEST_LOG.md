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

| Path | Status |
|---|---|
| Python rule, in-memory reference | **measured** — 30/30 |
| 5c vector-tier filter (reference) | **measured** — 10/10 |
| Rule-based valid-time extraction | **measured** — 6/6 |
| Python rule over real Postgres / Neo4j storage | measured 2026-07-23 on the older 9-scenario set (21/21); **not re-run** on the 11-scenario set |
| **SQL-native `bt_upsert_edge()` + 5a/5b SQL predicates** | **NOT MEASURED** — harness written and wired, no container runtime available |
| **Cypher-native R1/R2 + 5a/5b Cypher predicates** | **NOT MEASURED** — harness written and wired, no container runtime available |

**Why the native paths are unmeasured:** this session had no working container runtime — Docker
Desktop is installed but its Linux VM fails to start (`docker ps` → "Docker Desktop is unable to
start"; the engine never answers `_ping`, HTTP 500 after ~9.5 min, across a clean restart). No
alternative Postgres was available (no Homebrew, no native server), and `bt_upsert_edge()` is
PL/pgSQL, so it cannot be exercised without a real Postgres. Re-implementing the SQL rule in
Python to "test" it would just be a third statement of the same rule — the exact circularity this
change set exists to remove — so it was not done. **The equivalence claim is therefore stated as
pending, not as passing.** Anyone with a working Docker can settle it by running the command
below and checking the `Equivalence vs reference` block.

---

## run.py — bi-temporal scenario benchmark

**Status:** PASS (service-free backends), NOT RUN (Postgres / Neo4j / native paths)

**Command:** `PYTHONPATH=libs/agno .venvs/bitemporal/bin/python -m benchmarks.memory_bitemporal.run`

**Environment:** venv `.venvs/bitemporal` (psycopg 3.2.13, neo4j 5.28.4 — installed and
import-verified); Python 3.9. **No container runtime**, so Postgres/Neo4j backends were skipped
with a printed note rather than run.

**Description:** 11 scenarios / 30 point-in-time probes with timeline-derived ground truth
(insert, supersede, NOOP, three-way chain, back-dated "since Jan", error retraction, predicate &
tenant isolation, belief-evolution, **same-`t_valid` tie-break**, **deep re-correction**).

**Result (run `20260728T135659Z`):**

| Backend | Accuracy | Passed/Total |
|---|---|---|
| reference (Option A) | 100.0% | 30/30 |
| single_time (Option B) | 56.7% | 17/30 |
| postgres / neo4j / postgres_sql / neo4j_cypher | — | skipped (unreachable) |

The single-time baseline fails 13/30 — the point-in-time, correction and belief-evolution probes.
Committed output: [results/results_20260728T135659Z.json](results/results_20260728T135659Z.json),
[results/summary_20260728T135659Z.md](results/summary_20260728T135659Z.md).

**Superseded run:** [results/results_20260723T162455Z.json](results/results_20260723T162455Z.json)
(9 scenarios / 21 probes; reference + real Postgres + real Neo4j all 21/21, single-time 57.1%).
Kept as the record that the model round-trips losslessly through `timestamptz` and Neo4j
`datetime`. It predates the three new scenarios and the native adapters, so its numbers do not
describe the current probe set.

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

**Result:** ratified predicate **10/10**. The predicate SPEC §7 previously specified —
`valid_from <= :T` with no `COALESCE` on the lower bound — scores **7/10**, failing all three
`vec_legacy_null_valid_from` probes:

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

- **The SQL-native and Cypher-native equivalence is the headline open item** — written, wired,
  unmeasured. See the status table at the top.
- The vector-tier consistency model (SPEC §9.1) specifies transactional co-write, advisory-filter
  and idempotent re-sync. The **failure path** (drift, partial write, re-sync convergence) is
  stated but not measured; it belongs to whoever owns the consolidation write.
- PPR ranking quality over the 5a-filtered projection is untested and currently unowned
  (SPEC §12.1(b)) — SM-R9's spike recommends SM-H4, pending confirmation.
- Neo4j per-op latency (~0.7–4.7 s for the whole set, 2026-07-23 run) is a naive-session artifact,
  not a tuned number; latency is not this task's metric (deferred to SM-R1).
- Scenario set is small and single-authored; ground truth is mechanically timeline-derived
  (objective, not subjective). Scale is provisional for SM-R1.
- Core untouched: `git diff origin/main -- libs/` empty.
