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
