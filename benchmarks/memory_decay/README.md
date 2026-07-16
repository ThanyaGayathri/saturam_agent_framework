# Episodic Decay Model — Decision Harness (SM-R5)

Offline simulation used to **decide** how episodic memories decay / are weighted
over time in retrieval, and whether decay only re-ranks or also prunes. This is a
*decision task*: nothing here touches framework core (`libs/agno/egpt/**`). Every
candidate strategy lives inside `harness/`; only the chosen one is promoted to
core later (SM-C*).

- **Issue:** Saturam-Inc/aicoe-tracker#80
- **Outcome:** see [DECISION.md](DECISION.md)
- **Depends on:** SM-R1 (#76) — numeric targets in DECISION.md are provisional.

## Why a simulation

Recency/time decay is what makes retrieval genuinely *episodic* rather than plain
semantic RAG. The trade-off (drop stale facts vs. keep valid-but-old facts) is not
obvious from the literature numbers, so we implement all five candidates and
measure them on a controlled, reproducible synthetic stream. No LLM, no DB, no
network.

## What it does

1. **Synthetic stream** (`harness/stream.py`): a 90-day clock of memories —
   durable facts, versioned facts (updated over time; old versions go *stale*),
   ephemeral facts (expire), and never-queried noise. Ground truth at any query
   time is the currently-valid version of the topic. Relevance is a deterministic
   synthetic embedding (topic base vector + scaled noise) so same-topic cosine is
   high (~0.9) and cross-topic ~0, isolating the decay signal.
2. **Five strategies** (`harness/strategies.py`), each with sync + async methods:
   - `no_decay` — relevance only (conflict-driven baseline)
   - `exp_recency` — Generative Agents: `alpha*rel + beta*base^age + gamma*imp`
   - `ttl_halflife` — fixed half-life + optional hard TTL
   - `importance_weighted` — half-life scaled by importance
   - `ebbinghaus` — MemoryBank: `R = e^(-age/strength)`, strength grows on access
3. **Simulation** (`harness/simulate.py`): one chronological pass over queries +
   prune ticks; reinforce (reset last-access) every top-k injection; optionally
   prune. Metrics in `harness/metrics.py`.
4. **Runner** (`run_benchmark.py`) and **sensitivity sweep** (`sweep_params.py`)
   write to `results/`.

## Metrics

- **Retrieval quality:** nDCG@k, precision@k, recall@k, MRR, and **stale-hit-rate**
  (fraction of top-k that are same-topic but superseded/expired). Recall is also
  bucketed by fact age to expose over-forgetting of old-but-valid facts.
- **Pruning:** prune-precision, prune-recall, **false-forget** (pruned while still
  needed), and retained-count (growth).

## Relevance modes

The `α·relevance` term can be sourced two ways:

- **`synthetic`** (default) — deterministic topic-vector cosine. Fully offline,
  bit-reproducible, and isolates the decay signal. This is what the decision is
  made on.
- **`embedding`** — encode each episode/query's natural-language `text` (see
  `harness/vocab.py`) with a local `sentence-transformers` model
  (`all-MiniLM-L6-v2`) and use cosine over real embeddings. A **confirmation**
  run: it re-checks that the decision survives real semantic-retrieval noise. No
  network/keys after the first model download.

## Run

```bash
cd benchmarks/memory_decay
./setup.sh
./.venv/bin/python run_benchmark.py --seeds 5                    # synthetic (decision)
./.venv/bin/python run_benchmark.py --seeds 5 --relevance embedding  # confirmation
./.venv/bin/python sweep_params.py --seeds 5
```

Deterministic given the seeds (and, for embedding mode, the model). Results:
`results/summary.{json,md}`, `results/summary_embedding.{json,md}`,
`results/sensitivity.{json,md}`. See [TEST_LOG.md](TEST_LOG.md).

## Layout

```
benchmarks/memory_decay/
├── README.md              # this file
├── DECISION.md            # the deliverable: chosen model + rationale
├── TEST_LOG.md            # run log
├── setup.sh               # dedicated venv (numpy only)
├── requirements.txt
├── run_benchmark.py       # compare all five candidates (--relevance synthetic|embedding)
├── sweep_params.py        # parameter sensitivity
├── harness/               # stream, strategies, simulate, metrics, episode, vocab, relevance
├── results/               # generated outputs (synthetic + embedding)
└── findings/              # notes for the implementation phase (SM-C*)
```
