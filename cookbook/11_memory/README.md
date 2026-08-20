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
