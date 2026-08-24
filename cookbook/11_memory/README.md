# Agent Memory

This section demonstrates how EGPT agents persist and use user memories across runs, sessions, and agents.

## Directory Layout

- `01_agent_with_memory.py` to `08_memory_tools.py`: Core memory patterns with agents.
- `09_memory_provider_seam.py`: The Symbiotic Memory seam (SM-C1) -- read/write/consolidate over
  both substrates, with the backend and deployment mode chosen by config. Runs with no database and
  no API key.
- `10_graph_substrate_neo4j.py`: The same seam over a **real** temporal knowledge graph (SM-G1) --
  tenant partitioning, bi-temporal invalidation, and point-in-time reads against Neo4j. Needs
  `./cookbook/scripts/run_neo4j.sh`; no API key.

- `11_memory_tiers.py`: The CoALA tier model (SM-C2) -- what the framework decides because of a
  record's tier: write routing, graph write shape, read tier-selection, which tiers a deployment
  serves, and how the six `learn` stores map in. Also runs with no database and no API key.
- `12_episode_representation.py`: The unit of episodic memory (SM-E1) -- how a write-back event
  becomes the stored episode row, how that row's link to its graph episode node is derived rather
  than chosen, and what a reader gets back. Runs with no database and no API key.
- `13_vector_substrate_pgvector.py`: The same seam over a **real** vector store (SM-V1) -- one
  partition and one HNSW index per tenant, scope isolation down to the user slot, and similarity
  scores in `[0.0, 1.0]` against Postgres + pgvector. Needs `./cookbook/scripts/run_pgvector.sh`;
  no API key (the embedder is the offline `bge-base` ADR 0003 ratifies for Mode A, downloaded on
  first run). It names no embedder at all -- `mode` selects it, per `16_`.
