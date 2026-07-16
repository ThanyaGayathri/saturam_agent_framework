# TEST_LOG — Memory Consolidation Benchmark (SM-R7)

Environment: dedicated venv `.venv` (python 3.12), egpt installed `-e ../../libs/agno`,
`openai` + `tiktoken`. LLM: Azure OpenAI Responses API, deployment `gpt-5.4`.
LLM mode `auto` warms `results/llm_cache/`; `replay` reproduces offline.

---

### tests/test_harness.py (offline self-tests, scripted fake LLM)

**Status:** PASS

**Description:** Exercises the full pipeline with no network/credentials:
all five strategies run over the synthetic set; provenance + bi-temporal UPDATE
(supersede, union sources); DELETE retires without erasing; label metrics on a
constructed "perfect" memory return 1.0 across recall/conflict/provenance.

**Result:** All assertions pass. Confirms plumbing, provenance/bi-temporal ops,
and metric wiring independent of the LLM.

---

### Azure Responses connectivity (single live call)

**Status:** PASS

**Description:** One live `gpt-5.4` call through the `AzureOpenAIResponses`
subclass (Responses API) returning token usage + latency.

**Result:** Response returned; usage/latency/cost accounted. Confirms the
Responses-API-over-Azure path and the cost/latency accounting.

---

### run.py --dataset synthetic --qa (all strategies)

**Status:** PASS

**Description:** All five strategies over the labelled synthetic stream (17
episodes, 6 sessions, 12 targets incl. 3 conflicts), with LLM assertion judge +
QA. Metrics in `results/synthetic_results.json`.

**Result:** Recall/conflict/QA saturate at 1.0 (small-corpus + strong reader).
Store-quality discriminates: mem0 best dedup (0.80) and best distiller provenance
precision (0.85) and smallest footprint; hierarchical worst provenance precision
(0.35, the summarization failure mode). See RESEARCH.md §5.1.

**Reproducible:** `HARNESS_LLM_MODE=replay python run.py --dataset synthetic --qa`
returns identical numbers from cache (deterministic merge prompts).

---

### run.py --dataset locomo (sample 0, first 4 sessions, qa-limit 15)

**Status:** PASS (ran to completion; `results/locomo_results.json`)

**Description:** External-validity + scale. 76 episodes ingested from 4 sessions.
Footprint/compression + QA + cost per strategy.

**Result:** Compression is the scale signal — mem0 distils 76 episodes to 29
atomic facts / 452 memory tokens (5.9x smaller than baseline's 2655, smaller than
the summary's 773). QA is coverage-limited (0.33-0.40, within one question) because
probes span all 19 sessions while only 4 were ingested — LOCOMO does not
discriminate consolidation quality here, but confirms no regression and sizes the
~2-3x merge cost premium. See RESEARCH.md §5.2.

---

### ./scripts/format.sh && ./scripts/validate.sh (repo dev venv)

**Status:** PASS

**Description:** Repo gates. `validate.sh`: ruff check + mypy clean on agno (904
files), agnoctl (20), cookbook. `format.sh`: all files already formatted (no
changes). Both scripts scope to agno/agnoctl/agno_infra/cookbook, not benchmarks/;
benchmark files were linted directly with the repo-pinned ruff 0.15.20
(line-length 120) — clean, mirroring cookbook's ruff+run treatment.

**Result:** Both pass; no files outside `benchmarks/` modified.
