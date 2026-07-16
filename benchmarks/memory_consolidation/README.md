# Memory Consolidation Benchmark (SM-R7)

Research harness for **episodic -> semantic memory consolidation**: prototyping and
measuring candidate strategies so the recommendation in [`RESEARCH.md`](RESEARCH.md)
is backed by numbers, not citations. Tracker: `Saturam-Inc/aicoe-tracker#82`.

> This is a **research spike**. Nothing here is production code. The chosen
> strategy is promoted to `libs/agno/egpt/memory/` later, in the SM-L7
> implementation task. Candidate strategy implementations live inside this
> harness on purpose.

## What it does

Consolidation distils episodic memories ("things the user did") into durable
semantic memory ("things learned"): extract facts from episodes, merge/dedup
against existing memory (ADD / UPDATE / DELETE / NOOP), resolve conflicts (keep
newer, bi-temporal), and promote reflections — while preserving **provenance**
(each semantic fact cites its source episodes).

Five strategies are compared:

| key | strategy | idea |
|-----|----------|------|
| `baseline` | No consolidation | keep every episode verbatim (control) |
| `mem0` | Extract + merge | atomic facts, LLM ADD/UPDATE/DELETE/NOOP (mem0-style) |
| `reflection` | Generative-Agents | importance-gated insight synthesis |
| `hierarchical` | MemoryBank | session + rolling global summaries |
| `hybrid` | mem0 + reflection | atomic base + gated insights (recommended family) |

## Layout

```
harness/
  schemas.py        Episode, SemanticFact (provenance + bi-temporal), UsageStats
  llm.py            Azure Responses subclass + record/replay cache + cost model
  metrics.py        label metrics, assertion judge, QA correctness, composite
  runner.py         run/arun a strategy over a dataset and score it
  strategies/       baseline, mem0, reflection, hierarchical, hybrid (+ shared ops)
  datasets/         synthetic (labelled) + LOCOMO loader
tests/test_harness.py   offline self-tests (scripted fake LLM; no network)
results/            metric JSON + llm_cache/ (record-replay)
findings/           notes/patches for the SM-L7 implementation phase
run.py              CLI entrypoint
```

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ../../libs/agno openai tiktoken

# Azure OpenAI creds (Responses API). Copy the template and fill in:
cp .env.example .env      # then edit .env  (gitignored; never committed)
```

The `.env` needs `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`,
`AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_DEPLOYMENT`. The house model is
`gpt-5.5`; these runs used the available `gpt-5.4` Azure deployment (the repo's
own default model family is `gpt-5.4-mini`). Model id is not hard-coded — it is
the Azure deployment name.

## Run

```bash
# Warm the cache with real Azure calls (synthetic set + QA):
.venv/bin/python run.py --dataset synthetic --qa

# Offline, deterministic replay (CI-safe; needs a warm cache):
HARNESS_LLM_MODE=replay .venv/bin/python run.py --dataset synthetic --qa

# LOCOMO external validity (download once, then a scoped run):
.venv/bin/python run.py --dataset locomo --download-locomo --max-sessions 4 --qa --qa-limit 15

# Offline self-tests (no network / no key):
.venv/bin/python tests/test_harness.py
```

### LLM modes (`HARNESS_LLM_MODE`)

- `auto` (default): replay from cache if present, else call Azure and record.
- `replay`: cache only; errors on a miss. Fully offline and deterministic.
- `live`: always call.

All LLM calls are cached under `results/llm_cache/` keyed on the prompt, so reruns
are free and reproducible. Cost is estimated with a configurable price table
(`HARNESS_PRICE_IN` / `HARNESS_PRICE_OUT`, USD per 1M tokens) — see RESEARCH.md.

See [`TEST_LOG.md`](TEST_LOG.md) for run status and [`RESEARCH.md`](RESEARCH.md)
for the recommendation and evidence.
