# Memory eval harness (SM-R13)

A reusable evaluation backbone for memory quality: it drives a `MemoryProvider` through an
**ingest → query → answer → score** loop over four benchmarks (LongMemEval, LOCOMO, DMR, and a
custom bi-temporal/decay set) and reports QA correctness, retrieval recall@k / nDCG@k, temporal
and bi-temporal accuracy, decay behavior, latency (p95) and token cost.

This is the **SM-R13 plan deliverable** (see [`PLAN.md`](PLAN.md)); **SM-X7 (#143)** implements it
on the seed `eval/suite.py` — `adapters/egpt_suite.run_memory_suite`/`arun_memory_suite` actually
drive the seed's `run_cases`/`arun_cases` and merge the metrics it doesn't compute onto the QA
verdicts they return. Nothing here modifies core (`libs/agno/egpt/**`).

## Layout

```
PLAN.md                     the plan (mirrors issue #88 field-by-field)
harness/
  schemas.py                unified Turn / Session / Probe / Benchmark
  datasets/                 loaders: longmemeval, locomo, dmr, custom_temporal (+ registry)
  metrics.py                recall@k, nDCG@k, QA, temporal, bi-temporal, decay, latency, cost
  stats.py  usage.py        percentile/CI ; token-cost accounting
  judge.py                  Judge protocol + deterministic StubJudge (offline)
  provider.py               MemoryProvider eval contract (projection of SM-R11)
  baselines.py              two baseline providers + StubAnswerer (+ registry)
  runner.py                 the eval loop (sync + async)
  report.py  gate.py        markdown summary ; CI pass/fail gate
adapters/egpt_suite.py      bridge to egpt.eval.suite (LLMJudge, build_memory_cases,
                            run_memory_suite/arun_memory_suite)
fixtures/                   tiny format-faithful offline sets (raw samples + unified)
results/                    committed sample run (JSON + markdown)
tests/                      metric + runner + suite-adapter/wiring tests
ci/memory-eval.yml          archived SM-R13 draft of the CI workflow — the real one is
                            .github/workflows/memory-eval.yml (SM-X7)
findings/                   data-quality & licensing note
```

CI: `.github/workflows/memory-eval.yml` runs the offline smoke tier (zero LLM spend, gated on
this repo's `run-ci` PR label) on every PR touching this folder or `eval/suite.py`, and the full
tier (real corpora + real LLM judge) on a weekly schedule / manual dispatch only — never on a PR.

## Run it

Everything under `harness/` is **stdlib-only** and runs with any `python3` (no egpt needed):

```bash
cd benchmarks/memory_eval

# unit tests (offline, deterministic)
python tests/test_metrics.py
python tests/test_runner.py

# offline smoke: all fixtures, both baseline providers, + CI gate
python run.py --mode fixture --benchmark all --provider all
```

Outputs land in `results/results_<UTC>_fixture.json` and `results/summary_<UTC>_fixture.md`.

### Via the seed's `eval/suite.py` (SM-X7)

`adapters/egpt_suite.run_memory_suite(benchmark, provider)` (and its async twin
`arun_memory_suite`) is the executable form of `PLAN.md §3`: it builds one `Case` per probe,
drives them through the seed's `run_cases`/`arun_cases` (real answer agent + real LLM judge), and
merges the metrics `run_cases` doesn't compute (recall@k, nDCG, temporal, bi-temporal, decay,
footprint) onto its QA verdicts, returning one `MemorySuiteResult(suite, metrics)`. This makes a
real LLM call, so it is not part of the offline smoke tier — `tests/test_suite_run.py` proves the
merge logic against a mocked `run_cases` (no network), and `tests/test_suite_adapter.py` proves
the `Case` objects it builds compile against the real seed API. Exercise it with real credentials
via the dev venv:

```python
from adapters.egpt_suite import run_memory_suite
from harness.baselines import get_provider
from harness.datasets import get_loader
from harness.provider import RunConfig

result = run_memory_suite(get_loader("longmemeval").fixture(), get_provider("recency_decay"), RunConfig(k=3))
print(result.suite.status, result.metrics["qa_accuracy"])
```

**Reachability — this path is a library API, not a wired-up run mode.** Nothing in the repo calls
`run_memory_suite`/`arun_memory_suite` outside the tests above: `run.py` (both `--mode fixture` and
`--mode full`) drives the older `harness.runner.run_benchmark` path, and both CI jobs go through
`run.py`. So no CI job and no CLI invocation executes `run_cases` against a real, unmocked
benchmark today — the snippet above is currently the only way to exercise it. SM-X7's AC1 is
closed as *"the seed's suite is wired and unit-tested against mocks"*, not as *"the benchmark runs
through the suite in CI"*. Adding a `run.py` mode (or flag) that routes to `run_memory_suite`, and
a CI job that exercises it on the offline fixture, is deliberately **not** in this PR and belongs
to its own ticket.

**Latency semantics — `latency_ms.answer` means different things on the two paths.** Both write to
`ProbeRecord.answer_ms` and both aggregate into `latency_ms.answer`, but:

| path | what `answer` measures |
| --- | --- |
| `harness.runner.run_benchmark` | the answerer call alone (`answerer.answer(...)`) |
| `adapters.egpt_suite.run_memory_suite` | the seed's whole-case clock — `setup`, agent answer, **LLM judge**, reliability check, `teardown` |

On a live judge the judge call dominates, so this path's number is closer to case wall time than
to time-to-answer. **Do not compare `latency_ms.answer` across the two paths.** The field is not
renamed because `answer_ms` lives on the shared `ProbeRecord` that `run_benchmark` also writes;
renaming it for one path would fork the schema or change the offline runner's output.

### Full runs (real corpora + real LLM judge) — gated

Needs the dev venv (`./scripts/dev_setup.sh`), network to download, and gpt-5.4/5.5 credentials in
`.env`. **Ask before any large run** (dataset size + token budget):

```bash
python run.py --mode full --benchmark longmemeval --provider recency_decay --judge llm
```

Datasets are fetched on demand into gitignored `data/<name>/raw/` (never vendored — see
`findings/01-data-quality-and-licensing.md`). DMR has no canonical URL: set `DMR_URL`/`DMR_JSON`.

## Regenerate fixtures

```bash
python fixtures/_build_fixtures.py    # rebuilds unified fixtures from the raw samples
```

## Config (env)

| Var | Meaning | Default |
|---|---|---|
| `HARNESS_JUDGE_MODEL` | LLM judge model id | `gpt-5.5` (use gpt-5.4 per `.env`) |
| `HARNESS_PRICE_IN` / `_OUT` | USD per 1M tokens for cost | 1.25 / 10.0 (placeholder) |
| `LONGMEMEVAL_JSON` / `LOCOMO_JSON` / `DMR_JSON` | local corpus paths | fetched on demand |
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` | Azure backend for the judge | unset → plain OpenAIResponses |

## Note on the sample numbers

The committed `results/` come from the **tiny offline fixtures + stub judge**. They prove the
harness computes end-to-end and that the metrics **discriminate** providers; they are **not**
system rankings. Use `--mode full` on the real corpora with the LLM judge for that.

## Scope & known gaps (read before trusting a number)

- **Lexical-only baselines — no embedder.** Both providers rank by token overlap; semantic/embedding
  retrieval is not exercised. Embedder choice is **SM-R4's** (#79); a real embedding provider drops
  in behind the seam unchanged. (`findings/02`.)
- **Consolidation quality: seam wired, intrinsic score not run here.** `consolidate()` is wired and
  tested, and the metric is defined as SM-R7's `composite_quality`, but running it needs SM-R7's
  labeled targets (SM-R7 owns them). (`PLAN.md §2.4`.)
- **Bi-temporal** uses SM-R10's two-sided `t_valid <= T < t_invalid`; **latency gate** is SM-R8's
  ~50 ms retrieval hot-path budget; the **decay clock** is a labeled non-production stand-in (not
  SM-R5's `last_accessed_at`). See `PLAN.md §2.3/§2.5` and `baselines.py`.
