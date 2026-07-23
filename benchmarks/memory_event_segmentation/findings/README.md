# Findings for the implementation phase (SM-E3 / SM-E2 / SM-R7)

Notes captured while building the SM-R6 decision harness. **No core code was
changed in this task** — these are pointers for whoever implements the chosen
tiered segmentation. There is no `*.patch` here: no bug in `libs/agno/egpt/**`
was needed to get true numbers (the only defect found was in the harness itself,
already fixed — see below).

## Where segmentation plugs into core

- **Write-back / ingestion** — `libs/agno/egpt/memory/manager.py:377`
  `create_user_memories(messages, ...)` extracts memories per turn with **no
  boundary concept**. The inline v1 segmenter (`time_gap` + `max_turns_per_episode`)
  belongs *here*, closing/opening the provisional episode as events are appended.
  Add both sync and async variants (there is `acreate_user_memories`).
- **Message metadata is sufficient for v1.** `egpt/models/message.py` `Message`
  carries `role`, `content` and `created_at` (epoch int). `time_gap` and the
  turn-cap need only `created_at` and ordering — no new fields to *segment*; the
  new fields are on the *episode* (see DECISION.md §5), not the message.
- **Session summary is the wrong granularity.** `session/summary.py:63`
  `SessionSummaryManager` summarises a whole session; episodes are sub-session.
  Keep session summary; add episode segmentation beneath it.
- **Consolidation lane** — the async semantic re-segmenter + optional LLM pass
  belong on the SM-R7 consolidation worker (they need whole-episode embeddings —
  too heavy for the request path), and must implement the **episode-identity
  contract** (stable id + supersede/revision, DECISION.md §4).

## Modeling choices (updated after the PR #9 review rework)

- **Do NOT ship the hybrid.** The earlier draft's `hybrid_gap_emllm` was best only
  because it inherited `time_gap` precision = 1.0, which is a *gap-model artifact*.
  Under realistic mid-topic pauses the anchors are noisy and the hybrid collapses
  (F1 0.799 → 0.503). Ship **standalone `embedding_drift`** (v2 default) or EM-LLM
  (alternative). See DECISION.md §3/§8.
- **`time_gap` is a cheap prior, not a precise oracle.** Its precision is 1.0 only
  when long gaps coincide with real boundaries; inject mid-topic pauses and it
  falls to 0.31. Implement it inline as a first cut, but the async lane must be
  able to override it — do not treat its boundaries as high-precision.
- **Surprise proxy — re-test before preferring EM-LLM.** `harness/surprise.py` uses
  embedding-cosine surprise, not token NLL. EM-LLM wins on structured synthetic
  data but *loses to `embedding_drift` on real TIAGE* (0.368 vs 0.537). If SM-C
  wires a model exposing logprobs, swap in token NLL and re-run before choosing
  EM-LLM over `embedding_drift`. Graph refinement itself measurably helps
  (`results/sensitivity.md`, Pk 0.136 → 0.096) — keep it, with modularity default.
- **Trust the real dataset, not synthetic vectors.** `embedding_drift` looks
  perfect on synthetic vectors (0.970) but is 0.537 on real TIAGE dialogues; on
  synthetic vectors it is a trap. Calibrate thresholds against the **production
  embedding model + real logs**, not synthetic. The vendored TIAGE set
  (`data/tiage_test.json`) is the external cross-check to re-run against.
- **The LLM is the quality default, not a luxury.** `gpt-5.4` is the only method
  robust across hardened-synthetic (0.876) and real (0.622) data; the cheap methods
  degrade badly on real turns. Budget for the async LLM pass where segmentation
  quality matters; the cheap semantic methods are the cost-saving fallback.
- **LLM model id.** Default `gpt-5.5` via `OpenAIResponses`; measured on a
  **gpt-5.4** Azure deployment. Re-measure cost/latency on production `gpt-5.5`.
  Unit prices ($5/$15 per 1M in/out) in `harness/llm.py` are provisional.
- **Do not foreclose the on-prem SLM (Mode A).** llama3.1-8B on **CPU** with a
  single whole-conversation prompt scored F1 0.319 at ~338 s — a worst case, not a
  fair test. Re-test on **GPU with a per-window prompt** (and/or a light fine-tune)
  before concluding SLMs are unfit for segmentation.

## Env / reproducibility notes

- Dedicated venv `benchmarks/memory_event_segmentation/.venv`. `numpy` pinned `<2`
  so the same venv also satisfies torch's numpy-1.x ABI for the embedding mode.
- Offline modes are bit-reproducible given the seeds. The `embedding` mode is
  reproducible given the seeds + the sentence-transformers model. The `azure`/
  `ollama` modes call real models (latency/cost vary; boundaries are near-stable
  at temperature 0 but the cloud LLM is not guaranteed deterministic).

## Harness bug fixed during development (not core)

Early synthetic streams had the topic signal swamped by per-dimension embedding
noise at low `dim`. Fixed in `harness/stream.py` `_event_vector` by scaling the
noise by `1/sqrt(dim)` (same lesson as SM-R5). This is harness-only; no
`libs/agno/egpt/**` change was involved.