- `14_hybrid_candidates_pgvector.py`: Two ranked candidate lists from that one store (SM-V3) --
  dense cosine and Postgres full-text, returned **unfused** so a fusion stage can still see the
  per-leg ranks. Shows the legs disagreeing on length, order, and top hit. Fusing them is SM-H2
  (#132); ADR 0003 section A.6 ratifies weighted fusion for this two-leg case and holds the
  three-leg choice open. Same setup as `13_`.
- `15_scoped_and_windowed_recall.py`: Scoped, time-windowed recall over the same store (SM-V4) --
  tenant, user, tier and a valid-time window all applied **inside** the search rather than to the
  page it returns, and why a window is not a created-at filter: an open-ended memory keeps
  answering until something bounds it. Same requirements as `13_`.
- `16_memory_embedding_pipeline.py`: What fills the embedder-shaped hole in `13_` (SM-V2) -- which
  model each mode selects and at what dimension, why Mode A **refuses** an embedder that reaches
  the network (ADR 0003 A.3c), the version tag stamped on every stored vector, and the single
  embedder shared by the write and query paths. No database and no API key; downloads `bge-base`
  at the end to resolve the real Mode A default.
- `17_entity_extraction_graph.py`: Entity extraction into temporal-graph nodes (SM-G2) -- where the
  pass runs (the consolidation lane, never a read), what a candidate carries (type, provenance back
  to the interaction, a 768-d vector), and which duplicates are deliberately left for SM-G8's entity
  resolution. Needs `./cookbook/scripts/run_neo4j.sh`, `./cookbook/scripts/run_pgvector.sh`, and an
  `OPENAI_API_KEY` -- extraction is one model call per interaction.
- `18_episodic_subgraph.py`: The episodic subgraph (SM-G6) -- raw non-lossy episodes as graph nodes
  linked to the entities they mention, recalled by valid-time window and by mention link. Needs
  `./cookbook/scripts/run_neo4j.sh`; no API key. The entity names are supplied on the event:
  extracting them from content is SM-G2's (see `17_`). Requires `.venv`, not `.venvs/demo` --
  `demo_setup.sh` does not install the `neo4j` driver.
- `19_relation_extraction_graph.py`: Relation/fact edge extraction into typed graph edges (SM-G3) --
  `(source)-[relation]->(target)` between the entity nodes `17_`'s pass creates, provenanced back to
  the interaction. Same consolidation-lane placement as `17_`, wired alongside it with a second
  `relation_pipeline` option. Needs `./cookbook/scripts/run_neo4j.sh`,
  `./cookbook/scripts/run_pgvector.sh`, and an `OPENAI_API_KEY`. Reads the edges back through
  `graph.recall_facts` (SM-G7), the read `19_` used to reach `graph._driver` directly for.
- `20_entity_resolution_graph.py`: Entity resolution (SM-G8) -- folding "Acme" and "Acme
  Corporation" into one node via `EntityResolutionPipeline`, re-pointing their `MENTIONS` edges and
  keeping the losing name as an alias. No API key; needs `./cookbook/scripts/run_neo4j.sh` and a
  local embedder (`sentence-transformers`, installed by `.venvs/demo`).
- `21_episode_importance.py`: Episode salience scoring (SM-E5) -- an episode's content scored
  1-10 (Generative Agents' poignancy scale) and normalized to the `[0, 1]` range
  `Decaying.importance` already validates, then applied to an already-built episode with
  `dataclasses.replace`. Needs a real model (`OPENAI_API_KEY`); there is no offline path for
  LLM-assigned salience.
- `22_episodic_relevance_scoring.py`: Episodic relevance scoring (SM-E6) -- query-vs-episode
  cosine similarity as its own signal, computed independently of a live `VectorSubstrate.search`
  round trip so a candidate reaching composite scoring by another path (graph traversal, a scan)
  can still be scored. `cosine_relevance` on precomputed embeddings and `episode_relevance` on raw
  text; recency/importance/composite weighting are SM-E4/SM-E5/SM-E7's. Runs with no database and
  no API key -- a tiny deterministic bag-of-hashed-tokens embedder, not a real model.
- `23_consolidation_worker.py`: The async consolidation worker (SM-L1) -- draining `consolidate`
  off the hot path across a tier larger than one bounded pass, via the provider's internal
  resumption cursor; retrying a failed pass safely (idempotent upsert by content-derived id); and
  running as a background task on an interval. Vector-only, so it runs with no database and no API
  key.
- `24_episodic_segmentation.py`: The SM-R6 v1 inline segmentation prior at write-back (SM-E3) --
  the deterministic `time_gap` + `max_turns_per_episode` cut, suppressed below
  `min_turns_per_episode`, that tags each write-back event with which boundary rule (if any)
  opened a new episode. Shows a two-topic session segmenting in one call and across two write-back
  calls via `SegmentState`, and the hard turn cap firing with no idle gap. The async
  re-segmentation lane (embedding-similarity drift, EM-LLM, LLM) is SM-R7's, not shown here. Runs
  with no database and no API key.
- `25_semantic_subgraph.py`: The semantic subgraph (SM-G7) -- resolved `(:Entity)` nodes and the
  `(:Entity)-[:REL]->(:Entity)` fact edges between them, recalled by entity, by relation label, and
  by valid time, and shown as a subgraph distinct from `18_`'s episodic one over the same scope.
  Needs `./cookbook/scripts/run_neo4j.sh`; no API key -- entities and fact edges are written
  directly, the same way `18_` writes episodes without SM-G2's extraction pass. Requires `.venv`,
  not `.venvs/demo`, for the same reason `18_` does.
  **Renumbered from `20_` to `23_` to `24_` to `25_` across three merges** -- `feat/SM-G8` took
  `20_entity_resolution_graph.py` first, then `feat/SM-E5`/`feat/SM-E6` took `21_`/`22_`, then
  `feat/SM-L1` took `23_consolidation_worker.py`, then `feat/SM-E3` took `24_episodic_segmentation.py`
  while this branch was still catching up. Per this file's own convention, whoever merges second
  renumbers -- three times, this time.
- `26_episodic_composite_scoring.py`: Composite episodic scoring (SM-E7) -- relevance (`22_`),
  recency (`14_`) and importance (`21_`) combined with `composite_episodic_score` into one
  weighted-sum ranking score. Shows the default weights (`recency=0.0`, per ADR 0003 B.1: no
  additive per-query hot-path recency rescore -- recency is a write-time prior instead), a
  write-time weight set that re-enables it, and a four-episode fixture ranked in order. Wiring the
  composite into live retrieval is SM-H5's (#135). Runs with no database and no API key.

- `27_episode_forgetting.py`: Episode forgetting/pruning (SM-E9) -- the SM-R5-ratified
  recency-floor gate applied to a mixed batch of episodes at different ages and importances,
  showing the importance-weighted half-life keep a high-importance stale episode alive past where
  a low-importance one at the same age is already archived. Archival is row-level `expired_at`
  (invalidate-not-delete) on detached copies; the originals passed in are never mutated. Runs with
  no database and no API key. **Renumbered from `26_` to `27_`: SM-E7 (#109) took `26_` first.**

- `28_multihop_graph_traversal.py`: Anchor-seeded multi-hop traversal (SM-G9) -- `traverse` scores
  a fact edge reached by walking a bounded number of `REL` hops from an entity the query text
  names, surfacing a fact the query shares no words with at all. Shows the hop bound is set at
  substrate construction (`max_traverse_hops`), not per call, and that a bare `upsert_facts`
  endpoint (no explicit `upsert_entities`) is still a valid anchor. Needs
  `./cookbook/scripts/run_neo4j.sh`; no API key -- entities and fact edges are written directly, the
  same way `25_` does. Runs with `.venvs/demo`.
  **Renumbered from `26_` to `28_`: `feat/SM-E7` (#109) and `feat/SM-E9` (#112) both merged to
  `main` first and took `26_`/`27_` while this branch was still catching up.**

- `29_near_duplicate_fact_dedup.py`: Near-duplicate fact folding during consolidation (SM-L4) --
  two episodes that are the same fact once normalized ("the deploy failed!" / "the deploy
  failed") but that `_distil`'s raw token-overlap grouping treats as distinct, folded by
  `consolidation.dedup.merge_near_duplicate_facts` immediately after `_distil` runs. Shows
  provenance kept from every source episode, the bi-temporal window widened to the earlier bound
  either member carries, topics unioned, and `ConsolidationReport.near_duplicate_facts_folded`
  (facts, this stage) reported separately from `duplicates_merged` (episodes, `_distil`'s own
  grouping). Vector-only, so it runs with no database and no API key.

- `30_episodic_contiguity_retrieval.py`: Temporal-contiguity retrieval (SM-E8) --
  `temporal_neighbors` names a hit's +/- n neighbors in temporal order (occurred_at, falling back
  to created_at), `ContiguityBuffer` accumulates them across a pass in a bounded, deduplicating
  FIFO, and `blend_contiguity`/`contiguity_bonus` turn a buffered neighbor's distance from its
  hit into a bonus on top of its own `composite_episodic_score` (`26_`). A six-episode fixture
  shows an unrelated episode outranking the hit's own temporal continuation on relevance alone,
  and the ranking reversing once the continuation's contiguity bonus is blended in. Wiring
  contiguity into live retrieval is SM-H5's (#135). Runs with no database and no API key.
  **Renumbered from `29_` to `30_`: `feat/SM-L4` took `29_near_duplicate_fact_dedup.py` first.
  Per this file's own convention, whoever merges second renumbers.**

- `31_semantic_fact_store.py`: The semantic fact store (SM-S1) -- `SemanticFactStore` dual-writes a
  batch of `FactWrite`s to both substrates from one call, and this shows the acceptance criterion
  directly: a fact retrieved through `VectorSubstrate.search` and the same fact retrieved through
  `GraphSubstrate.recall_facts` carry the same subject/predicate/object/confidence/derived_by,
  because `egpt.memory.schemas.semantic.fact_record` projects them identically onto both legs. Also
  shows an untripled fact (no SPO) storing fine on the vector leg alone. Fact *extraction* from raw
  episodes is SM-L3's (#124), not shown here -- the `FactWrite`s are handed to the store directly.
  Needs both `./cookbook/scripts/run_pgvector.sh` and `./cookbook/scripts/run_neo4j.sh`; no API key.
  Runs with `.venvs/demo`.
  **Renumbered from `29_` to `30_` to `31_`: `feat/SM-L4` took `29_near_duplicate_fact_dedup.py`
  first, then `feat/SM-E8` took `30_episodic_contiguity_retrieval.py` while this branch was still
  catching up. Per this file's own convention, whoever merges second renumbers -- twice, this
  time.**

- `32_fact_extraction_merge.py`: Fact extraction + merge decision (SM-L3) -- `_distil`'s
  deterministic dedup (SM-C1) replaced by `FactExtractionPipeline`, opt-in via `fact_pipeline`: a
  model extracts atomic candidate facts, compares each against the top-k most similar existing
  facts, and picks ADD/UPDATE/DELETE/NOOP. The second `consolidate()` call is the point -- a
  changed circumstance retires the old fact (`expired_at`/`superseded_by`, transaction time) rather
  than leaving two contradictory "current" facts standing. Needs `./cookbook/scripts/run_neo4j.sh`,
  `./cookbook/scripts/run_pgvector.sh`, and an `OPENAI_API_KEY` -- extraction and the merge decision
  are each a model call.
  **Renumbered from `29_` to `32_`: `feat/SM-L4` took `29_near_duplicate_fact_dedup.py`, then
  `feat/SM-E8` took `30_episodic_contiguity_retrieval.py`, then `feat/SM-S1` took
  `31_semantic_fact_store.py`, all while this branch was still catching up. Per this file's own
  convention, whoever merges second renumbers -- three times, this time.**

- `33_reflection.py`: Importance-gated insight synthesis (SM-L6) -- `ReflectionPipeline` accumulates
  episode importance (SM-E5) per scope across passes, reusing SM-L2's `SizeTrigger` policy, and once
  the accumulation crosses a threshold asks an `LLMInsightSynthesizer` to distill the cluster into
  one insight or decline. A synthesized insight becomes a `FactWrite` citing every source episode
  and lands on the SEMANTIC tier through the same write path a deduped fact uses -- retrievable
  through an ordinary read. Requires a real model (`OPENAI_API_KEY`); no database.
  **Renumbered from `29_` to `33_`: `feat/SM-L4` took `29_near_duplicate_fact_dedup.py`, then
  `feat/SM-E8` took `30_episodic_contiguity_retrieval.py`, then `feat/SM-S1` took
  `31_semantic_fact_store.py`, then `feat/SM-L3` took `32_fact_extraction_merge.py`, all while this
  branch was still catching up. Per this file's own convention, whoever merges second renumbers --
  four times, this time.**

- `34_sleep_time_scheduling.py`: Sleep-time compute scheduling (SM-L9) -- `SleepTimeScheduleConfig`
  bundles a daily `SleepWindow` (evaluated in a named timezone, not just UTC), an idle threshold,
  and `mode` (Mode A/`LOCAL` by default). `build_sleep_time_trigger` wraps a fresh `IdleTrigger` so
  the resulting trigger only fires inside the window, while `record_event` keeps accumulating
  pending work at any hour. Plugs into `ConsolidationWorker` as `trigger=...`, no worker change.
  Requires nothing; no database, no API key, no network.

- `35_hierarchical_summarization.py`: Hierarchical (session/day/global) summarization (SM-L8) --
  `SummaryPipeline`, opt-in via `summary_pipeline`: a rolling session summary reuses
  `SessionSummaryResponse` (`session/summary.py`); day/global periodic rollups are gated by
  `LatencyTrigger` and folded via `SummarizeStrategy` (`memory/strategies/summarize.py`). Every
  summary lands on the SEMANTIC tier through the same write path a distilled fact or a reflected
  insight uses, tagged `metadata={"kind": "summary", "level": ...}` -- retrievable through an
  ordinary read. Requires a real model (`OPENAI_API_KEY`); no database.
  **Renumbered from `34_` to `35_`: `feat/SM-L9` took `34_sleep_time_scheduling.py` while this
  branch was still catching up. Per this file's own convention, whoever merges second renumbers.**

- `36_hybrid_retrieval_orchestration.py`: Feed-forward retriever orchestration (SM-H1 #131) --
  `read` fans out through `egpt.memory.retrieval.orchestrate`/`aorchestrate` (vector+BM25, and the
  graph leg), with the lexical leg present in `MemorySlice.fusion["lexical_hits"]` but not
  affecting the default `score_sum` ranking. `41_rrf_fusion.py` shows the opt-in strategy (SM-H2
  #132) that does fold it in. No database, no model, no network.

- `37_procedural_memory.py`: Procedural memory (SM-S2) -- `ProceduralReflectionPipeline` is
  importance-gated like `ReflectionPipeline`, but stateless across calls: PROCEDURAL takes no part
  in `consolidate`'s scan (`TIER_POLICY` reserves that sweep to EPISODIC/SEMANTIC), so it is
  invoked directly over one episode batch. Once an `LLMProcedureSynthesizer` distills a reusable
  rule/skill/prompt fragment, it becomes a plain `MemoryEvent` on the PROCEDURAL tier -- vector-only,
  so the ordinary `write` needs no new routing -- and is retrieved and injected through an ordinary
  `read` with no tier named, since PROCEDURAL is in `PERSISTENT_TIERS`. Requires a real model
  (`OPENAI_API_KEY`); no database.
  **Renumbered from `35_` to `37_`: `feat/SM-L8` took `35_hierarchical_summarization.py` and
  `feat/SM-H1` took `36_hybrid_retrieval_orchestration.py` (the latter's own README entry appears
  to be missing on `main`, not added here) while this branch was still catching up. Per this
  file's own convention, whoever merges second renumbers.**

- `38_kb_and_memory_unified_retrieval.py`: The KB vs memory boundary (SM-S4) -- `unified_read()`
  fans one query out across a `MemoryProvider` and a `KnowledgeRetrievable` leg without merging
  the two stores, labeling every result `RetrievalSource.KB` or `RetrievalSource.MEMORY`. A leg
  with no store wired returns an empty list, not an absent one; a raising leg does not silently
  discard the other's result. No database, no API key.
  **Renumbered from `34_` to `38_`: `feat/SM-L9` took `34_sleep_time_scheduling.py`,
  `feat/SM-L8` took `35_hierarchical_summarization.py`, `feat/SM-H1` took
  `36_hybrid_retrieval_orchestration.py`, and `feat/SM-S2` took `37_procedural_memory.py`, all
  while this branch was still catching up. Per this file's own convention, whoever merges second
  renumbers -- four times, this time.**

- `39_working_memory.py`: The WORKING tier's own buffer (SM-C6) -- `WorkingMemory` holds a run's
  context messages, scratchpad and goals, and is never itself persisted (`TIER_POLICY[WORKING]`
  routes to no substrate, and a direct `write_tier(WORKING, ...)` is still refused). Grows past
  `max_messages`, offloads by reusing `SessionSummaryManager`, and -- given a `MemoryProvider` --
  writes the overflow through as ordinary EPISODIC events before summarizing it away, which is the
  boundary crossing into long-term memory. `provider.assemble_context()` then renders the buffer
  next to a persistent-tier read, the WORKING half of SM-H6's future retrieval assembly. Requires
  nothing; no database, no API key, no network (a minimal `Model` double stands in for
  summarization, the same one `test_working_memory.py` uses under test).
  **Renumbered from `35_` to `37_` to `39_`: `feat/SM-L8` took `35_hierarchical_summarization.py`,
  `feat/SM-H1` took `36_hybrid_retrieval_orchestration.py`, then `feat/SM-S2` took
  `37_procedural_memory.py` and `feat/SM-S4` took `38_kb_and_memory_unified_retrieval.py` while
  this branch was still catching up. Per this file's own convention, whoever merges second
  renumbers -- twice, this time.**

- `40_user_profile_store.py`: Structured user profile updated on consolidation (SM-S3) --
  `UserProfileStore`'s storage-free `extract_profile`/`aextract_profile` (a model call, no `db`)
  wired into `CoLocatedMemoryProvider` via `profile_store=`, same opt-in shape as
  `entity_pipeline`/`relation_pipeline`/`fact_pipeline`. Consolidation runs the extraction over the
  pass's episodes and persists the result through `SemanticProfileStore` -- tenant/user-scoped
  through the vector substrate, not the seed store's own single-tenant `self.db`. Shows a stated
  preferred name surviving one consolidation pass. Requires `./cookbook/scripts/run_pgvector.sh`
  and a real model (`OPENAI_API_KEY`).
  **Renumbered from `34_` to `40_`: `feat/SM-L9` took `34_sleep_time_scheduling.py`,
  `feat/SM-L8` took `35_hierarchical_summarization.py`, `feat/SM-H1` took
  `36_hybrid_retrieval_orchestration.py`, `feat/SM-S2` took `37_procedural_memory.py`,
  `feat/SM-S4` took `38_kb_and_memory_unified_retrieval.py`, and `feat/SM-C6` took
  `39_working_memory.py`, all while this branch was still catching up. Per this file's own
  convention, whoever merges second renumbers.**

- `41_rrf_fusion.py`: Reciprocal Rank Fusion (SM-H2 #132) -- the opt-in alternative to
  `36_`'s `score_sum` default: `config.fusion={"method": "rrf", "k": ...}` fuses all three legs,
  including lexical, by rank rather than score, so it never needs the legs to share a scale. It
  does not become the default -- ADR 0003 A.6 holds the 3-leg weighted-vs-RRF choice open, gated
  on SM-G9's (#107) real graph retriever and SM-H2/H3's own measurement. Same "cat dog" scenario
  `36_` uses, shown ranking differently
  under `score_sum` vs `rrf`, plus `k`'s effect on the fused score. No database, no model, no
  network.

- `memory_manager/`: Direct `MemoryManager` API examples using PostgreSQL.
- `optimize_memories/`: Memory optimization strategy examples.

> SurrealDB memory manager examples are now located in `cookbook/integrations/surrealdb/`.

## Setup

### 1. Create a virtual environment

```shell
python3 -m venv ~/.venvs/aienv
source ~/.venvs/aienv/bin/activate
```

### 2. Install dependencies

```shell
pip install -U psycopg sqlalchemy openai
```

### 3. Run an example

```shell
python cookbook/11_memory/01_agent_with_memory.py
```
