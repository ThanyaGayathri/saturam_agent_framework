# Memory eval harness (SM-R13)

A reusable evaluation backbone for memory quality: it drives a `MemoryProvider` through an
**ingest → query → answer → score** loop over four benchmarks (LongMemEval, LOCOMO, DMR, and a
custom bi-temporal/decay set) and reports QA correctness, retrieval recall@k / nDCG@k, temporal
and bi-temporal accuracy, decay behavior, latency (p95) and token cost.

This is the **SM-R13 plan deliverable** (see [`PLAN.md`](PLAN.md)); **SM-X7 (#143)** implements it
on the seed `eval/suite.py`. Nothing here modifies core (`libs/agno/egpt/**`).

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
adapters/egpt_suite.py      bridge to egpt.eval.suite (LLMJudge + build_memory_cases)
fixtures/                   tiny format-faithful offline sets (raw samples + unified)
results/                    committed sample run (JSON + markdown)
tests/                      metric + runner + suite-adapter tests
ci/memory-eval.yml          two-tier CI template (on-PR smoke + scheduled full)
findings/                   data-quality & licensing note
```

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
  retrieval is not exercised. Embedder choice is SM-R9's; a real embedding provider drops in behind
  the seam unchanged. (`findings/02`.)
- **Consolidation quality: seam wired, intrinsic score not run here.** `consolidate()` is wired and
  tested, and the metric is defined as SM-R7's `composite_quality`, but running it needs SM-R7's
  labeled targets (SM-R7 owns them). (`PLAN.md §2.4`.)
- **Bi-temporal** uses SM-R10's two-sided `t_valid <= T < t_invalid`; **latency gate** is SM-R8's
  ~50 ms retrieval hot-path budget; the **decay clock** is a labeled non-production stand-in (not
  SM-R5's `last_accessed_at`). See `PLAN.md §2.3/§2.5` and `baselines.py`.
