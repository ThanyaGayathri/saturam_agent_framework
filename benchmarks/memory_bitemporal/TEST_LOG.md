# TEST_LOG — memory_bitemporal (SM-R10)

## run.py — bi-temporal scenario benchmark (all backends)

**Status:** PASS

**Command:** `PYTHONPATH=libs/agno .venvs/bitemporal/bin/python -m benchmarks.memory_bitemporal.run`

**Environment:** dedicated venv `.venvs/bitemporal` (psycopg 3.2.13, neo4j 5.28.4); Docker 29.5.3;
Postgres 16 container `smr10-bitemporal-pg` (host port 5533); Neo4j 5 container
`smr10-bitemporal-neo4j` (host ports 7688 bolt / 7475 http). Reference + extraction need no services.

**Description:** 9 scenarios / 21 point-in-time probes with timeline-derived ground truth
(insert, supersede, NOOP, three-way chain, back-dated "since Jan", error retraction, predicate &
tenant isolation, belief-evolution). Scored as point-in-time reconstruction accuracy against the
in-memory reference (Option A), the single-time baseline (Option B), and real Postgres + Neo4j.

**Result (run `20260723T162455Z`, stable across re-runs):**

| Backend | Accuracy | Passed/Total |
|---|---|---|
| reference (Option A) | 100.0% | 21/21 |
| postgres (real SQL) | 100.0% | 21/21 |
| neo4j (real Cypher) | 100.0% | 21/21 |
| single_time (Option B) | 57.1% | 12/21 |

All three bi-temporal backends agree at 100%; the single-time baseline fails the 9 point-in-time /
correction / belief-evolution probes — the measured justification for bi-temporal. Committed
output: [results/results_20260723T162455Z.json](results/results_20260723T162455Z.json),
[results/summary_20260723T162455Z.md](results/summary_20260723T162455Z.md).

---

## SQL-native path — `bt_upsert_edge()` / 5a query

**Status:** PASS

**Description:** Drove the SQL-native function directly (the path SM-X11 consumes) with the
NYC→LA→SF chain + a genuine NOOP, then queried point-in-time via the real 5a SQL predicate.

**Result:** outcomes `insert / supersede / supersede / noop`; point-in-time Feb→NYC, Jul→LA,
Dec→SF — all correct.

---

## extraction.py — rule-based valid-time extraction

**Status:** PASS

**Description:** `RuleBasedValidTimeExtractor` over `EXTRACTION_CASES` (fixed ingest reference
2024-07-15): "since Jan", "since March 2022", "in 2019", "from Jun until Sep" (dual bound), ISO
date, and a no-cue fallback.

**Result:** 6/6 exact matches on `(t_valid, t_invalid)`; async variant confirmed.

---

## Reference-adapter smoke (no services)

**Status:** PASS

**Description:** insert → supersede → NOOP outcomes; point-in-time (Feb→NYC, Jul→LA);
invalidate-not-delete (superseded row retained, bounded, current belief); 5b bi-temporal;
async variant.

**Result:** all assertions passed.

---

## Format / validate gates

**Status:** PASS

- **My folder** held to the repo's exact linters (ruff 0.15.20, line-length 120; mypy):
  `ruff format --check` clean, `ruff check` clean, `mypy` "Success: no issues found in 12 source
  files" — verified with both a standalone venv and the repo's `.venv`.
- **`./scripts/validate.sh`:** PASS — agno + egptctl + cookbook ruff + mypy all green (validate.sh's
  scope is libs/ + cookbook; it does not include `benchmarks/`, hence the direct lint above).
- **`./scripts/format.sh`:** runs clean, but it auto-reformats 6 pre-existing-drift files in core
  (`libs/agno/tests/*`, `libs/agno_infra/egpt/docker/*`) that are unrelated to this task. Those
  edits were **reverted** so core stays byte-identical to `origin/main` (`git diff origin/main --
  libs/` empty) — pre-existing drift is documented, not papered over. `format.sh` does not touch
  `benchmarks/`.

## Known gaps / notes

- Neo4j per-op latency (~0.7–4.7 s for the whole set) is a naive-session artifact, not a tuned
  number; latency is not this task's metric (deferred to SM-R1).
- Scenario set is small and single-authored, but ground truth is mechanically timeline-derived
  (objective, not subjective), and cross-checked by three independent backend implementations
  agreeing exactly — mitigating single-author circularity. Scale is provisional for SM-R1.
- Core untouched: `git diff origin/main -- libs/` empty.
