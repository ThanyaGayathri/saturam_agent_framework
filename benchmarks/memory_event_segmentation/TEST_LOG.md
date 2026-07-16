# TEST_LOG — Event Segmentation Harness (SM-R6)

Environment: dedicated venv `benchmarks/memory_event_segmentation/.venv`
(Python 3.9, numpy pinned <2 for torch ABI). Offline modes: no services, no keys,
bit-reproducible given seeds. Embedding mode: local `all-MiniLM-L6-v2`
(downloaded once, then offline). LLM mode: real Azure OpenAI Responses (gpt-5.4
deployment) via gitignored `.env`. SLM mode: local llama3 8B via ollama.

---

### run_benchmark.py --seeds 5 (synthetic embeddings)

**Status:** PASS

**Description:** All 8 candidates over 5 seeds (~100 events / ~11 true episodes
per stream), boundary tolerance ±1. Writes `results/summary.{json,md}`.

**Result:** Completed cleanly. On clean synthetic vectors `embedding_drift` (F1
0.970) and `hybrid_gap_drift` lead, `surprise_em_llm`/`hybrid_gap_emllm` 0.846,
`time_gap` 0.691 (precision 1.000), `fixed_window` 0.271; naive baselines bracket
the range (`single_session` 0.0, `per_turn` 0.161). Synthetic vectors are
idealised — see the embedding run for the trustworthy ranking.

---

### run_benchmark.py --seeds 5 --relevance embedding (real embeddings)

**Status:** PASS

**Description:** Confirmation run using real `all-MiniLM-L6-v2` text embeddings.
Writes `results/summary_embedding.{json,md}`.

**Result:** Completed cleanly. **The semantic ranking changes.** `embedding_drift`
collapses (F1 0.970 → 0.682, over-segments to 17 vs ~11) while `surprise_em_llm`
holds (0.724) and **`hybrid_gap_emllm` is the best local method** (F1 0.799, Pk
0.130, recall 0.939). `time_gap` is unchanged (0.691, precision 1.000 — it uses no
embeddings). Basis for DECISION.md §3.

---

### run_benchmark.py --seeds 5 --llm --llm-provider azure_responses (gpt-5.4)

**Status:** PASS

**Description:** Real Azure OpenAI Responses call, one per stream (gpt-5.4
deployment, labelled gpt-5.5 per repo policy). Writes `results/summary_llm.{json,md}`.

**Result:** Completed cleanly. `llm_boundary` is the quality ceiling: F1 0.871,
precision 0.822, recall 0.933, Pk 0.080 — above every local method on realistic
signal — at ~2.5 s and ~$0.009 per session (in≈1721 / out≈30 tokens). Reserved
for high-value sessions on the async lane.

---

### run_benchmark.py --seeds 3 --llm --llm-provider ollama (on-prem SLM)

**Status:** PASS

**Description:** Real local llama3 8B (ollama) call, one per stream. Writes
`results/summary_slm.{json,md}`.

**Result:** Completed (3 seeds). **~338 s per session on CPU** (a single
~1.6k-token prompt) and **poor quality (F1 0.319, precision 0.225, recall 0.705,
Pk 0.482)** — below even `time_gap`. Untenable inline; GPU-batched deployment with
a stronger model only. Documents the on-prem alternative for SM-R1. (A first
attempt hit the 600 s per-request cap during model cold-load; raised to 1800 s and
warmed the model — real per-call latency ~338 s.)

---

### sweep_params.py --seeds 5 (sensitivity + robustness)

**Status:** PASS

**Description:** Threshold sweeps (time-gap seconds, fixed-window size,
embedding-drift `threshold_c`, EM-LLM `gamma`/objective/refine) and a noise sweep
(clean→high). Writes `results/sensitivity.{json,md}` and `results/robustness.{json,md}`.

**Result:** Completed cleanly. `time_gap` flat for ≥300 s (idle-gap-only regime,
above the ~180 s within-session cadence). EM-LLM `gamma` flat over 0.5–1.5 (robust);
**graph refinement helps** (Pk 0.136 → 0.096 refine off→on); `modularity` beats
`conductance` on Pk. Under rising noise, the hybrid is the most stable useful
method; pure `embedding_drift`/`surprise_em_llm` over-segment.

---

### Determinism + sync/async parity + metric sanity smoke test

**Status:** PASS

**Description:** Same seed yields identical events/vectors; `generate_stream` vs
`agenerate_stream` and every segmenter's `segment` vs `asegment` are identical; Pk
/ WindowDiff / F1 stay in [0,1] with correct known cases; embedding geometry sane.

**Result:** All assertions passed. Same-topic cosine 0.864 vs cross-topic -0.049
(topic signal clean). Stream determinism, segmenter sync/async parity, and metric
bounds all verified.

---

### Lint / type

**Status:** PASS

**Description:** `ruff format --check`, `ruff check`, and `mypy` (py39,
line-length 120 — the repo's ruff config from `libs/agno/pyproject.toml`) run on
`benchmarks/memory_event_segmentation/` (all 11 source files).

**Result:** `ruff format`: 11 files already formatted. `ruff check`: All checks
passed. `mypy --ignore-missing-imports`: Success, no issues in 11 source files.
The repo scripts `./scripts/format.sh` / `./scripts/validate.sh` only lint
`libs/**` and `cookbook/` (never `benchmarks/`) and require the dev venv (`uv` +
editable framework install), which is not set up here; no `libs/**` or `cookbook/`
file was touched by this task, so there is nothing in their scope to regress.
