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
0.937, idle-30d+ decay-resistance recall 0.362 (vs peers 0.007–0.130), prune
precision 1.000, post-prune recall 0.937, **0 false-forgets**. Pure-recency
candidates suppress stale (~0.03–0.05) but over-forget (idle-30d+ 0.007–0.13) and
false-forget 21–28 memories under pruning (post-prune recall collapses to
0.54–0.69). Basis for DECISION.md. (v2: metrics corrected per review — idle-age
clock, need-based prune precision, post-prune recall, versioned stale-hit.)

---

### run_benchmark.py --seeds 5 --relevance embedding (real embeddings)

**Status:** PASS

**Description:** Confirmation run using real `all-MiniLM-L6-v2` embeddings of the
episode/query text instead of synthetic vectors. Writes
`results/summary_embedding.json` and `results/summary_embedding.md`.

**Result:** Completed cleanly. Candidate ranking preserved — importance_weighted
again the only Pareto-strong decay strategy (nDCG 0.703, recall 0.782, stale-hit
0.059, prune precision 0.996, post-prune recall 0.782, false-forget 0.6) vs
pure-recency (recall 0.43–0.49, false-forget 25–28) and no_decay (stale 0.151, 439
retained). Absolute recall lower than synthetic (embeddings make fresh-vs-stale
harder), confirming the §7 targets are backend-sensitive. A first-run
torch/numpy-2 ABI crash was fixed by pinning numpy<2 (documented in findings/).

---

### confound.py --seeds 5 (importance de-circularization)

**Status:** PASS

**Description:** Decorrelates importance from kind (`importance_noise` 0→1) and
re-runs the comparison, to test the oracle-importance confound raised in review.

**Result:** importance_weighted stays ahead of pure-recency peers at every noise
level (recall margin +0.219 → +0.307), but the *salience-targeting* advantage
(idle-30d+ margin) shrinks ~65% (0.232 → 0.080) as importance decorrelates — i.e.
the robust part is a longer average half-life, the targeting part needs a
calibrated importance scorer. Feeds the SM-R1 calibration gate.

---

### ablations.py --seeds 5 --embedding (prune-gate + reinforcement)

**Status:** PASS

**Description:** (a) recency-only vs the literal §4 three-condition prune gate;
(b) all-top-k vs valid-only reinforcement under synthetic + embeddings.

**Result:** (a) The three-condition gate is *worse* for the chosen strategy —
prune-recall 0.394→0.101, retained 292→402 (≈ no-prune) because it can't shed
FOREVER-valid noise; §4 rewritten to the recency-floor rule. (b) valid-only
reinforcement beats all-top-k on every axis (importance_weighted synthetic recall
0.937→0.962, stale 0.110→0.092, idle-30d+ 0.362→0.642; embedding similar); §3
flipped to reinforce-on-confirmed-use.

---

### significance.py --seeds 5 (synthetic + embedding)

**Status:** PASS

**Description:** Paired bootstrap over pooled per-query values, Holm-corrected, on
the comparisons the decision leans on. Uses `harness/stats.py` (adapted from
SM-R4's stats module).

**Result:** importance_weighted significantly beats every pure-recency peer on
recall and nDCG under both backends (Δ +0.22 to +0.39, all p<0.01); no_decay
significantly beats it on raw recall/nDCG (the expected decay cost). The
decay-group ranking is not seed noise.

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
