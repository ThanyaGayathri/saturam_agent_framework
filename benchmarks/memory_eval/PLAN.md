<!--
SM-R13 plan document. The numbers in results/ are produced by THIS harness on the
committed offline fixtures (stub judge, tiny smoke set) — regenerate with run.py.
They prove executability, not system rankings. See README.md.
-->
# SM-R13 — Plan: memory eval harness (LongMemEval / LOCOMO / DMR)

- **Status:** Proposed (plan + runnable skeleton)
- **Tracking issue:** [Memory] SM-R13 — Saturam-Inc/aicoe-tracker#88
- **Decisions referenced:** D7, D8
- **Depends on:** SM-R1 (#76, ADR — *not yet ratified*; targets below are provisional for R1)
- **Feeds:** SM-X7 (#143, implements this harness on `eval/suite.py`). Once implemented it is
  the reusable backbone SM-R7 (consolidation) / SM-R8 (retrieval) measure against.
- **Seam referenced:** SM-R11 (#86) `MemoryProvider` (`read`/`write`/`consolidate`) — referenced, not redefined.

## What this deliverable is

A **plan** whose claims are backed by a **runnable harness skeleton** (`harness/`, `run.py`,
`tests/`). It loads four benchmarks into one schema, drives a `MemoryProvider` through an
ingest→query→answer→score loop, computes every metric defined below, and gates on them — proven
by scoring two baseline providers offline (`results/`). SM-X7 lifts this onto `eval/suite.py`.

> ⚠️ **STATUS CAVEAT (updated by SM-X7, #143): the offline instrument below is still
> CONSTRUCTION-VALIDATED, NOT run end-to-end — that part of this caveat stands.** Offline it uses
> a **stub lexical judge** (a proxy that tracks recall) and **lexical baselines**; SM-R7's
> label-based **`composite_quality` is NOT run** (only the labelless footprint). So the committed
> numbers below prove the harness *computes and discriminates*, **not** system quality —
> consolidation *quality* (the hardest problem, PR #8) in particular cannot be judged from them.
>
> **What SM-X7 closed:** the seed **`run_cases`/`arun_cases` wiring described in §3 is no longer
> static-traced only** — `adapters/egpt_suite.run_memory_suite`/`arun_memory_suite` actually call
> them and merge the retrieval/temporal/decay/footprint metrics onto the QA verdicts they return
> (`tests/test_suite_run.py`, mocking `run_cases` itself since exercising it for real needs a live
> LLM). **What is still open:** that wiring is reachable only as a **library API** — nothing in the
> repo calls it. `run.py` (both modes) and both CI jobs drive the older `harness.runner.run_benchmark`
> path, so no CI job and no CLI invocation executes `run_cases` against an unmocked benchmark. A
> *live* run of the path (real answer agent + real LLM judge) has therefore not been executed, and
> routing a `run.py` mode through `run_memory_suite` plus a CI job to exercise it is its own ticket,
> not part of SM-X7. SM-R7's labeled `composite_quality` still needs SM-R7's targets. **Before
> any decision leans on real-corpus numbers, run `run_memory_suite` against the real corpora with
> the LLM judge + SM-R7 labels** — see README.md "Via the seed's `eval/suite.py`".

---

## Acceptance criteria (verbatim from #88)

- [x] **Benchmarks + metrics chosen (incl. temporal + consolidation coverage)** — §1 (benchmarks),
      §2 (metrics). Temporal/bi-temporal/decay fully implemented + tested. Consolidation coverage =
      **labelless footprint wired now + seam wired; SM-R7 `composite_quality` (label-based) defined,
      not run here** — see §2.4 for the honest split. Implemented: `harness/datasets/`, `harness/metrics.py`.
- [x] **Integration approach with the existing `eval/suite.py` defined** — §3. Implemented:
      `adapters/egpt_suite.py` (`build_memory_cases` → seed `Case`s; `LLMJudge` wraps
      `AgentAsJudgeEval`).
- [x] **Bi-temporal + decay behavior covered by the plan** — §2.3 + §1 (custom set). Implemented:
      `Probe.as_of` / `decay_target_turn_id`, `metrics.aggregate` (`as_of_leak_rate`, decay
      suppression/outranked), `fixtures/custom_temporal.sample.json`.
- [x] **CI reporting approach noted** — §4. Implemented: `harness/gate.py`, `run.py` (writes
      `results/*.json` + `summary_*.md`), and — closed by SM-X7 — `.github/workflows/memory-eval.yml`
      (`ci/memory-eval.yml` here is the archived draft it was installed from).

## Questions to answer (verbatim from #88)

| Question | Answer |
|---|---|
| Which benchmarks (LongMemEval / LOCOMO / DMR) + what each measures? | §1. LongMemEval = headline (5 long-term abilities incl. temporal + knowledge-update); LOCOMO = external-validity multi-session QA (single/multi-hop/temporal/adversarial); DMR = near-saturated sanity baseline; + a custom set for bi-temporal/decay. |
| Offline dataset + metrics (recall@k, temporal-QA, consolidation quality from SM-R7)? | §1.1 (fetch-on-demand + committed fixtures) + §2 (exact metric defs). Consolidation quality = SM-R7's `composite_quality` **defined + seam-wired, not run here** (needs SM-R7's labeled targets) — §2.4. |
| How to test bi-temporal ("true at T") + episodic decay specifically? | §2.3. `as_of`-tagged probes whose gold value changes over time (leak rate + QA), and decay probes naming a stale turn that must be de-ranked (suppression@k / outranked). |
| How to wire memory cases into `eval/suite.py` `run_cases`? | §3. `build_memory_cases()` builds one `Case` per probe (retrieval → context in `input`, gold → `criteria`, `AgentAsJudgeEval` judge), returns the retrieved-id map so recall/nDCG merge onto the `SuiteResult`. |

## Options evaluated (verbatim list from #88)

| Option | Decision | Why |
|---|---|---|
| **LongMemEval** | **Include — headline** | Most discriminative; covers temporal + knowledge-update + abstention. Loader: `harness/datasets/longmemeval.py`. |
| **LOCOMO** | **Include — external validity** | Independent multi-session set with turn-level evidence → validates recall/nDCG + temporal. Loader: `harness/datasets/locomo.py`. |
| **DMR** | **Include — sanity baseline only** | Near-saturated (issue's own note); kept as a low-value cross-check, never headline. Loader: `harness/datasets/dmr.py`. |
| **Custom temporal + consolidation set** | **Include — targeted** | Public sets don't isolate bi-temporal "true at T" or decay; a small in-house probe set does. Loader: `harness/datasets/custom_temporal.py`. Single-authored caveat in `findings/01`. |

*(User ratified "all four" + gpt-5.4 for the live judge, 2026-07-27.)*

## Seed-context verification & disposition

Issue seed facts: `eval/{accuracy,agent_as_judge,performance,reliability,suite}.py`. Verified in
the **current** tree at `libs/agno/egpt/eval/` (post-rebrand path unchanged):

| Seed surface | Verified | Disposition |
|---|---|---|
| `eval/suite.py` `Case`/`run_cases`/`SuiteResult` | `suite.py:60` (`Case`), `:578` (`run_cases`), `:483` (`arun_cases`) | **Reuse** — memory probes become `Case`s; `SuiteResult.to_dict()` is the CI contract. Never modified. |
| `eval/agent_as_judge.py` `AgentAsJudgeEval` | `:168`; `arun(input,output)`→`AgentAsJudgeResult.results[i].{passed,score,reason}` | **Reuse** — QA-correctness grading (wrapped by `LLMJudge`). |
| `eval/performance.py` `PerformanceEval` | `:186`; exposes `p95_run_time` | **Reuse (planned)** — the suite/full path (SM-X7) calls it; the offline harness replicates its p95 convention in `stats.py` (not imported here). |
| `eval/accuracy.py` `AccuracyEval` | `:149` (`run`/`arun`) | Reuse where a reference-answer accuracy is wanted. |
| `eval/reliability.py` `ReliabilityEval` | present | Out of scope here (tool-call reliability, not memory). |

**Core untouched:** `git diff origin/main -- libs/` is empty; all work is under `benchmarks/memory_eval/`.

---

## 1. Benchmarks + offline access

Each benchmark is normalized into one schema (`harness/schemas.py`: `Turn` / `Session` / `Probe`
/ `Benchmark`) so one runner and one metric suite serve all four. Retrieval ground truth is the
`turn_id`; a `Probe` carries `evidence_ids` (gold turns), a canonical `category`, `as_of`
(bi-temporal), and `decay_target_turn_id`.

### 1.1 Offline / air-gapped availability (data-quality: `findings/01`)

Real corpora are **never vendored** (licensing). Each loader exposes `download()` (→ gitignored
`data/<name>/raw/`) and `build(path)` (parses real file OR a committed synthetic raw sample). CI
scores the tiny, **format-faithful synthetic fixtures** under `fixtures/` — no third-party content,
so no redistribution question. Full runs fetch the real sets. Licensing table + caveats:
`findings/01-data-quality-and-licensing.md`.

## 2. Metrics — exact definitions (`harness/metrics.py`)

All definitions live in the module docstring + code so they are auditable, and are unit-tested on
hand-computed values (`tests/test_metrics.py`). Summary:

### 2.1 QA correctness
Per probe, a `Judge` verdict (graded 1–10, pass at threshold, or binary). `accuracy = mean(correct)`,
reported overall, **per canonical category**, and with a **Wilson 95% CI** (`stats.py`) so close
options are not ranked inside noise (skill §5). Offline: deterministic `StubJudge`; full runs:
`LLMJudge` = seed `AgentAsJudgeEval` (gpt-5.4/5.5, Azure per `.env`).

### 2.2 Retrieval — recall@k, hit@k, nDCG@k, all_hops_hit@k
Over probes that have gold `evidence_ids` E and the provider's ranked `retrieved_ids` (top-k):
`recall@k = |E ∩ top-k| / |E|`; `hit@k = 1[E ∩ top-k ≠ ∅]`;
`nDCG@k = DCG/IDCG`, binary relevance, `DCG=Σ rel_i/log2(i+2)`, `IDCG=Σ_{i<min(|E|,k)} 1/log2(i+2)`;
**`all_hops_hit@k = 1[E ⊆ top-k]`** over multi-hop probes (`|E| >= 2`) — the strict multi-hop
retrieval signal (SM-R9), since hit/recall reward partial retrieval. Probes without evidence
(DMR-thin, abstention) return `None` and are **excluded** (never scored 0). Graph/PPR retrieval path
+ bridge dataset are scoped to SM-X7 — see §2.6.

### 2.3 Temporal, bi-temporal ("true at T"), decay
- **temporal accuracy** = QA accuracy over categories {temporal, knowledge_update, as_of_t}.
- **bi-temporal** = QA over {as_of_t, knowledge_update} **plus** a **two-sided, top-k** `as_of_leak_rate`.
  SM-R10 canonical (SPEC §7): a fact is true at T iff `t_valid <= T < t_invalid` (**both bounds
  required**). `Turn` carries `t_valid`/`t_invalid` (open interval → +inf). For any probe with an
  `as_of` = T, **every** top-k retrieved turn must satisfy `valid_at(T)`; a leak = **any** top-k turn
  NOT valid at T — **post-dates T** (future) OR **already superseded** (`t_invalid <= T`, a stale
  fact returned as current). Checking only top-1 would miss a stale fact at rank 2..k, so the metric
  scans the whole top-k; `as_of_top1_leak_rate` + `as_of_first_leak_rank` keep the head/rank signal.
  (The lexical baselines lack bi-temporal invalidation → top-1 valid but top-k can leak; only a real
  bi-temporal provider drives it to 0. Unit-tested in `tests/test_metrics.py`.)
- **decay** = for a probe naming a stale turn D: `suppression@k` = fraction with D **not** in top-k;
  `outranked` = fraction where every gold evidence turn ranks above D. Higher = better forgetting.

### 2.4 Consolidation quality (from SM-R7) — labelless footprint wired; label-based score deferred
Honest status, split into what runs now vs what is deferred:

- **Wired now (labelless):** a reference-free **memory footprint** — `{items, tokens}` over the
  provider's store (`MemoryProvider.footprint()`, in every report as `memory_footprint`, unit-tested).
  This is SM-R7's own companion signal (`active_fact_count`/`memory_tokens`; the `composite_quality`
  docstring says to *"read the score alongside footprint"*): it needs **no labels**, and a
  consolidating provider shrinks it vs a raw store. This is the partial score wireable today.
- **Wired seam:** `consolidate(tiers)` is called by `runner.py` when `RunConfig.consolidate=True`
  (unit-tested end-to-end); baselines' `consolidate()` is a no-op, an SM-R7 provider drops in behind it.
- **Deferred (label-based):** SM-R7's `composite_quality` (`benchmarks/memory_consolidation/harness/
  metrics.py:258`) is a weighted blend of fact recall/precision, dedup, conflict and provenance —
  **all six terms are label-gated** (computed via `match_facts_to_targets(facts, targets)`), so it has
  **no labelless mode**. Running it needs SM-R7's `TargetFact` labels, which SM-R7 owns and this
  harness's benchmarks do not carry. Its intrinsic score is produced by SM-R7's harness; here it is
  defined + reused, plus the planned end-to-end QA-after-consolidate impact. (Unification: `findings/01`.)

### 2.5 Latency + cost
Latency mean/p50/**p95**/max for ingest and query. `stats.summarize` replicates the p95 convention
of the seed `eval/performance.py` `PerformanceEval` (which is not imported by the offline,
stdlib-only harness; the suite/full path in SM-X7 calls `PerformanceEval` directly). The **query**
p95 measures `provider.read()` only (the retrieval hot path) — the number the gate holds to SM-R8's
~50 ms budget; end-to-end QA latency (incl. LLM answer+judge) is reported separately and not gated
on that budget. Cost = measured tokens × price table (`usage.py`; rates via `HARNESS_PRICE_IN/OUT`)
— a measured number, never cited.

### 2.6 Graph / multi-hop track + cross-tenant isolation
- **Graph/multi-hop (D8's graph half).** SM-R9 (multi-hop + HippoRAG PPR) and SM-R10 SS12.1(c)
  (bi-temporal PPR) assign "PPR ranking quality over a time-filtered projection" to this eval. Wired
  now: **`all_hops_hit@k`** (§2.2) + multi-hop probes (e.g. LOCOMO 2-hop) + composition with the
  top-k `as_of_leak`. Scoped to **SM-X7** (named owner): the graph-traversal/**PPR provider** (consumes
  SM-R9 + SM-G1), a **bridge-passage graph dataset**, and the **bi-temporal-graph PPR probe** — all
  drop in behind the existing seam with no harness change. Full rationale: `findings/03`.
- **Cross-tenant isolation (security).** The harness threads a `tenant` (→ R11 `Scope.tenant_id`) and
  ships a provider-level probe (`harness/isolation.py`): write tenant A + B into one provider, read
  each scope, assert **0** foreign memories. It is a **gate** — any cross-tenant leak fails the build
  (`run.py`; unit-tested). This catches a provider scope-binding/cache leak the store-level tests
  (SM-R3 #7, data model #18) cannot see.

## 3. Integration point — how the harness drives a MemoryProvider, wired to `eval/suite.py`

Two seams, both proven in code:

1. **Memory access** — the `MemoryProvider` protocol (`harness/provider.py`) is a thin projection of
   SM-R11's **current, ratified** signatures — `read(scope: Scope, query: MemoryQuery)`,
   `write(scope, events)`, `consolidate(scope, tiers)` — with the full mapping table in that module
   (the harness's `k`/`as_of`/`types` → `MemoryQuery.limit`/`.as_of`/`.tiers`; tenant/agent →
   `Scope`). *(An earlier draft copied the issue-#86 strawman `read(tenant, agent, query, types)`;
   SM-R11's own `interface.py`/SPEC §5.3 DELIBERATE-DEVIATION callout names this PR — now corrected.)*
   The one requirement beyond R11 is **retrieval provenance**: retrieved items expose `source_ids`
   (turn ids) so recall/nDCG can score. Two baseline adapters (`harness/baselines.py`) prove the
   seam is swappable; SM-R7/R8's real providers drop in unchanged.
2. **Eval scaffolding** — `adapters/egpt_suite.build_memory_cases()` turns a `Benchmark` + provider
   into seed `egpt.eval.suite.Case`s: ingest once, then per probe `read()` → memory context in
   `Case.input`, gold → `Case.criteria`, judged by `AgentAsJudgeEval`; `run_cases` produces the
   `SuiteResult` CI contract. It returns the retrieved-id map so recall/nDCG (not native to the
   suite) merge on. QA correctness + latency come from the suite; the memory-specific metrics come
   from `harness/metrics.py`.

The offline `harness/runner.py` (sync + async) is the executable equivalent used in CI without an
LLM; the suite adapter is the same wiring against the real seed API (exercised in the venv job).

## 4. CI / reporting

Two-tier (`ci/memory-eval.yml` template + `harness/gate.py` + `run.py`):
- **On-PR offline smoke** — stdlib-only fixtures through the **reference provider**, unit tests,
  and the gate. Deterministic, zero LLM spend. Gates the merge.
- **Scheduled/manual full run** — downloads real corpora, real LLM judge (secrets), uploads
  `results/*.json` + `summary_*.md` artifacts.
- **Pass/fail gates** (`GateConfig`, PROVISIONAL for SM-R1): `min_qa_accuracy=0.50`,
  `min_recall_at_k=0.60`, `max_query_p95_ms=50` (SM-R8 ratified hot-path p95 `<~50 ms`, retrieval
  read only — was mis-cited as 1500), `max_cost_usd`
  set per-run once real token costs land. The gate demonstrably **fails** the naive baseline (see
  TEST_LOG) — it is a real regression guard.
- **Results land in** `results/` as JSON (machine) + markdown (human), mirroring SM-R3.

## 5. Couplings & handoff to SM-X7 (#143)

SM-X7 receives, concretely: the four dataset loaders + unified schema; `metrics.py` (exact defs +
tests); the `MemoryProvider` contract + `build_memory_cases` recipe for `run_cases`; `gate.py` +
the CI template. The `Answerer`/`Judge`/`MemoryProvider` seams mean X7 swaps the stub judge/answerer
for the LLM ones and the baselines for R7/R8 providers with no metric/runner changes.

**Deviation from the "lifts into `tests/memory/eval/` inside core" line above (recorded, not
silent):** `tests/memory/eval/` does not correspond to anything in this repo's actual layout — the
repo has no top-level `tests/`; core tests live at `libs/agno/tests/`, and this epic's own
convention for iterative memory deliverables is a sibling top-level `benchmarks/memory_*/` folder
(`memory_consolidation`, `memory_multihop`, etc. — this folder among them). Moving this harness's
~30 files into `libs/agno/tests/` would also break its deliberate "stdlib-only, egpt-free offline
core" property (`harness/` has zero egpt import; only `adapters/` does), which is what lets
`tests/test_metrics.py`/`test_runner.py` run without the dev venv at all. SM-X7 therefore closes
the *behavioral* handoff (real `run_cases` wiring, §3; CI, §4) in place, rather than relocating the
tree — a physical move stays open as a follow-up if `benchmarks/` is ever retired as a convention.

## 6. Out of scope (owned elsewhere)

- The canonical `MemoryProvider` interface → **SM-R11 (#86)**; its implementation → **SM-C1 (#89)**.
- Consolidation strategy + its intrinsic metric → **SM-R7 (#82)** (reused, not redefined).
- Retrieval/store internals → **SM-R8 / SM-R3**. Latency SLO ratification + scale targets → **SM-R1**.
- **Embedder choice → SM-R4** (#79, ratified `bge-base-en-v1.5`/`text-embedding-3-large`); this
  harness ships **lexical-only** baselines (no embedder). Known coverage gap — semantic/embedding
  retrieval is not exercised here; `findings/02`.
- **Graph-traversal / PPR retrieval provider + bridge-passage graph dataset + bi-temporal-graph PPR
  probe → SM-X7 (#143)** (consuming **SM-R9** PPR + **SM-G1** graph store + **SM-R10** bi-temporal).
  The multi-hop *metric* (`all_hops_hit@k`) + probe shape are wired here (§2.6); the graph *provider*
  + dataset are the deferral's runnable subject, owned by SM-X7. `findings/03`.
- Tool-call reliability eval (`eval/reliability.py`) — not memory quality.

## 7. Consistency with dependencies

- **SM-R1 (#76):** unratified → all gate thresholds + latency SLOs marked PROVISIONAL.
- **SM-R11 (#86):** the eval contract maps 1:1 onto R11's current signatures
  `read(Scope, MemoryQuery)` / `write(Scope, events)` / `consolidate(Scope, tiers)` (verified against
  `interface.py` on `origin/feat/memory-SM-R11`, the twice-fixed version); the harness's colliding
  `consolidate(scope: str)` param was renamed to `tiers`. If R11's final `MemorySlice`/`MemoryRecord`
  differs, only the (single) adapter changes.
- **SM-R7 (#82):** consolidation quality **defines/reuses** `composite_quality` (not run here — needs
  SM-R7's labeled targets); schemas kept shape-compatible.
- **SM-R10 (#85):** bi-temporal "true at T" uses R10's canonical `t_valid <= T < t_invalid` (both
  bounds); `Turn` carries `t_valid`/`t_invalid`.
- **SM-R8 (#84):** retrieval-latency gate set to R8's `p95 < ~50 ms` hot-path budget.
- **SM-R5 (#80):** the `RecencyDecayProvider` clock is a **non-production creation-time** stand-in,
  explicitly NOT R5's `now - last_accessed_at` (labeled in `baselines.py` so it is not copied).

## 8. Grounding & reproducibility

- Grounded in `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` (LongMemEval, LOCOMO, DMR; Zep
  LongMemEval +18.5%; temporal QA; recency/importance scoring). **Gap:** the doc's `docs/` symlink
  is absent in this worktree; grounding currently leans on the public benchmark sources + the
  sibling SM-R7 harness (`findings/01`). Fold in exact citations when the symlink is restored.
- **Reproduce:** `README.md`. Fixtures regenerate via `fixtures/_build_fixtures.py`; sample run via
  `python run.py --mode fixture --benchmark all --provider all`; results in `results/`.
