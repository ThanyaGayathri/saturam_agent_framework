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
- **Consolidation lane** — the async EM-LLM hybrid + optional LLM pass belong on
  the SM-R7 consolidation worker (they need whole-episode embeddings and a graph
  pass — too heavy for the request path).

## Modeling choices worth revisiting with real data

- **Surprise proxy.** `harness/surprise.py` uses an *embedding-space* Bayesian
  surprise (`1 - cos(v_i, EMA(context))`) because we have no token NLL offline.
  EM-LLM proper uses the LLM's token-level negative log-likelihood. If SM-C wires
  EM-LLM to a real model that exposes logprobs, swap the surprise array for token
  NLL and re-run the sweep — the graph-refinement stage is unchanged.
- **Graph refinement pays off — keep it.** `results/sensitivity.md` (`em_llm.refine`)
  shows Pk 0.136 → 0.096 with refinement on. Implement the full EM-LLM (surprise +
  modularity refinement), not just a surprise threshold. `modularity` beat
  `conductance` on Pk; keep modularity as the default objective but leave both in.
- **Real embeddings change the semantic ranking — trust the `embedding` run.** On
  clean synthetic vectors `embedding_drift` looks best (F1 0.970) but collapses to
  0.682 on real `all-MiniLM-L6-v2` embeddings, while EM-LLM and the hybrid hold up.
  Calibrate thresholds against the **production embedding model**, not synthetic.
- **Hybrid > standalone.** The union of deterministic time-gap anchors with the
  semantic pass (`hybrid_gap_emllm`) was the best no-API-cost method under real
  embeddings (Pk 0.130). Implement segmentation as a *composition*, not a single
  algorithm — this also makes v1→v2 a strict extension (add the semantic pass on
  the async lane; the inline anchors are unchanged).
- **LLM model id.** Harness/DECISION default to `gpt-5.5` via `OpenAIResponses`;
  the measured numbers came from a **gpt-5.4** Azure Responses deployment (the one
  available). Re-measure cost/latency on the production `gpt-5.5` deployment. Unit
  prices ($5/$15 per 1M in/out) in `harness/llm.py` are provisional.
- **On-prem SLM needs a GPU.** llama3 8B via ollama on CPU took ~365 s per session
  (a single ~1.6k-token prompt) — untenable inline, and only batchable on a GPU.
  If an on-prem-only deployment is required, budget GPU and re-measure; otherwise
  prefer the deterministic local methods and reserve the cloud LLM for flagged
  sessions.

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
