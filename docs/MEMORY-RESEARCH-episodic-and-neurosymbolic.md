# Memory Research — Episodic Memory & Neuro-Symbolic Memory (for the deferred Symbiotic Memory epic)

Cited research brief synthesizing three parallel web investigations. Feeds the **deferred**
Symbiotic Memory epic (D7 in `BACKLOG.md`) — the one that needs dedicated research before
tickets are written. Primary sources prioritized; hype flagged.

---

## 0. Terminology — read this first (three different things)

| Term | What it actually means | Relevance |
|---|---|---|
| **Episodic memory** | A memory *type* (Tulving): raw, timestamped, instance-specific **events/experiences** in temporal order — "what happened, when." Distinct from semantic (facts) / procedural (skills) / working (context). | Core to this brief. |
| **Neuro-symbolic memory** ("l") | An established field: **neural** (LLM/embeddings) **+ symbolic** (knowledge graph / logic / rules) hybrid memory. Shipping form = **KG + vector** agent memory (GraphRAG, Zep/Graphiti, Mem0g, Cognee, HippoRAG, AriGraph). | Core to this brief. |
| **Neuro-symbiotic** ("b") | **Unrelated.** Refers to brain-computer interfaces / real-time human-brain-AI coupling (NSAI). **No product/paper/vendor uses "neuro-symbiotic memory."** It is a misspelling of *neuro-symbolic*. | Excluded — not about agent memory. |
| **"Symbiotic Memory"** (our project's coined term) | *Our* name for the co-evolving **write-back + feed-forward** loop (reflect → consolidate → feed-forward). A design pattern, **not** the same as neuro-symbolic. | This brief informs it; don't conflate with neuro-symbolic. |

**Takeaway:** the two research targets are **episodic memory** (a memory type) and **neuro-symbolic
memory** (a neural+symbolic architecture). They compose well — the frontier design is *episodic
+ semantic memory backed by a neural+symbolic (vector+graph) store*, consolidated on a
background loop (which is where our "Symbiotic Memory" pattern lives).

---

## 1. Episodic memory — what it is and how stores handle it

### Definition (Tulving 1972 → CoALA 2023 → 2025 formalization)
- **Tulving:** episodic = memory for *temporally dated events and their spatio-temporal
  relations* (personally experienced, when/where/context); semantic = de-contextualized
  general knowledge. Episodes can consolidate into semantic memory over time.
- **CoALA (arXiv:2309.02427):** agent memory = working + **episodic** ("chronologically
  organized records of experiences") + semantic + procedural.
- **Five properties an episodic store must satisfy** (arXiv:2502.06975, "Episodic Memory is
  the Missing Piece"): *instance-specific*, *contextual relations (when/where/why)*,
  *long-term storage*, *explicit reasoning*, *single-shot learning*. Systems that immediately
  distill turns into facts (Mem0, Vertex Memory Bank) are **semantic, not episodic**.

### The load-bearing markers of a genuinely *episodic* store
1. Preserves **raw timestamped events/trajectories in temporal order** (not distilled facts).
2. **Recency/time decay** in ranking.
3. **Event segmentation** into discrete episodes.
4. **Temporal-order retrieval** (contiguity / bi-temporal).

### How the leading systems represent / write / retrieve / forget episodes

| System | Episodic? | Representation | Write | Retrieval | Forgetting |
|---|---|---|---|---|---|
| **Generative Agents** (2304.03442) | in-function | NL memory objects + timestamps (memory stream) | append every observation | **recency(0.995^h) + importance(LLM 1-10) + relevance(cosine)**, equal weights | soft recency; reflection consolidates |
| **EM-LLM** (2407.09450, ICLR'25) | yes | groups of KV pairs = events | **Bayesian-surprise** boundaries refined by modularity/conductance | k-NN + **temporal-contiguity buffer** (2-stage) | bounded queue (implicit) |
| **MemoryBank** (2305.10250) | yes | logs + daily/global summaries + user profile | continuous + LLM summarize | dense top-k (FAISS) | **Ebbinghaus curve** e^(-t/S); recall reinforces (S+1) |
| **Larimar** (2403.11901, ICML'24) | yes | fixed memory matrix M (latent) | **one-shot** least-squares (no gradients) | read weights → decode | **invertible forget** (alpha=-1 subtracts) |
| **MemGPT/Letta** (2310.08560) | recall tier | raw time-ordered message history | auto-write every message | `conversation_search`/`_date` | none (eviction != deletion); sleep-time consolidation |
| **Zep/Graphiti** (2501.13956) | **explicit episodic subgraph** | raw non-lossy episodic node → entities/edges | entity+fact+**temporal** extraction | hybrid cosine+BM25+BFS, RRF/MMR/cross-encoder | **bi-temporal invalidation** (non-lossy) |
| **AWS AgentCore Memory** (prod) | **dedicated Episodic strategy** | structured episode: scenario/intent/thoughts/actions/outcomes | **episode-completion** trigger (lazy) | semantic search; episodes on "intent" | turns→episode consolidation + cross-episode reflection |
| **Vertex Memory Bank** (prod) | no (Sessions hold trajectories) | consolidated facts/preferences (**semantic**) | async post-session | similarity + scope | Gemini dedup/contradiction merge |
| **Mem0 / A-MEM / LangMem** | mixed | facts / Zettelkasten notes / few-shot examples | extract-consolidate / note / DIY | top-k similarity | conflict-driven / none / DIY |

### Consolidation (episodic → semantic) is the least-mature piece
Reflection (Generative Agents, AgentCore), hierarchical summarization (MemoryBank),
sleep-time rewriting (Letta). The 2026 survey finds **no reliable production episodic→semantic
consolidation** — mostly fragile LLM summarization. **This is the genuine gap/opportunity for
our Symbiotic Memory work.**

---

## 2. Neuro-symbolic memory — neural + symbolic (KG) hybrids

### Why memory is a natural neuro-symbolic fit
Agent memory needs *both* fuzzy associative recall (neural: embedding similarity over messy
experience) *and* precise, updatable, queryable **relations** (symbolic: KG triples, rules,
temporal validity). Pure-vector RAG struggles with multi-hop reasoning, temporal/state
updates, contradiction handling, and provenance; pure-symbolic can't ingest natural language.
The hybrid also mirrors the episodic (experiences) vs semantic (facts) cognitive split.

*(Foundations: IBM neuro-symbolic; Kautz's 6-category taxonomy, AAAI 2020; NS-CL ICLR'19;
IBM Logical Neural Networks 2020; Garcez & Lamb "3rd Wave" survey.)*

### Systems (all share: LLM extracts → graph stores typed entities+relations → retrieval blends similarity + traversal)

| System | Neural part | Symbolic/graph part | Retrieval | Differentiator |
|---|---|---|---|---|
| **Microsoft GraphRAG** (2404.16130) | LLM extracts entities/claims + community summaries | entity KG + hierarchical **Leiden** communities | global (map-reduce over communities) vs local | whole-corpus "global sensemaking" vector RAG can't do; **expensive indexing** |
| **Zep/Graphiti** (2501.13956) | gpt-4o-mini extraction; embeddings + rerank | 3-tier graph; **bi-temporal** edges; invalidate-not-delete | hybrid cosine+BM25+BFS, reranked; **no LLM on default read path** | bi-temporal + real-time incremental (sub-second) |
| **Mem0g** (2504.19413) | GPT-4o-mini extract; ADD/UPDATE/DELETE/NOOP | directed labeled graph (Neo4j) | entity traversal + triplet matching | incremental; +26% vs OpenAI Memory on LOCOMO |
| **HippoRAG / HippoRAG 2** (2405.14831 NeurIPS'24; 2502.14802) | LLM OpenIE → KG ("neocortex"); encoders ("parahippocampal") | open KG = "hippocampal index" to passages | **Personalized PageRank** from query seeds = pattern completion (1-step multi-hop) | neuroscience-grounded; +~20% multi-hop, **10-30x cheaper/faster** than iterative; HippoRAG2 +7 F1 with no simple-QA regression |
| **AriGraph** (2407.04363) | agent extracts triplets each step | graph world-model: **semantic + episodic** memory | semantic (triplet embeddings) + episodic (linked recent) | KG world-model + episodic for embodied agents |
| **TReMu** (2502.01630, ACL'25) | LLM timeline summarization | LLM-generated **Python** for temporal calc (symbolic execution) | time-aware retrieval + neuro-symbolic temporal reasoning | genuine peer-reviewed "neuro-symbolic" agent memory; 29.8→77.7 temporal QA |
| **Cognee** (2505.24478) | LLM extract + summarize | swappable graph+vector+relational; ECL pipeline | similarity + traversal | modular swappable backends; benchmarks thin |

### Neuroscience grounding (what the terms gesture at)
- **Hippocampal indexing theory** (Teyler & Rudy 2007): hippocampus stores an *index/pointer*
  to distributed cortical patterns; partial cue → **pattern completion**; distinct episodes →
  **pattern separation**. Direct basis for HippoRAG.
- **Complementary Learning Systems** (McClelland 1995; Kumaran/Hassabis/McClelland 2016):
  **hippocampus = fast sparse episodic**, **neocortex = slow distributed semantic** (slow rate
  avoids catastrophic forgetting) + replay. This *is* the episodic+semantic two-tier design.

---

## 3. Store technology — decision-oriented

### Start here / add-when (the practical spine)
- **Default: Postgres + pgvector** — one store, `tenant_id` + row-level security, HNSW,
  **hybrid (vector + full-text/BM25 fused with RRF, k≈60)**, namespaced by **user identity**
  (survives sessions). Covers semantic + basic episodic to ~millions of memories. Letta and
  LangMem both default to exactly this. *(Aligns with what the adopted framework already ships:
  `learn`/`memory` + pgvector.)*
- **Add a temporal knowledge graph (Graphiti/Zep over Neo4j) when** you need point-in-time
  "what was true at T," **fact invalidation without loss** / auditability / provenance, or
  genuine **multi-hop relational reasoning**. Don't pay the graph-extraction cost before you
  can name one of these.
- **Add a dedicated vector DB (Qdrant → Milvus) when** >~10M vectors, sub-20ms p99 under heavy
  concurrency, or filter-in-search multi-tenancy at high tenant counts.
- **Add an append-only event log (Redis Streams / DynamoDB+Streams) when** you need immutable
  episodic ground truth for replay/audit, separate from the derived semantic/vector layer.
- **Use a managed service (AgentCore Memory / Vertex Memory Bank) when** you want the async
  extraction/consolidation handled for you and accept lock-in + less control.

### Bi-temporal modeling (the real reason to pay for a graph)
Two independent time axes: **valid time** (when a fact was true in the world) and
**transaction time** (when the system learned it). Graphiti puts **4 timestamps per edge**
(`t_valid`/`t_invalid`, `created_at`/`expired_at`); a contradiction **invalidates** the old
edge (sets `t_invalid`) rather than deleting → point-in-time reconstruction + provenance +
correction without data loss. This is the standout capability for auditability/compliance.

### Hybrid retrieval mechanics
- **RRF** fusion (rank-based, k≈60) of dense + sparse (BM25) + graph traversal — the proven
  default; native in Weaviate/Qdrant/OpenSearch/Azure AI Search.
- **Two-stage rerank**: fast retrieve ~50-100 → **cross-encoder** rerank to ~10 (+50-300ms).
- **HippoRAG PPR**: personalized PageRank over the KG for single-step multi-hop.
- **Episodic time-aware**: temporal *filter* + **recency decay** + composite scoring
  (relevance + recency + importance) — multi-signal beats cosine-only.
- **Keep the read path cheap** (no query-time LLM, like Zep); push extraction/dedup/
  contradiction/summarization to **async "sleep-time" background jobs**.

### What each system uses under the hood
Letta/MemGPT → Postgres+pgvector (tiered core/recall/archival). Mem0 → vector+KV; Mem0g →
Neo4j+vector. Zep/Graphiti → Neo4j (bi-temporal), native vector+BM25. Cognee → pluggable
(LanceDB/pgvector + Kuzu/Neo4j). LangMem → LangGraph `BaseStore` over Postgres+pgvector.
AgentCore/Vertex → managed (backend undisclosed), async consolidation.

---

## 4. Proven vs hype
- **Proven:** Generative-Agents recency+importance+relevance scoring; EM-LLM surprise
  segmentation (ICLR'25); Zep bi-temporal + LongMemEval +18.5%; HippoRAG PPR multi-hop
  (NeurIPS'24); RRF hybrid fusion; cross-encoder rerank; pgvector to millions per-user; async
  consolidation split (AgentCore/Vertex).
- **Real but heavier:** KG/graph memory generally (write-side LLM extraction + maintenance);
  Mem0g temporal reasoning; HippoRAG index maintenance.
- **Hype-flavored:** "brain-inspired/hippocampal" framing (analogy, not a guarantee);
  "temporal knowledge graph" branding (only worth it for point-in-time/audit/multi-hop);
  "one DB for everything"; Cognee benchmarks are thin; "no LLM at retrieval" true for default
  fast paths only; Zep P95 ~300ms is vendor-blog; **episodic→semantic consolidation has no
  reliable production implementation** (biggest open problem). Notably, **Mem0's shipping
  product moved *away* from the paper's external Neo4j graph** — a signal that pure-KG overhead
  isn't always worth it.

---

## 5. Implications for our Symbiotic Memory epic (when we write it)

> **DECISION (locked): the design is a NEURO-SYMBOLIC HYBRID — vector + temporal knowledge
> graph, both first-class from the start.** Not "pgvector-first, graph-when-needed." The graph
> tier is in scope from day one.

1. **Tiered, neuro-symbolic, provider-agnostic store.** Working (context) → **episodic** (raw,
   timestamped) → **semantic** (facts) → procedural, backed by BOTH a **vector** substrate and
   a **temporal knowledge graph**. The store is model-agnostic; only the extraction/reflection
   *LLM calls* vary, so it maps cleanly onto **SLM-native (Mode A) vs provider-native (Mode B)**.
2. **Two co-equal substrates:**
   - **Vector** = pgvector (already in the adopted framework's `learn`/`memory`) for semantic
     similarity + episodic recall entry.
   - **Graph** = a **temporal knowledge graph** for typed entities/relations, multi-hop
     reasoning, provenance, and **bi-temporal** episodic validity. Reference pattern:
     **Graphiti/Zep over Neo4j** (bi-temporal, incremental, no query-time LLM). Build on the
     adopted framework's existing graph paths (Neo4j vector index, `lightrag` graph-RAG, Cognee)
     rather than from scratch where possible.
3. **The differentiator is the write-side consolidation loop** ("Symbiotic Memory"): async /
   sleep-time extraction → dedup → contradiction-resolve → **episodic→semantic consolidation**,
   writing to BOTH vector and graph. This is the least-solved part of the field — the build
   opportunity.
4. **Episodic tier**: raw non-lossy episode store + recency/importance decay + event
   segmentation + **bi-temporal invalidation** (valid + transaction time, invalidate-not-delete)
   on the graph. Model on AgentCore's Episodic strategy + Zep's episodic subgraph.
5. **Retrieval = hybrid neuro-symbolic**: vector + BM25 + **graph traversal** fused with RRF
   (k≈60), optional cross-encoder rerank, optional **HippoRAG-style personalized-PageRank** for
   multi-hop; composite time-aware scoring; **no query-time LLM** on the hot path.
6. **`MemoryProvider` seam** exposes both substrates and is tenant-configurable:
   - **Mode A**: co-located vector + graph (e.g. pgvector + Neo4j in-VPC).
   - **Mode B**: external vector + graph services behind the seam.
7. **Cost note**: the graph's write-side LLM extraction + maintenance is the real cost; keep
   consolidation on the async/sleep-time lane so the read path stays cheap.

---

## Sources (primary prioritized)
- Tulving / CoALA / episodic position: neuropsychologylab.psych.utoronto.ca (Semantic-Episodic);
  arXiv:2309.02427 (CoALA); arXiv:2502.06975 (Episodic is the Missing Piece); arXiv:2603.07670 (survey).
- Episodic systems: 2304.03442 (Generative Agents); 2407.09450 (EM-LLM); 2305.10250 (MemoryBank);
  2403.11901 (Larimar); 2310.08560 (MemGPT/Letta); 2504.19413 (Mem0/Mem0g); 2502.12110 (A-MEM);
  2501.13956 (Zep/Graphiti).
- Neuro-symbolic: research.ibm.com/topics/neuro-symbolic-ai; Kautz AAAI'20 taxonomy; 1904.12584
  (NS-CL); 2006.13155 (LNN); 2012.05876 (3rd Wave); 2404.16130 (GraphRAG); 2405.14831 +
  2502.14802 (HippoRAG 1/2); 2407.04363 (AriGraph); 2502.01630 (TReMu, ACL'25); 2505.24478 (Cognee).
- Neuroscience: Teyler & Rudy 2007 (hippocampal indexing); McClelland 1995 + Kumaran/Hassabis/
  McClelland 2016 (Complementary Learning Systems).
- Stores/managed: docs.aws.amazon.com/bedrock-agentcore (AgentCore Memory);
  cloud.google.com/agent-builder/agent-engine/memory-bank (Vertex Memory Bank);
  neo4j.com/blog/developer/graphiti-knowledge-graph-memory; langchain-ai.github.io/langmem;
  letta.com/blog/agent-memory + sleep-time-compute; pgvector/Qdrant/Milvus/Weaviate docs.
- Terminology: "neuro-symbiotic" (with a b) = brain-computer interface (NSAI), unrelated to
  memory; **no "neuro-symbiotic memory" product/paper exists** — it is a misspelling of
  "neuro-symbolic memory."
