# Findings for the implementation phase (SM-C3 / SM-R12 / SM-E4 / SM-E9)

Notes captured while building the SM-R5 decision harness. No core code was
changed in this task; these are pointers for whoever implements the chosen model.

## Where decay plugs into core

- **Retrieval** — `libs/agno/egpt/memory/manager.py:597` `search_user_memories`
  currently supports only `last_n` / `first_n` (temporal sort on `updated_at`) and
  `agentic` (LLM picks IDs). There is **no numeric relevance/recency/importance
  blend anywhere**. The chosen decay ranking is a *new* scoring path (e.g. a
  `retrieval_method="decay"` or a re-rank applied after candidate fetch). Add both
  sync and async variants.
- **Schema** — `libs/agno/egpt/db/schemas/memory.py:9` `UserMemory` has only
  `created_at` / `updated_at`. Add `last_accessed_at` (epoch s, nullable, default
  = `created_at`) and `importance` (float [0,1], nullable, default 0.5). Update
  `to_dict` / `from_dict` and the DB adapters. Migration is additive (both
  nullable) so it is back-compatible. `strength` is intentionally **deferred**
  (see DECISION.md §5).
- **Pruning** — `libs/agno/egpt/learn/curate.py:125` `Curator._filter_by_age` /
  `_keep_newest` do coarse age/count pruning today. The importance-gated prune from
  DECISION.md §4 belongs alongside these (feeds SM-E9). **Do not prune on recency
  alone** — the simulation false-forgot 22–29 still-valid facts per run when
  recency was the only gate.

## Modeling choices worth revisiting with real data

- **Access = injection into top-k.** Defined in the harness; if production logs a
  different signal (e.g. only user-confirmed-useful memories), re-check the
  reinforcement rule in SM-E4.
- **Ebbinghaus strength reinforcement added negligible benefit** in-sim (nDCG
  spread 0.056 across the whole `reinforce_gain` sweep). If a future importance
  scorer changes the dynamics, re-run the sweep before adding a `strength` column.
- **Relevance is synthetic here.** Once real embeddings are wired in, re-run
  `run_benchmark.py` with an embedding-backed relevance to confirm the
  importance_weighted advantage holds; the ranking of candidates is expected to be
  stable but the absolute stale-hit / recall numbers will move.

## No core bug found

The one bug hit during development was in the harness itself (synthetic-embedding
noise was scaled per-dimension and swamped the topic signal at dim=32); fixed in
`harness/stream.py` `_topic_vector` by scaling noise by `1/sqrt(dim)`. No patch
against `libs/agno/egpt/**` was needed, so there is no `*.patch` here.
