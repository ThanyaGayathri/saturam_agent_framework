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

---

## Acceptance criteria (verbatim from #88)

- [x] **Benchmarks + metrics chosen (incl. temporal + consolidation coverage)** — §1 (benchmarks),
      §2 (metrics), §2.4 (consolidation via the SM-R7 seam). Implemented: `harness/datasets/`,
      `harness/metrics.py`.
- [x] **Integration approach with the existing `eval/suite.py` defined** — §3. Implemented:
      `adapters/egpt_suite.py` (`build_memory_cases` → seed `Case`s; `LLMJudge` wraps
      `AgentAsJudgeEval`).
- [x] **Bi-temporal + decay behavior covered by the plan** — §2.3 + §1 (custom set). Implemented:
      `Probe.as_of` / `decay_target_turn_id`, `metrics.aggregate` (`as_of_leak_rate`, decay
      suppression/outranked), `fixtures/custom_temporal.sample.json`.
- [x] **CI reporting approach noted** — §4. Implemented: `harness/gate.py`, `ci/memory-eval.yml`,
      `run.py` (writes `results/*.json` + `summary_*.md`).

## Questions to answer (verbatim from #88)

| Question | Answer |
|---|---|
| Which benchmarks (LongMemEval / LOCOMO / DMR) + what each measures? | §1. LongMemEval = headline (5 long-term abilities incl. temporal + knowledge-update); LOCOMO = external-validity multi-session QA (single/multi-hop/temporal/adversarial); DMR = near-saturated sanity baseline; + a custom set for bi-temporal/decay. |
| Offline dataset + metrics (recall@k, temporal-QA, consolidation quality from SM-R7)? | §1.1 (fetch-on-demand + committed fixtures) + §2 (exact metric defs) + §2.4 (reuse SM-R7 `composite_quality` behind the `consolidate()` seam). |
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
| `eval/performance.py` `PerformanceEval` | `:186`; exposes `p95_run_time` | **Reuse** — latency mean/p95 SLO. |
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

### 2.2 Retrieval — recall@k, hit@k, nDCG@k
Over probes that have gold `evidence_ids` E and the provider's ranked `retrieved_ids` (top-k):
`recall@k = |E ∩ top-k| / |E|`; `hit@k = 1[E ∩ top-k ≠ ∅]`;
`nDCG@k = DCG/IDCG`, binary relevance, `DCG=Σ rel_i/log2(i+2)`, `IDCG=Σ_{i<min(|E|,k)} 1/log2(i+2)`.
Probes without evidence (DMR-thin, abstention) return `None` and are **excluded** (never scored 0).

### 2.3 Temporal, bi-temporal ("true at T"), decay
- **temporal accuracy** = QA accuracy over categories {temporal, knowledge_update, as_of_t}.
- **bi-temporal** = QA over {as_of_t, knowledge_update} **plus** `as_of_leak_rate` = fraction of
  as-of probes whose top-1 retrieval post-dates the query time T (lower better). A provider that
  honors `as_of` (e.g. `RecencyDecayProvider`) scores 0 leak; a naive one leaks.
- **decay** = for a probe naming a stale turn D: `suppression@k` = fraction with D **not** in top-k;
  `outranked` = fraction where every gold evidence turn ranks above D. Higher = better forgetting.

### 2.4 Consolidation quality (from SM-R7)
The `MemoryProvider` contract includes `consolidate(scope)`. Consolidation quality is **not**
re-invented here: SM-R7's `benchmarks/memory_consolidation` harness already defines
`composite_quality` (fact recall/precision, dedup, conflict, provenance). The plan reuses it —
a consolidation-backed provider runs `consolidate()` then this harness measures the **end-to-end
QA/temporal impact**, and imports SM-R7's label metrics for the intrinsic score. (Shared-schema
note + unification path: `findings/01`.)

### 2.5 Latency + cost
Latency mean/p50/**p95**/max for ingest and query (`stats.summarize`, p95 matches the seed
`PerformanceEval` convention). Cost = measured tokens × price table (`usage.py`; rates via
`HARNESS_PRICE_IN/OUT`) — a measured number, never cited.

## 3. Integration point — how the harness drives a MemoryProvider, wired to `eval/suite.py`

Two seams, both proven in code:

1. **Memory access** — the `MemoryProvider` protocol (`harness/provider.py`) is a thin projection
   of SM-R11's `read`/`write`/`consolidate` (mapping table in that module). The one requirement
   beyond R11 is **retrieval provenance**: retrieved items expose `source_ids` (turn ids) so
   recall/nDCG can score. Two baseline adapters (`harness/baselines.py`) prove the seam is
   swappable; SM-R7/R8's real providers drop in unchanged.
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
  `min_recall_at_k=0.60`, `max_query_p95_ms=1500` (SM-R3 latency SLO family), `max_cost_usd`
  set per-run once real token costs land. The gate demonstrably **fails** the naive baseline (see
  TEST_LOG) — it is a real regression guard.
- **Results land in** `results/` as JSON (machine) + markdown (human), mirroring SM-R3.

## 5. Couplings & handoff to SM-X7 (#143)

SM-X7 receives, concretely: the four dataset loaders + unified schema; `metrics.py` (exact defs +
tests); the `MemoryProvider` contract + `build_memory_cases` recipe for `run_cases`; `gate.py` +
the CI template. X7's home is `tests/memory/eval/` inside core; this folder is the portable source
it lifts. The `Answerer`/`Judge`/`MemoryProvider` seams mean X7 swaps the stub judge/answerer for
the LLM ones and the baselines for R7/R8 providers with no metric/runner changes.

## 6. Out of scope (owned elsewhere)

- The canonical `MemoryProvider` interface → **SM-R11 (#86)**; its implementation → **SM-C1 (#89)**.
- Consolidation strategy + its intrinsic metric → **SM-R7 (#82)** (reused, not redefined).
- Retrieval/store internals → **SM-R8 / SM-R3**. Latency SLO ratification + scale targets → **SM-R1**.
- Tool-call reliability eval (`eval/reliability.py`) — not memory quality.

## 7. Consistency with dependencies

- **SM-R1 (#76):** unratified → all gate thresholds + latency SLOs marked PROVISIONAL.
- **SM-R11 (#86):** the eval contract maps 1:1 onto `read`/`write`/`consolidate`; if R11's final
  `MemorySlice` differs, only the (single) adapter changes.
- **SM-R7 (#82):** consolidation quality reuses `composite_quality`; schemas kept shape-compatible.

## 8. Grounding & reproducibility

- Grounded in `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` (LongMemEval, LOCOMO, DMR; Zep
  LongMemEval +18.5%; temporal QA; recency/importance scoring). **Gap:** the doc's `docs/` symlink
  is absent in this worktree; grounding currently leans on the public benchmark sources + the
  sibling SM-R7 harness (`findings/01`). Fold in exact citations when the symlink is restored.
- **Reproduce:** `README.md`. Fixtures regenerate via `fixtures/_build_fixtures.py`; sample run via
  `python run.py --mode fixture --benchmark all --provider all`; results in `results/`.
