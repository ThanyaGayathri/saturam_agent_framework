# TEST_LOG — memory eval harness (SM-R13)

Environment: offline (stdlib-only harness; no egpt/LLM). Python 3.13 host `python3`.
Lint/type: dedicated venv with ruff 0.16.0 + mypy 1.19.1.

---

## Pre-review revision — sibling-consistency fixes (Part 2 audit)

Applied against the CURRENT sibling docs (re-read, not from memory):

| # | Fix | Evidence |
|---|---|---|
| P2.1 | SM-R11 signature mismatch — mapping table rewritten to `read(Scope, MemoryQuery)` / `write(Scope, events)` / `consolidate(Scope, tiers)`; colliding `consolidate(scope: str)` param renamed to `tiers`. | `harness/provider.py` docstring + `MemoryProvider.consolidate`; `harness/baselines.py:_BaseLexical.consolidate`. Verified vs `origin/feat/memory-SM-R11:.../interface.py` (SIGNATURE NOTE names this PR). |
| P2.2 | Consolidation quality — claim downgraded to "seam wired, intrinsic score not run here (needs SM-R7 labeled targets)". No false checkbox. | `PLAN.md §2.4` + AC1 row; `harness/runner.py:60` fires the seam (unscored no-op for baselines). |
| P2.3 | Bi-temporal leak made **two-sided** (`t_valid <= T < t_invalid`, SM-R10). Added `Turn.t_valid`/`t_invalid` + `valid_at`; leak now catches future AND stale-superseded, over all `as_of` probes. | `harness/schemas.py:Turn`; `harness/metrics.py` (`as_of_leak_rate`); `tests/test_metrics.py:test_bitemporal_leak_is_two_sided` (2/3, incl. the stale case a one-sided check misses). |
| P2.4 | Latency gate `1500 ms → 50 ms` (SM-R8 hot-path `p95 < ~50 ms`, retrieval read only); mis-citation of "SM-R3 family" corrected. | `harness/gate.py:GateConfig.max_query_p95_ms`. Reference-provider gate still PASS. |
| P2.5 | Decay clock — loud NON-PRODUCTION comment: ages from creation, NOT SM-R5's `now - last_accessed_at` (reset on access). | `harness/baselines.py:RecencyDecayProvider` docstring + `_score`. |
| P2.6 | Embedder gap flagged (both baselines lexical-only; no embedder). Not fixed — out of scope (embedder decision = SM-R4 #79). | `findings/02-embedder-coverage-gap.md`; `PLAN.md §6`. |

Post-fix verification (offline, re-run): ruff (E,F,W,I) clean; `ruff format --check` clean; mypy clean
(20 files); `7 metric tests`, `7 runner tests`, suite-adapter SKIP (no egpt). Reference-provider gate
PASS at 50 ms. Core untouched: `git diff origin/main -- libs/` = 0.

## Second self-review round (verify-don't-re-trust)

| § | Finding | Resolution |
|---|---|---|
| 1 | **Misattribution** — embedder ownership said "SM-R9". SM-R9 (#84) is the HippoRAG PPR spike; the embedding-model decision is **SM-R4 (#79)** (bge-base / text-embedding-3-large). | Fixed in `findings/02`, `PLAN.md §6`, `README.md`, this log. Bug-history mention of SM-R9/R8/R11 kept (accurate). |
| 2 | **Bi-temporal default** — is `valid_at` defaulting `t_valid→timestamp` intentional (not R10's NULL-drop bug)? | Confirmed intentional; added explicit docstring + `test_valid_at_default_lower_bound` proving a `t_valid=None` turn is evaluated, not dropped. |
| 3 | **Is the SM-R7 downgrade the full story?** Re-read `composite_quality`: all 6 terms are label-gated (no labelless mode). **But** its companion **footprint** is reference-free. | **Wired it** — `MemoryProvider.footprint()` `{items, tokens}`, in every report as `memory_footprint`, unit-tested + shown in results. Label-based `composite_quality` stays deferred. Stronger than a full punt. |
| 4 | **Static-trace of suite wiring** — field-by-field vs `Case`/`run_cases`. All fields match; **found** a gap: `run_cases` judges via `AgentAsJudgeEval(model=case.judge_model or judge_model)` → falls back to `OpenAIChat(gpt-5-mini)` (house-rule violation) if unset, which a construct-only test misses. | Fixed: `build_memory_cases` now pins `case.judge_model = OpenAIResponses` (test asserts it). |
| 5 | Doc sweep — `1500` / `consolidate(scope: str)` / strawman `read(tenant,…)`. | Confirmed: no live residuals; remaining mentions are the corrected R11 signature + the explanatory "was wrong" note only. |

Round-2 verification: ruff + `ruff format --check` clean; mypy clean (20 files); **8 metric + 8 runner
tests** pass; suite-adapter SKIP offline; reference gate PASS at 50 ms; footprint renders (e.g.
`custom_temporal` 3 items / 24 tok); `git diff origin/main -- libs/` = 0.

## Design-review round (paranthaman-commits, APPROVED conditional — 1 major + 3 minor)

| # | Review item | Resolution |
|---|---|---|
| 1 (Major) | No graph/multi-hop dimension (all-hops-hit@k / PPR / graph dataset) — the SM-R9 + SM-R10-SS12.1(c) deferral had no home. | **`all_hops_hit@k`** metric wired + tested (`metrics.py`, `retrieval.all_hops_hit_at_k`), multi-hop probes exercised (LOCOMO 2-hop); graph-traversal/**PPR provider** + bridge dataset + bi-temporal-graph probe **scoped to SM-X7** with named upstream owners (`findings/03`, PLAN §2.6/§6). |
| 2 (Minor) | No cross-tenant isolation probe (a provider-level security check). | Threaded `tenant` (→ R11 `Scope.tenant_id`); added `harness/isolation.py` (write A+B, read each, assert 0 leak), a **gate** (`run.py` fails on any leak), and `test_cross_tenant_isolation`. Both baselines: 0 leak. |
| 3 (Minor) | `as_of_leak_rate` checked top-1 only; a stale fact at rank 2..k escaped. | Now scans the **whole top-k** (leak if ANY top-k turn invalid at T); kept `as_of_top1_leak_rate` + `as_of_first_leak_rank`. New `test_asof_leak_topk_catches_rank2`. (Lexical baselines now show top-1 valid but top-k leak — honest, only a bi-temporal provider → 0.) |
| 4 (Minor) | "Construction-validated, not run end-to-end" should be pinned as THE main caveat. | Added a prominent ⚠️ STATUS CAVEAT block at the top of `PLAN.md` (run_cases unrun, stub judge, composite_quality unrun; SM-X7 must run for real). |

Review-round verification: ruff + `ruff format --check` clean (34 files); mypy clean (21 files, py3.10);
**10 metric + 9 runner tests** pass; suite-adapter SKIP offline; isolation gate = 0 leak both providers;
reference gate PASS. Core untouched: **my commits touch no `libs/` file** (`git log origin/main..HEAD
-- libs/` empty). NOTE: `git diff origin/main -- libs/` is now non-empty only because origin/main
advanced (PR #22 mcp-2.0 merge) after this branch's base — not from this branch.

---

### tests/test_metrics.py

**Status:** PASS (10/10)

**Description:** Unit tests for the metric engine on hand-computed values — recall@k / hit@k,
nDCG@k (DCG/IDCG spelled out), percentile/p95, Wilson interval, StubJudge, an end-to-end
`aggregate` check (per-category QA, temporal slice, retrieval means, bi-temporal leak, decay), and
the **two-sided** bi-temporal leak (future + stale-superseded, SM-R10 `t_valid <= T < t_invalid`).

**Result:** `10 metric tests passed` (added `all_hops_hit@k` + top-k as-of-leak-catches-rank-2 tests;
earlier: the two-sided leak test + a `valid_at` t_valid=None
default test). Formulas cross-checked in-test against independent closed-form
expressions (not hardcoded constants).

---

### tests/test_runner.py

**Status:** PASS (9/9)

**Description:** End-to-end runner + provider protocol: report has all sections; both baselines
conform to the `MemoryProvider` protocol (runtime_checkable); no-evidence probes excluded from
recall (`n==2` on longmemeval); time-aware provider ≥ naive on update-heavy sets; recency respects
`as_of` (leak 0.0); the `consolidate()` seam runs end-to-end (`consolidate=True`); async == sync.

**Result:** `9 runner tests passed` (added `memory_footprint` + `cross_tenant_isolation` tests).

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

---

## SM-X7 (#143) — closing AC1 (suite wiring) and AC3 (CI integration)

Scope for this pass, agreed up front: close the mechanical gaps in AC1/AC3 against the existing
committed fixtures — no real corpus fetch, no live LLM call, no graph/PPR track (that track,
`PLAN.md §2.6`/§6 and `findings/03`, is not green-field: `libs/agno/egpt/memory/graph/` exists
and `integrations/memory/graph/neo4j/neo4j.py` already ships a flag-gated
`multihop_ppr_enabled` PPR path. It's left for a follow-up because ownership against SM-G1/SM-H4
is unresolved, not because the code doesn't exist). AC2's consolidation-quality split (footprint
wired, `composite_quality` deferred to SM-R7 labels) was already correct and is unchanged.

| # | Gap (from the pre-X7 STATUS CAVEAT) | Closed by |
|---|---|---|
| 1 | `build_memory_cases` only *built* `Case`s; nothing called `run_cases`/`arun_cases`, and the retrieval/temporal/decay merge onto the `SuiteResult` existed only as prose in the docstring. | `adapters/egpt_suite.run_memory_suite` / `arun_memory_suite`: call the seed's `run_cases`/`arun_cases`, then join `SuiteResult.results` (keyed by `CaseResult.name` == `Probe.probe_id`) with the retrieved-id map and query latency into `ProbeRecord`s, and run them through the existing `harness.metrics.aggregate` unchanged. `build_memory_cases`/`abuild_memory_cases` now also return per-probe query latency (third tuple element — `test_suite_adapter.py` updated for the new shape). |
| 2 | `ci/memory-eval.yml` was a template inert under `benchmarks/memory_eval/`; nothing in `.github/workflows/` referenced it. | Installed `.github/workflows/memory-eval.yml` (adapted from the template — same two-tier shape, PR-triggered smoke tier now gated on this repo's `run-ci` label per `ci.yml`'s cost-control convention, which the original template predates). The template file is kept as an archived draft with an updated header pointing at the real one. |

### QC review round 3 — dispositions

| # | Finding | Disposition |
|---|---|---|
| 1 | `full` job never installed `uv`, so `dev_setup.sh` hard-exited | **Fixed** — `astral-sh/setup-uv@v6` added before it, matching `ci.yml`; `python-version` corrected to `3.12` to match the venv `dev_setup.sh` actually builds |
| 2 | `full` job's `if: != 'pull_request'` also fired on `push` to `main` | **Fixed** — enumerates `schedule`/`workflow_dispatch`, matching the stated intent |
| 3 | `run_memory_suite` reachable only as a library API; AC1 read as fully closed | **Documented, not wired** — the boundary is now stated in PLAN.md and README; routing a `run.py` mode through it plus a CI job is its own ticket |
| 4 | `RunConfig.consolidate` silently dropped | **Fixed** — honoured on both build paths at the same point `harness/runner.py` uses; pinned by `test_run_config_consolidate_is_honoured_on_both_build_paths` |
| 5 | Fixtures never diverged probe order, so positional-merge regressions were invisible | **Fixed** — `_fake_suite_for(..., order=)` plus `test_results_are_joined_by_probe_id_not_by_position`, asserted through `qa_accuracy_by_category` (overall accuracy cannot see a permutation) |
| 6 | `answer_ms` spans the whole case vs the offline runner's answerer-only span | **Documented, not renamed** — `answer_ms` is a shared `ProbeRecord` field `run_benchmark` also writes, so renaming forks the schema; the divergence is stated at the call site and in README "Latency semantics" |
| 7 | `afootprint()`/`footprint()` swap was invisible | **Fixed** — `test_arun_memory_suite_uses_the_async_footprint` |
| 8 | "24 passed (+3 new)" did not reconcile | **Fixed** — the count above is now measured, not recalled |

Residual carried out of this PR: **#3's wiring half** — a `run.py` mode (or flag) that routes to
`run_memory_suite`, and a CI job exercising it on the offline fixture. Needs a new ticket; not a
review fix.

**Deliberately not done, and why:** a physical move of this tree into `tests/memory/eval/`
(PLAN.md §5's stated home) — that path does not exist in this repo's actual layout and moving
~30 files into `libs/agno/tests/` would break the harness's deliberate stdlib-only/egpt-free core
(`harness/` has zero egpt import today). See PLAN.md §5's recorded deviation for the full
reasoning. AC1/AC3 are closed *in place* instead.

**New tests:** `tests/test_suite_run.py` — `run_memory_suite`/`arun_memory_suite` against a
*mocked* `run_cases`/`arun_cases` (patching `egpt.eval.suite.run_cases`/`arun_cases`): asserts the
merge reads real QA-pass/fail correctly (both an all-pass and an all-fail fixture, so a
join-on-the-wrong-key bug that always reports success cannot pass silently) and that
retrieval/footprint metrics — the actual point of the merge — are populated. Exercising `run_cases`
against a *real* LLM is out of scope for this pass (cost/scope decision); `test_suite_adapter.py`'s
existing real-API construction test plus these mocked-merge tests together cover what can be
proven without spending anything.

**Verification (dev venv, `libs/agno` on `PYTHONPATH`):**
- `pytest tests/` (whole folder): **29 passed** (was 21; +8 new).
- `run.py --mode fixture --benchmark all --provider all`: unchanged from the pre-X7 numbers above
  (no harness/metrics code touched) — confirms the merge additions didn't perturb the offline path.
- `ruff check --select E,F,W,I --line-length 120 --target-version py39 .` → All checks passed.
- `ruff format --check --line-length 120 .` → clean (28 files).
- `mypy --explicit-package-bases --ignore-missing-imports --python-version 3.10 harness run.py
  adapters` → 1 pre-existing error unrelated to this change (`LLMJudge.__init__`'s `scoring: str`
  vs `AgentAsJudgeEval`'s `Literal` param — reproduced identically against `origin/main`, not
  introduced here; left as-is per "don't fix unrelated pre-existing issues"). Note: this repo's
  installed mypy no longer accepts `--python-version 3.9` (raises "not supported, must be 3.10 or
  higher") — ran at 3.10 instead; not a regression in this change.
- `.github/workflows/memory-eval.yml` YAML-validated (`yaml.safe_load`); not run in Actions as part
  of this local verification (no CI runner available here).
