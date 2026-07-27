# TEST_LOG — memory eval harness (SM-R13)

Environment: offline (stdlib-only harness; no egpt/LLM). Python 3.13 host `python3`.
Lint/type: dedicated venv with ruff 0.16.0 + mypy 1.19.1.

---

### tests/test_metrics.py

**Status:** PASS (6/6)

**Description:** Unit tests for the metric engine on hand-computed values — recall@k / hit@k,
nDCG@k (DCG/IDCG spelled out), percentile/p95, Wilson interval, StubJudge, and an end-to-end
`aggregate` check (per-category QA, temporal slice, retrieval means, bi-temporal leak, decay).

**Result:** `6 metric tests passed`. Formulas cross-checked in-test against independent closed-form
expressions (not hardcoded constants).

---

### tests/test_runner.py

**Status:** PASS (7/7)

**Description:** End-to-end runner + provider protocol: report has all sections; both baselines
conform to the `MemoryProvider` protocol (runtime_checkable); no-evidence probes excluded from
recall (`n==2` on longmemeval); time-aware provider ≥ naive on update-heavy sets; recency respects
`as_of` (leak 0.0); the `consolidate()` seam runs end-to-end (`consolidate=True`); async == sync.

**Result:** `7 runner tests passed`.

---

### tests/test_suite_adapter.py

**Status:** SKIP offline / PASS in venv

**Description:** Builds real `egpt.eval.suite.Case`s via `build_memory_cases` and constructs
`LLMJudge` (no LLM call). Skips cleanly when egpt is absent so the offline CI stays green.

**Result:** `2 suite-adapter tests processed (egpt present: False)` — both SKIP offline. Run in the
dev venv to exercise the real seed API.

---

### run.py — offline smoke (all fixtures × both baseline providers)

**Status:** PASS (harness computes end-to-end; metrics discriminate providers)

**Command:** `python run.py --mode fixture --benchmark all --provider all`

**Result:** wrote `results/results_*_fixture_smoke.json` + `summary_*_fixture_smoke.md`:

| benchmark | provider | QA | temporal | recall@5 | nDCG | bitemporal | as_of_leak | decay(supp/outr) |
|---|---|---|---|---|---|---|---|---|
| custom_temporal | lexical | 66.7% | 100% | 100% | 0.833 | 100% | 0.00 | 0.00/0.00 |
| custom_temporal | recency_decay | 100% | 100% | 100% | 1.000 | 100% | 0.00 | 0.00/1.00 |
| dmr | lexical | 50.0% | - | 100% | 0.815 | - | - | - |
| dmr | recency_decay | 100% | - | 100% | 1.000 | - | - | - |
| locomo[0] | lexical | 100% | 100% | 100% | 1.000 | - | - | - |
| locomo[0] | recency_decay | 66.7% | 100% | 100% | 0.833 | - | - | - |
| longmemeval | lexical | 33.3% | 0.0% | 100% | 0.693 | 0.0% | - | - |
| longmemeval | recency_decay | 66.7% | 100% | 100% | 1.000 | 100% | - | - |

**Observations (honest):**
- The metrics **discriminate**: recency_decay beats lexical on the update/temporal sets
  (custom_temporal, longmemeval, dmr), and — importantly — **lexical beats recency on LOCOMO**
  (100% vs 66.7%): recency down-ranks an older supporting turn needed for multi-hop. The harness
  captures a real tradeoff, not a rigged win.
- Retrieval metrics correctly **exclude** no-evidence probes (longmemeval recall over n=2, not 3;
  the abstention probe has no gold evidence).
- `as_of_leak_rate = 0.00` for recency (honors `as_of`); decay `outranked = 1.00` for recency
  (gold outranks the stale turn) vs `0.00` for lexical.
- These are **smoke numbers on tiny fixtures with a stub judge** — they prove executability, NOT
  system quality. Ranking real systems requires `--mode full` on the real corpora + LLM judge.

---

### CI gate demonstration (harness/gate.py)

**Status:** PASS (gate is a real regression guard)

- Reference provider run (`--provider recency_decay`, what on-PR CI runs): **GATE: PASS**.
- Naive `lexical` on longmemeval: **GATE FAIL** — `QA 0.333 < min 0.50`. The gate can fail a build.

---

### Lint / type / format (dedicated venv)

**Status:** PASS

- `ruff check --select E,F,W,I --line-length 120 --target-version py39` → **All checks passed!**
- `ruff format --check` → **28 files already formatted**.
- `mypy --explicit-package-bases --ignore-missing-imports --python-version 3.9 harness run.py adapters`
  → **Success: no issues found in 20 source files**.

Note: the repo's `scripts/validate.sh` / `format.sh` only target `libs/agno`, `libs/egptctl`,
`cookbook` — **not `benchmarks/`** — and require the full dev venv (`uv` + editable egpt), which is
not installable in this environment (`uv` absent). They are therefore unaffected by this change;
`git diff origin/main -- libs/ cookbook/ scripts/` is empty (core untouched), so they pass iff they
passed on origin/main. The lint/type results above are the direct self-check on the deliverable.

---

### Core-untouched proof

`git diff origin/main -- libs/` → empty. All work confined to `benchmarks/memory_eval/`.

---

## Known gaps (carried forward, not papered over)

- Research doc `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` not accessible (symlink absent);
  grounding leans on public sources + the SM-R7 harness (`findings/01`).
- DMR raw schema varies by release — `dmr.build` is lenient and raises loudly; verify on the real dump.
- Full LLM-judge path (`LLMJudge`, `--judge llm`) is wired against the real seed API but not executed
  here (offline, no credentials). Exercise it in the dev venv once gpt-5.4 creds are in `.env`.
- Custom temporal/decay set is single-authored (data-quality caveat, `findings/01`).
