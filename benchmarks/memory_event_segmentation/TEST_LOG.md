# TEST_LOG — Event Segmentation Harness (SM-R6)

Environment: dedicated venv `benchmarks/memory_event_segmentation/.venv`
(Python 3.9, numpy <2 for torch ABI). Offline modes deterministic given seeds.
Embedding mode: local `all-MiniLM-L6-v2`. LLM mode: real Azure OpenAI Responses
(gpt-5.4 deployment) via gitignored `.env`. SLM mode: local llama3 8B via ollama.
All means reported with standard deviation.

The entries below marked **(rework)** were added/regenerated to address the PR #9
review (`paranthaman-commits`): variance, hardened data, seed equalisation, and a
real external dataset (TIAGE). See DECISION.md §8.

---

### data/fetch_tiage.py  (rework)

**Status:** PASS

**Description:** Downloads + processes the TIAGE topic-shift test split (MIT) into
`data/tiage_test.json` — 100 real PersonaChat dialogues with human-annotated
boundaries.

**Result:** 100 dialogues, 1564 turns, 315 boundaries (mean 15.6 turns / 3.15
boundaries per dialogue). Boundary mapping (label '1' -> boundary) verified against
the source; dialog 1 boundaries `[3, 8, 11, 13]` match the annotations.

---

### Generator hardening smoke test  (rework)

**Status:** PASS

**Description:** `gradual_shift_turns` + `mid_topic_pause_rate` on a separate RNG so
enabling them does not perturb segment structure (controlled comparison).

**Result:** Labels identical base-vs-hard (12 boundaries); 28 interior long-gaps
injected (so `time_gap` can now false-positive); boundary<->prev cosine rises
-0.06 -> 0.35 (boundaries genuinely harder). TIAGE loads as 100 no-timestamp
streams.

---

### run_benchmark.py --seeds 5  (synthetic vectors) / --config hard

**Status:** PASS

**Description:** All 8 candidates, synthetic vectors, base and `hard` config.
Writes `results/summary.{json,md}`, `results/summary_hard.{json,md}`.

**Result:** Base: `embedding_drift` 0.970, `em_llm`/hybrids strong. Hard (synthetic
vectors): gradual blend mildly lowers semantic F1 (drift 0.970 -> 0.950, em_llm
0.846 -> 0.799); mid-topic pauses **crater `time_gap` (0.691 -> 0.425, precision
1.0 -> 0.31)** and the hybrids (0.85 -> 0.49). Confirms reviews #1/#2.

---

### run_benchmark.py --seeds 5 --relevance embedding [--config hard] [--llm]  (rework)

**Status:** PASS

**Description:** Real `all-MiniLM-L6-v2` embeddings, base + hard, with the real
gpt-5.4 LLM. Writes `results/summary_embedding_llm.{json,md}` and
`results/summary_hard_embedding_llm.{json,md}`.

**Result:** Under real embeddings the gradual-vector blend is overwritten by real
text, so `embedding_drift` (0.682) / `em_llm` (0.724) are unchanged base-vs-hard;
the **hybrid collapses under hard (0.799 -> 0.503)** because its time-gap anchors go
noisy. **`llm_boundary` is immune to the hardening (0.877 -> 0.876)** and leads on
Pk (0.078). Basis for withdrawing the hybrid and elevating the LLM.

---

### run_benchmark.py --dataset tiage [--llm]  (rework)

**Status:** PASS

**Description:** Real TIAGE dialogues (100), real embeddings; time_gap/hybrids
skipped (no timestamps). With and without the gpt-5.4 LLM. Writes
`results/summary_tiage_llm.{json,md}`.

**Result:** Absolute F1 low and variance high (real elliptical turns are hard):
`embedding_drift` 0.537±0.26 (best no-API), `em_llm` 0.368±0.30, `per_turn`
0.343, `fixed_window` 0.252, **`llm_boundary` 0.622±0.28 (best overall)**, ~$0.002
/ dialogue. The `drift` > `em_llm` gap (~5 SE) and `llm` > `drift` gap (~2.5 SE)
are beyond noise; drives the v2 (embedding_drift default) + LLM-quality-default call.

---

### run_benchmark.py --seeds 5 --relevance embedding --llm --llm-provider ollama  (rework)

**Status:** PASS

**Description:** On-prem llama3 8B (ollama), **equalised to 5 seeds** (was 3) with
real-embedding offline peers. Writes `results/summary_embedding_slm.{json,md}`.

**Result:** F1 0.335±0.08, precision 0.259, recall 0.623, Pk 0.490 at ~340–440 s
per session on CPU (5 seeds; consistent with the earlier 3-seed 0.319). Worst-case
config (CPU, whole-conversation prompt) — documented as a Mode-A GPU re-test gate,
not a verdict (review #5).

---

### sweep_params.py --seeds 5

**Status:** PASS

**Description:** Threshold sweeps + noise robustness. Writes
`results/sensitivity.{json,md}`, `results/robustness.{json,md}`.

**Result:** EM-LLM graph refinement helps (Pk 0.136 -> 0.096); `gamma` flat;
`modularity` beats `conductance` on Pk; `embedding_drift threshold_c` best at 0.75.
Robustness: semantic methods over-segment as noise rises; the hybrid's apparent
stability does not survive the hard config.

---

### Determinism + sync/async parity + metric sanity smoke test

**Status:** PASS

**Description:** Same seed -> identical events/vectors; `generate_stream` vs
`agenerate_stream` and every segmenter's `segment` vs `asegment` identical; Pk /
WindowDiff / F1 in [0,1] with correct known cases; embedding geometry sane.

**Result:** All assertions passed. Same-topic cosine 0.864 vs cross-topic -0.049.

---

### Lint / type

**Status:** PASS

**Description:** `ruff format --check`, `ruff check`, `mypy` (py39, line-length 120,
repo ruff config) on all `benchmarks/memory_event_segmentation/` source files.

**Result:** ruff format: all files formatted. ruff check: All checks passed. mypy:
Success, no issues. The repo `format.sh`/`validate.sh` scope `libs/**` + `cookbook/`
(not `benchmarks/`) and need the dev venv (`uv` + editable framework install), not
set up here; no `libs/**`/`cookbook/` file was touched, so nothing in their scope
regresses.
