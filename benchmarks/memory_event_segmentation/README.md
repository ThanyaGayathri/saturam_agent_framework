# Event Segmentation — Decision Harness (SM-R6)

Offline benchmark used to **decide** how the raw interaction/event stream is cut
into discrete **episodes** before they enter the episode log → consolidation
(SM-R7). This is a *decision task*: nothing here touches framework core
(`libs/agno/egpt/**`). Every candidate segmenter lives inside `harness/`; only the
chosen approach is promoted to core later (SM-E3 / SM-C*).

- **Issue:** Saturam-Inc/aicoe-tracker#81
- **Outcome:** see [DECISION.md](DECISION.md)
- **Depends on:** SM-R1 (#76) — thresholds/targets in DECISION.md are provisional.

## Why a benchmark

Segmentation is what turns a raw message stream into retrievable *episodes* — a
core episodic marker. Today the seed forms memories per turn with **no boundary
concept** (`memory/manager.py:377`, `session/summary.py:63`). The cost/quality
trade-off between cheap deterministic rules and richer surprise/LLM methods is not
obvious from the literature, so we **implement all of them** and measure them on a
controlled, labelled stream — under synthetic embeddings, **real** embeddings, a
**real gpt-5.4** call, and a **real on-prem SLM**.

## What it does

1. **Labelled stream** (`harness/stream.py`): multi-topic conversations built by
   concatenating coherent single-topic segments; the join between two segments is
   a ground-truth boundary. Human-readable topic text (`harness/topics.py`) so the
   LLM/SLM has real signal; per-event synthetic embeddings (topic base + noise,
   scaled 1/√dim); knobs for time gaps, backchannel filler and topic drift. Not
   every boundary gets a time gap (`p_time_gap`), so `time_gap` cannot trivially win.
2. **Eight candidates** (`harness/segmenters.py`, each sync + async):
   `single_session`, `per_turn` (naive baselines), `fixed_window`, `time_gap`,
   `embedding_drift` (TextTiling), `surprise_em_llm` (EM-LLM: Bayesian surprise +
   modularity/conductance graph refinement, `harness/surprise.py`), the
   `hybrid_gap_*` unions, and the LLM/SLM detector (`harness/llm.py`).
3. **Scoring** (`harness/metrics.py`, `harness/simulate.py`): boundary
   precision/recall/F1 (±tolerance), **Pk** and **WindowDiff** (Pevzner & Hearst),
   plus latency, tokens and cost for the model calls.
4. **Runner** (`run_benchmark.py`) and **sweeps** (`sweep_params.py`) write to
   `results/`.

## Metrics

- **Boundary detection:** precision / recall / F1 with a ±`tol` event tolerance
  (greedy one-to-one match).
- **Segmentation error:** **Pk** and **WindowDiff** — window-based penalties that,
  unlike raw F1, do not over-punish near-misses and reward getting the *number* of
  episodes right. Both in [0, 1], **lower is better**.
- **Cost:** wall-clock latency, input/output tokens, and USD/session (model calls).

## Embedding modes

- **`synthetic`** (default) — deterministic topic-vector embeddings. Fully
  offline, bit-reproducible, isolates the topic signal. Sensitivity/robustness
  sweeps run here.
- **`embedding`** — encode each turn's real text with a local
  `sentence-transformers` model (`all-MiniLM-L6-v2`). A **confirmation** run: it
  re-checks the decision survives real semantic-retrieval noise. (This is where
  the ranking of the semantic methods changes — see DECISION.md §3.)

## LLM / on-prem SLM

The `llm_boundary` option makes **one real model call per stream** (not one per
gap) — how it would be deployed:

- `--llm-provider azure_responses` — Azure OpenAI **Responses API** (production
  `OpenAIResponses` path). Reads `AZURE_OPENAI_*` from a **gitignored `.env`** (see
  `.env.example`). Model id defaults to `gpt-5.5` per repo policy; measured on a
  gpt-5.4 deployment.
- `--llm-provider ollama` — local, on-prem SLM (no key, no budget). The on-prem
  alternative the task asks us to consider.

**Never commit real keys.** Put them in `.env` (already gitignored).

## Run

```bash
cd benchmarks/memory_event_segmentation
./setup.sh
./.venv/bin/python run_benchmark.py --seeds 5                          # offline, synthetic
./.venv/bin/python run_benchmark.py --seeds 5 --relevance embedding    # real-embedding confirm
./.venv/bin/python sweep_params.py  --seeds 5                          # sensitivity + robustness
# real model calls (optional):
./.venv/bin/python run_benchmark.py --seeds 5 --llm --llm-provider azure_responses   # gpt-5.5 (needs .env)
./.venv/bin/python run_benchmark.py --seeds 3 --llm --llm-provider ollama            # on-prem SLM
```

Deterministic given the seeds (offline modes). Results land in `results/`. See
[TEST_LOG.md](TEST_LOG.md).

## Layout

```
benchmarks/memory_event_segmentation/
├── README.md              # this file
├── DECISION.md            # the decision + measured rationale
├── TEST_LOG.md            # run log
├── requirements.txt
├── setup.sh
├── .env.example           # copy to .env (gitignored) for the LLM option
├── run_benchmark.py       # main runner (offline + optional LLM/SLM)
├── sweep_params.py        # sensitivity + robustness sweeps
├── harness/
│   ├── topics.py          # readable multi-topic content
│   ├── stream.py          # labelled stream generator (+ noise knobs)
│   ├── embeddings.py      # real sentence-transformers vectors (confirmation)
│   ├── segmenters.py      # the 8 candidates (sync + async)
│   ├── surprise.py        # EM-LLM: surprise + modularity/conductance refine
│   ├── llm.py             # Azure Responses + ollama SLM detector
│   ├── metrics.py         # P/R/F1, Pk, WindowDiff
│   └── simulate.py        # run + score one segmenter
├── results/               # generated json + md
└── findings/              # notes for the implementation phase (SM-C*/SM-E*)
```
