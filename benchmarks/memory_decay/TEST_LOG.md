# TEST_LOG — Episodic Decay Harness (SM-R5)

Environment: dedicated venv `benchmarks/memory_decay/.venv` (Python 3.9, numpy
pinned <2 for torch ABI). Synthetic mode: no external services. Embedding mode:
local `sentence-transformers` model (downloaded once, then offline). Deterministic
given seeds (and model).

---

### run_benchmark.py --seeds 5 (synthetic relevance)

**Status:** PASS

**Description:** Runs all five candidate decay strategies over 5 seeds (90-day
horizon, ~450 episodes / ~190 queries per seed), ranking-only and prune-enabled.
Writes `results/summary.json` and `results/summary.md`.

**Result:** Completed cleanly. importance_weighted is Pareto-strong: recall@k
0.937 (0.787 at 30d+) with stale-hit-rate 0.110 (vs no_decay 1.000 / 0.151) and
**0 false-forgets** when pruning. Pure-recency candidates (exp_recency,
ttl_halflife, ebbinghaus) suppress stale (~0.03–0.05) but over-forget old valid
facts (30d+ recall 0.16–0.44) and false-forget 21–28 memories under pruning.
Basis for DECISION.md.

---

### run_benchmark.py --seeds 5 --relevance embedding (real embeddings)

**Status:** PASS

**Description:** Confirmation run using real `all-MiniLM-L6-v2` embeddings of the
episode/query text instead of synthetic vectors. Writes
`results/summary_embedding.json` and `results/summary_embedding.md`.

**Result:** Completed cleanly. Candidate ranking preserved — importance_weighted
again the only Pareto-strong decay strategy (nDCG 0.703, recall 0.782, 30d+ 0.459,
stale-hit 0.059, false-forget 0.6) vs pure-recency (recall 0.43–0.49, false-forget
25–28) and no_decay (stale 0.151, 439 retained). Absolute recall lower than
synthetic (embeddings make fresh-vs-stale harder), confirming the §7 targets are
backend-sensitive. A first-run torch/numpy-2 ABI crash was fixed by pinning
numpy<2 (documented in findings/).

---

### sweep_params.py --seeds 5

**Status:** PASS

**Description:** Sensitivity sweeps over exp_recency (base, gamma), ttl_halflife
(half-life), importance_weighted (imp_factor), ebbinghaus (s0, reinforce_gain).
Writes `results/sensitivity.json` and `results/sensitivity.md`.

**Result:** Completed cleanly. Trade-offs are monotone with no cliffs.
`imp_factor` has a clean knee at 4–8 (nDCG 0.87 -> 0.93 as stale-hit rises 0.110
-> 0.133). `ebbinghaus.reinforce_gain` is nearly flat (nDCG spread 0.080, best at
gain=0) — strength reinforcement adds little; motivates deferring the `strength`
field.

---

### Determinism + sync/async parity smoke test

**Status:** PASS

**Description:** Same seed yields identical episode ids and vectors; `run_simulation`
vs `arun_simulation` and `generate_stream` vs `agenerate_stream` produce identical
results. Verified same-topic cosine ~0.905 vs cross-topic ~-0.008 (relevance
geometry sane).

**Result:** All assertions passed.

---

### Lint / type

**Status:** PASS

**Description:** `ruff format`, `ruff check`, and `mypy` run directly on
`benchmarks/memory_decay/` (py39, line-length 120 — same config as the repo).
Repo-wide `./scripts/format.sh` and `./scripts/validate.sh` also run to confirm no
regression (they scope `libs/**` and `cookbook/` only, not `benchmarks/`).

**Result:** See the final validation section in the task summary.
