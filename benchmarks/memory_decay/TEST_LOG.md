# TEST_LOG — Episodic Decay Harness (SM-R5)

Environment: dedicated venv `benchmarks/memory_decay/.venv` (Python 3.9, numpy
2.0.2). No external services. Deterministic given seeds.

---

### run_benchmark.py --seeds 5

**Status:** PASS

**Description:** Runs all five candidate decay strategies over 5 seeds (90-day
horizon, ~446 episodes / ~192 queries per seed), ranking-only and prune-enabled.
Writes `results/summary.json` and `results/summary.md`.

**Result:** Completed cleanly. importance_weighted is Pareto-strong: recall@k
0.939 (0.828 at 30d+) with stale-hit-rate 0.088 (vs no_decay 1.000 / 0.147) and
**0 false-forgets** when pruning. Pure-recency candidates (exp_recency,
ttl_halflife, ebbinghaus) suppress stale (~0.03) but over-forget old valid facts
(30d+ recall 0.18–0.43) and false-forget 22–29 memories under pruning. Basis for
DECISION.md.

---

### sweep_params.py --seeds 5

**Status:** PASS

**Description:** Sensitivity sweeps over exp_recency (base, gamma), ttl_halflife
(half-life), importance_weighted (imp_factor), ebbinghaus (s0, reinforce_gain).
Writes `results/sensitivity.json` and `results/sensitivity.md`.

**Result:** Completed cleanly. Trade-offs are monotone with no cliffs.
`imp_factor` has a clean knee at 4–6 (nDCG 0.86 -> 0.93 as stale-hit rises 0.088
-> 0.122). `ebbinghaus.reinforce_gain` is nearly flat (nDCG spread 0.056) —
strength reinforcement adds little; motivates deferring the `strength` field.

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
