# SM-R11 — MemoryProvider interface spec

| | |
|---|---|
| **Status** | **Implemented** by SM-C1 ([#89](https://github.com/Saturam-Inc/aicoe-tracker/issues/89)) — see §11 for what changed in implementation. Ratified by SM-R1 #76 (ADR 0003). |
| **Issue** | [Saturam-Inc/aicoe-tracker#86](https://github.com/Saturam-Inc/aicoe-tracker/issues/86) |
| **Depends on** | SM-R1 #76 (ADR, open) — the only formal dependency per #86 |
| **Siblings** | SM-R12 #87 (data model, [PR #18](https://github.com/Saturam-Inc/saturam-agent-framework/pull/18) open) — complementary layer, reconciled (see §5.2, §7, §9); SM-R8 (fusion), SM-R10 (bi-temporal) |
| **Feeds** | **SM-C1** (implements this seam) |
| **Task type** | Design/Spec → deliverable is an *implementable* artifact (signatures + types + semantics), not prose |
| **Grounded in** | `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` |
| **Decisions** | D7 (deferred-then-scoped) + D8 (neuro-symbolic hybrid: vector + temporal graph) |

The runnable contract is [`interface.py`](interface.py). This document specifies it and
records the decisions behind it. Two adapters and a config-only swap test
([`adapters.py`](adapters.py), [`swap_test.py`](swap_test.py)) prove the seam is
plug-and-play (Phases 3–4).

> **Phase status:** design complete and **implemented in core by SM-C1** — see §11 for the
> artifact map, the eight deviations, and what was carried forward. The prototype here remains the
> design record; `libs/agno/egpt/memory/` is the authoritative code.
>
> (The prose above once read "10/10 checks"; the artifact had grown to 12. ADR 0003 §5 flagged the
> drift. The shipped suite supersedes both — see `tests/unit/memory/`.)

---

## 1. Acceptance criteria (verbatim from #86)

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | `read`/`write`/`consolidate` signatures specified (with tier + tenant params) | ✅ specified | §5, `interface.py` `MemoryProvider.read/write/consolidate` (+ `read_tier`/`write_tier`); tenant via `Scope`, tier via `MemoryQuery.tiers` / `MemoryTier` |
| 2 | Both substrates (vector + graph) exposed behind one seam | ✅ specified + proven | §4, §5.4 single seam composing `VectorSubstrate` + `GraphSubstrate`; `co_located` adapter wires both; swap test "fusion folds a cross-substrate match", the two bi-temporal checks, and the SM-R7 dual-write check all pass |
| 3 | Relationship to `LearningStore` / `MemoryManager` decided (wrap vs replace) | ✅ decided: **WRAP/extend** | §6 decision + §6.1 per-surface disposition table (all 12 seed surfaces verified against current code) |
| 4 | Mode A/B implementation-swap approach defined | ✅ defined + proven | §5.5 config seam (`MemoryProviderConfig` + registry + `build_memory_provider`); swap test checks 1–3 drive Mode A + Mode B from one consumer, exit 0, deterministic |

Legend: ✅ done · ◑ specified but runnable proof lands in a later phase · unchecked = not yet.

---

## 2. Questions answered (from #86 "Questions to answer")

| Question | Answer |
|----------|--------|
| Method signatures: `read(tenant,agent,query,types) -> MemorySlice`, `write(tenant,agent,events)`, `consolidate(tenant,scope)`? | Yes, with `tenant`+`agent` folded into a single `Scope` object (threads through every call) and `types` expressed as `MemoryQuery.tiers`. See §5. Signatures: `read(scope, query) -> MemorySlice`, `write(scope, events) -> WriteReceipt`, `consolidate(scope, tiers=None) -> ConsolidationReport`. |
| How are vector + graph exposed behind one interface (single seam vs sub-interfaces)? | **Both**: one `MemoryProvider` seam for callers (issue's recommended option), composed internally from two substrate **ports** (`VectorSubstrate`, `GraphSubstrate`). Callers depend on the seam; substrates swap independently behind it. See §4. |
| Does the seam WRAP `LearningMachine`/`MemoryManager` or REPLACE them (per SM-R1)? | **WRAP/extend.** A tier is backed by a wrapped seed component (`LearningStore` / `MemoryManager`) via an adapter; no seed code is forked or discarded. Aligns with SM-R1 #76 Decision 2. Marked provisional for R1. See §6. |
| How does Mode A vs Mode B swap implementations (co-located vs external)? | Same contract, different substrate adapters selected by `MemoryProviderConfig.mode` + `.provider`/`.vector`/`.graph`. `build_memory_provider(config)` is the only construction call site, so switching mode is config, not a rewrite. See §5.5. |
| How do tier + tenant params thread through? | `tenant_id` is mandatory on `Scope`, present on **every** method. Tier is a first-class `MemoryTier` enum: multi-tier reads pass `MemoryQuery.tiers`; single-tier ops use `read_tier`/`write_tier`. Every stored/returned record carries its `tier` and `scope`. See §5.1–§5.2. |

---

## 3. Options evaluated (from #86 "Options to evaluate")

| Option | Assessment | Verdict |
|--------|-----------|---------|
| **Single `MemoryProvider` seam over both substrates** (issue-recommended) | One dependency for callers; provider owns fusion (weighted default, SM-R8) + optional PPR (SM-R9) in one place; matches the research doc's "one seam exposes both substrates, tenant-configurable" (§5.6). | **Chosen**, refined: single seam **composed from two substrate ports** — callers get one seam, backends still swap per-substrate. |
| Separate vector + graph interfaces composed by the caller | Pushes fusion and substrate-lifecycle into every call site; each consumer re-implements RRF and dual-write; Mode A/B swap leaks to callers. Rejected as the *caller-facing* seam. | **Rejected** as the public seam — but preserved *internally* as the `VectorSubstrate`/`GraphSubstrate` ports, which is where per-backend swap belongs. |

The chosen design takes the strength of the rejected option (independent substrate swap) without
its cost (fusion/dual-write leaking to callers).

---

## 4. Architecture: one seam, two substrate ports

```
        consumers (agents, teams, SM-C*)
                     │  depend only on ▼
             ┌──────────────────┐
             │  MemoryProvider  │   read / write / consolidate (+ tier-aware), sync + async
             │   (single seam)  │   owns fusion (weighted default SM-R8; PPR SM-R9)
             └───────┬──────────┘
              composes│
        ┌─────────────┴─────────────┐
        ▼                           ▼
 VectorSubstrate              GraphSubstrate
 (pgvector / Qdrant …)        (Neo4j+Graphiti / Zep …)
   SM-R3 backend                SM-R2 / SM-G1 backend
 similarity + full-text       multi-hop + bi-temporal (SM-R10)
```

- **Mode A** wires co-located substrates (in-VPC pgvector + Neo4j); **Mode B** wires external
  services. Both satisfy the identical ports, so the provider and all consumers are unchanged.
- Tier → substrate mapping (SM-R1 #76 diagram, provisional): WORKING → context only;
  EPISODIC → vector + graph; SEMANTIC → vector + graph; PROCEDURAL → rules/prompt.

---

## 5. The specification

### 5.1 Scoping — `Scope` (tenant + agent on every call)

```python
@dataclass(frozen=True)
class Scope:
    tenant_id: str                    # mandatory: multi-tenant isolation (+ row-level security)
    agent_id: Optional[str] = None
    user_id: Optional[str] = None     # identity namespace that survives sessions
    session_id: Optional[str] = None
```

`tenant_id` is required; there is no method that reads or writes without a `Scope`.

### 5.2 Data model — layering with SM-R12 #87

**Clean split (verified against SM-R12's authored `benchmarks/memory_datamodel/model.py`):**

- **SM-R12 owns the persisted per-tier ROW schemas** — `EpisodicRecord`, `SemanticFact` /
  `SemanticEntity` / `SemanticRelation`, `ProceduralRecord`, `WorkingMemory`, `MemoryLink`, on a
  `TenantScoped` + `BiTemporal` + `Embeddable` + `Decaying` spine. R12 does **not** define any
  seam-envelope type.
- **SM-R11 (this spec) owns the SEAM ENVELOPE** — `MemorySlice`, `MemoryQuery`, `MemoryEvent`,
  `WriteReceipt`, `ConsolidationReport`, `Scope`. These are interface DTOs, not stored rows.

`MemoryRecord` is the seam's **uniform read-projection** over R12's row types (not a competing
schema). Its field names + int epoch-seconds are **aligned to R12's spine** so SM-C1 maps 1:1:

- `MemoryRecord` — `id, tier, content, scope, score?, substrate?, topics?, provenance, t_valid?, t_invalid?, created_at?, expired_at?, superseded_by?, metadata` — bi-temporal names are SM-R10 canonical, matching R12's `BiTemporal` (`t_valid`/`t_invalid`/`created_at`/`expired_at`/`superseded_by`).
- `MemoryEvent` — `content, scope, tier=EPISODIC, id?, role?, topics?, occurred_at?(epoch s -> row t_valid), metadata`
- `MemoryQuery` — `text, tiers?(= issue "types"), substrates?, limit=10, as_of?(epoch s, SM-R10), filters?`
- `MemorySlice` — `scope, query?, records[], by_tier{}, fusion{}` + `as_context() -> str` (parallels `LearningStore.build_context`)
- `WriteReceipt` — `accepted, event_ids[], tiers_written[]`
- `ConsolidationReport` — `scope, episodic_scanned, semantic_written, duplicates_merged, contradictions_resolved, details{}`
- `Scope` — `tenant_id(req), agent_id?, user_id?, team_id?, session_id?` — scope ids match R12's `TenantScoped`.

Still provisional pending SM-R1 #76; final field reconciliation (e.g. R12's `Decaying`/`Embeddable`
fields surfacing on the projection) lands with SM-C1.

### 5.3 Method signatures (all sync **and** async)

> ⚠️ **DELIBERATE DEVIATION FROM ISSUE #86 — read `interface.py`, not the issue's sketch.**
> The issue's *Questions* sketch `read(tenant, agent, query, types) -> MemorySlice`. This spec
> does **not** use that literal shape. It folds `tenant`+`agent` into a single **`Scope`** object
> (so scoping threads uniformly through *every* method — you never pass tenant/agent positionally)
> and expresses `types` as **`MemoryQuery.tiers`**. Same information, better ergonomics. The real
> signature is `read(scope: Scope, query: MemoryQuery) -> MemorySlice`.
> Do **not** copy `read(tenant, agent, query, types)` into downstream docstrings — that literal was
> a **suggested shape in the issue's "Questions to answer" (a question, not a hard requirement)**,
> and a sibling PR (SM-R13 #19) already misquoted it by copying from the issue text instead of this
> file. When in doubt, the code in `interface.py` is authoritative.

| Verb | Sync | Async | Returns | Semantics |
|------|------|-------|---------|-----------|
| read (feed-forward) | `read(scope, query)` | `aread(scope, query)` | `MemorySlice` | Hybrid vector+graph(+BM25) fused via **WEIGHTED** relative-score fusion (SM-R8 default, `vector_weight≈0.5`); RRF (k≈60) reserved for the SM-G9-gated graph-join case; optional rerank (SM-R8) / PPR (SM-R9), off by default. No query-time LLM on hot path. |
| write (write-back) | `write(scope, events)` | `awrite(scope, events)` | `WriteReceipt` | Append raw events (default EPISODIC). Cheap, non-lossy; no distillation. |
| consolidate | `consolidate(scope, tiers=None)` | `aconsolidate(scope, tiers=None)` | `ConsolidationReport` | Sleep-time: extract → dedup → contradiction-resolve → write BOTH substrates. Async is the primary lane. |
| read tier | `read_tier(scope, tier, query)` | `aread_tier(...)` | `List[MemoryRecord]` | Single-tier direct read, no cross-tier fusion. |
| write tier | `write_tier(scope, tier, events)` | `awrite_tier(...)` | `WriteReceipt` | Write to a specific tier. |
| introspect | `substrates()` | — | `Dict[Substrate, bool]` | Which substrates are wired (health/diagnostics). |

**Async convention:** `a`-prefix (`aread`, `awrite`, `aconsolidate`), matching the `learn`/`memory`
seam this extends (`arecall`, `aprocess`). NOTE: `vectordb/base.py` uses the `async_` prefix
instead; a `VectorDb`-backed adapter bridges the two prefixes internally.

### 5.4 Substrate ports (behind the seam)

```python
class VectorSubstrate(Protocol):   # neural: similarity + full-text
    def upsert(scope, records); async def aupsert(...)
    def search(scope, query, tier=None) -> List[MemoryRecord]; async def asearch(...)

class GraphSubstrate(Protocol):    # symbolic: temporal KG, multi-hop
    def upsert_episode(scope, event); async def aupsert_episode(...)
    def traverse(scope, query, tier=None) -> List[MemoryRecord]; async def atraverse(...)
    def invalidate(scope, edge_id, t_invalid, at=None); async def ainvalidate(...)   # SM-R10 SCD-2, two times
```

Both are `runtime_checkable` Protocols (structural), so an existing `VectorDb` subclass can
satisfy `VectorSubstrate` through a thin adapter without inheritance.

### 5.5 Config seam (Mode A/B + backend swap) — the swap-proof

```python
@dataclass
class MemoryProviderConfig:
    provider: str                       # registry key → adapter
    mode: DeploymentMode = CO_LOCATED   # A (co-located) | B (external)
    vector: Dict[str, Any] = {}         # opaque backend settings (dsn, dim, …)
    graph:  Dict[str, Any] = {}         # opaque backend settings (uri, …)
    fusion: Dict[str, Any] = {"method": "weighted", "vector_weight": 0.5}   # SM-R8 default; "rrf" gated for graph-join; "ppr" flag = SM-R9
    options: Dict[str, Any] = {}

build_memory_provider(config) -> MemoryProvider   # the ONLY construction call site
register_provider(key)                            # adapters self-register on import
```

Swapping backend or Mode A↔B = editing `config` (or its source file); consumers are untouched.

### 5.6 Consumer contract (what a call site looks like)

```python
provider = build_memory_provider(load_config())      # config chooses the backend + mode
provider.write(scope, [MemoryEvent(content="...", scope=scope)])
slice_ = provider.read(scope, MemoryQuery(text="...", tiers=[MemoryTier.SEMANTIC]))
prompt_context = slice_.as_context()
# background lane:
await provider.aconsolidate(scope)
```

No concrete backend, dimension, DSN, or provider SDK appears at the call site.

---

## 6. Decision: WRAP vs REPLACE (provisional for SM-R1 #76)

**Decision: WRAP / extend.** `MemoryProvider` is a superset seam whose tiers delegate to
wrapped seed components; it does not fork or discard them. This aligns with SM-R1 #76 Decision 2
("reuse the `learn` store/tier abstraction; add episodic + vector-search + graph on top"). Per
the issue, the wrap-vs-replace call is ratified by SM-R1 — so this is provisional until #76 lands.

### 6.1 Per-surface disposition (verified against current code)

Every `file:line` below was confirmed present in the current post-rebrand tree (namespace is
`egpt`, paths under `libs/agno/egpt/`). Dispositions: **REUSE** (use as-is behind the seam),
**EXTEND** (add capability, keep the type), **REPLACE** (supersede with migration), **OUT-OF-SCOPE**
(owned by a sibling task; candidate noted). No surface is REPLACEd — consistent with #76 Decision 2.

| Seed surface (verified) | What it is today | Disposition | How it maps to the seam / migration note |
|---|---|---|---|
| `learn/stores/protocol.py:16` **`LearningStore`** | Per-store `recall`/`aprocess`/`build_context`/`get_tools`/`was_updated`/`schema` | **EXTEND** | The seam to build on. `recall→read_tier`, `process→write_tier`, `build_context→MemorySlice.as_context`. MemoryProvider adds what it lacks: substrate-awareness, cross-tier fusion, `consolidate`. |
| `learn/machine.py:53` **`LearningMachine`** | Orchestrates 6 stores; `recall/arecall`, `process/aprocess`, `build_context`, `get_tools`, `curator` | **REUSE (wrap)** | Mode-A co-located adapter wraps it: `read`→`recall`+`build_context`, `write`→`process`. Its `custom_stores: Dict[str, LearningStore]` is the DI point where a new **episodic** store + vector/graph slot in. |
| 6 learn stores (`user_profile`, `user_memory`, `session_context`, `entity_memory`, `learned_knowledge`, `decision_log`) | Tiered learning stores | **REUSE** (map to tiers) | `session_context→WORKING`; `user_memory`/`entity_memory`/`user_profile→SEMANTIC`; `learned_knowledge`/`decision_log→PROCEDURAL`. **No EPISODIC store exists → NEW** (see §6.2). |
| `learn/curate.py:28` **`Curator`** | `prune`, `deduplicate` | **REUSE** (inside `consolidate`) | Dedup + forgetting/decay lane of `consolidate`; decay model itself is SM-R5. |
| `memory/manager.py:46` **`MemoryManager`** | CRUD over `UserMemory`; `search_user_memories` = `last_n`/`first_n`/`agentic` (**not** vector); scoped by user/agent/team | **REUSE (wrap) + EXTEND** | Wrapped as a minimal co-located adapter (write + non-vector search). EXTEND: add vector search via a `VectorSubstrate` (it has no embedder today). |
| `memory/strategies/summarize.py:15` **`SummarizeStrategy`** (`MemoryOptimizationStrategy.optimize/aoptimize`) | Only consolidation today (LLM summarize) | **REUSE + EXTEND** | The episodic→semantic distillation step of `consolidate`. EXTEND with extraction + contradiction-resolution (the research doc's least-solved gap). |
| `db/schemas/memory.py:9` **`UserMemory`** | `memory, memory_id, topics[], user_id, agent_id, team_id, input, created_at, updated_at` — **no embedding / tenant_id / temporal** | **EXTEND** (owned by SM-R12 #87, SM-C3) | Needs embedding + `tenant_id` + bi-temporal. SM-R12 already models this as `EpisodicRecord`/`SemanticFact`/… on a `TenantScoped`+`BiTemporal`+`Embeddable` spine; this seam's `MemoryRecord` projects those rows. |
| `vectordb/base.py` **`VectorDb`** ABC (`insert`/`upsert`/`search`, `async_*`) | Vector DB abstraction | **REUSE** (as `VectorSubstrate` backend) | A thin adapter satisfies the structural `VectorSubstrate` Protocol; bridges `async_` → `a` prefix. Concrete store choice = SM-R3. |
| `db/base.py:37` **`BaseDb`** / `:1327` **`AsyncBaseDb`** | Persistence ABCs (sync + async) | **REUSE** | Backing store for records/events under both adapters. |
| `vectordb/lightrag/` **lightrag** | Graph-RAG (not bi-temporal) | **OUT-OF-SCOPE** (candidate) | Possible `GraphSubstrate` backend, but #76 Decision 3 leans NEW temporal graph (Graphiti/Neo4j) since lightrag is not bi-temporal. Engine choice = SM-R2. |
| `tools/neo4j.py`, `tools/zep.py` | Neo4j/Zep as agent **tools**, not memory backends | **OUT-OF-SCOPE** (candidate) | Repurpose-candidates as `GraphSubstrate` backends (SM-R2 / SM-G1). |
| `session/summary.py:63` **`SessionSummaryManager`** | Session summaries (adjacent) | **OUT-OF-SCOPE** (candidate) | Feeds WORKING-tier context / consolidation input; noted, not decided here. |

### 6.2 Net-new (capabilities absent from the seed, must be built by SM-C*)

The reuse audit confirms the seed has **no**: EPISODIC tier / raw non-lossy episode store,
vector-search-over-memory (retrieval is `last_n`/`first_n`/`agentic`), temporal knowledge graph
(only `lightrag` graph-RAG; Neo4j/Zep are tools), bi-temporal model, or an async consolidation
loop. These are the additive build surface `MemoryProvider` exposes — WRAP the above, ADD these.

---

## 7. Couplings & handoff

| Consumes / feeds | What crosses the boundary |
|---|---|
| **SM-C1** (implements this) | `MemoryProvider` ABC + `MemoryProviderConfig` + registry; adapters subclass the ABC. |
| **SM-R3** (vector store) | Selected as the `VectorSubstrate` backend via `config.vector`. |
| **SM-R2 / SM-G1** (graph engine) | Selected as the `GraphSubstrate` backend via `config.graph`. |
| **SM-R8** (retrieval fusion) | `config.fusion` — **weighted is the default** (`method="weighted"`, `vector_weight≈0.5`), RRF selectable but reserved for the SM-G9-gated graph-join case; optional cross-encoder rerank (off by default). The provider owns fusion in `read` (`_weighted`/`_rrf`). **Caveat:** SM-R8 measured weighted-over-RRF for dense+BM25 (2-leg) and dense+BM25+graph (3-leg), **not** this seam's vector+graph pairing; defaulting to weighted is a mechanism-based *transfer* to re-validate against real backends (SM-G9/SM-R3), not a measured result for this exact pairing. |
| **SM-R9** (multi-hop / PPR) | `config.fusion["ppr"]` — optional personalized-PageRank, **per-tenant flag, default off** (SM-R9's decision, not SM-R8's). |
| **SM-R10** (bi-temporal) | `t_valid`/`t_invalid`/`created_at`/`expired_at`/`superseded_by` on records. **Reads:** `MemoryQuery.as_of` enforces the **two-sided** `t_valid <= as_of < t_invalid` over **current belief** (`expired_at IS NULL`, §7 5a). **Writes:** `GraphSubstrate.invalidate(scope, edge_id, t_invalid, at=None)` mirrors SM-R10's `invalidate_edge` — **two times** (valid-time false + transaction-time) and **expire-and-reinsert (SCD-2)**, never in-place mutation. A real backend delegates to SM-R10's `upsert_edge`/`invalidate_edge`/`retract_edge`. |
| **SM-R7** (consolidation) | `consolidate` distills episodic→semantic and writes to **both** substrates; deterministic dedup here, LLM extraction + contradiction-resolution deferred to SM-C*. **One write model:** any contradiction it produces on the graph resolves through the **same SM-R10 expire-and-reinsert (SCD-2)** path as `invalidate` — not a second, in-place path. **Caveat:** the reference graph write is a **flat provenance-carrying stand-in** (via `upsert_episode`), not the SM-R12 `SemanticEntity`/`SemanticRelation` + fact↔episode-edge shape SM-R7 intends; SM-C1 needs a semantic-fact upsert on the `GraphSubstrate` port. |
| **SM-R12** (data model, [PR #18](https://github.com/Saturam-Inc/saturam-agent-framework/pull/18)) | Owns the per-tier row schemas (`EpisodicRecord`/`SemanticFact`/…) **and** a row-level `MemoryStore` port (`read(model, tenant_id, id)` / `write(rec)` + async; `Pg`/`InMemory` adapters). This seam's `MemoryRecord` is a read-projection over those rows; a `MemoryProvider` adapter **composes** `MemoryStore` for single-row persistence beneath the seam. Altitudes differ: R12 `read/write` = one row by primary key; R11 `read/write` = query→`MemorySlice` / event write-back. The seam envelope (`MemorySlice`/`MemoryEvent`/`Scope`) is owned here; R12 defines none of it. |

---

## 8. Out of scope (owned elsewhere)

- Graph engine choice → SM-R2. Vector store threshold/choice → SM-R3. Decay model → SM-R5.
  Bi-temporal field semantics → SM-R10. Authoritative schemas → SM-R12. Any production wiring /
  implementation → SM-C*. This task specifies the seam only.

---

## 9. Consistency with dependencies & siblings

- **SM-R1 #76 (open):** consistent with its Decision 2 (reuse/extend) and tier→substrate map;
  all decisions here marked provisional pending its ratification.
- **SM-R12 #87 ([PR #18](https://github.com/Saturam-Inc/saturam-agent-framework/pull/18), open):**
  reconciled against the PR's `benchmarks/memory_datamodel/model.py` — **byte-identical** to the
  version aligned to earlier, so the field alignment holds unchanged. Clean layering confirmed
  (R12 = row schemas + `MemoryStore` row-CRUD; R11 = retrieval seam + envelope; no duplication and
  no type-name collision — R12 defines none of `MemorySlice`/`MemoryProvider`/`MemoryQuery`/
  `MemoryEvent`/`Scope`). Field names + int epoch-seconds match R12's `TenantScoped`+`BiTemporal`
  spine (`t_valid`/`t_invalid`/`created_at`/`expired_at`/`superseded_by`); `Scope` carries
  `team_id`. PR unmerged, so this is alignment-to-current; final reconciliation is SM-C1's on
  ratification.
- **SM-R8 (fusion, ratified):** aligned to its DECISION.md — **weighted / relative-score fusion
  is the default** (`method="weighted"`, `vector_weight≈0.5`; the co_located adapter implements a
  real weighted path, `_weighted`). RRF is selectable but **reserved for the SM-G9-gated 3-leg
  graph-join case**, which SM-R8 marks provisional (its synthetic 3-leg test found weighted's edge
  over RRF *widened*, not flipped). Fusion is genuinely config-selectable, proven by the swap test
  ("weighted is the default; RRF selectable by config"). **Honest caveat:** SM-R8 never measured a
  vector+graph pairing (its legs were dense+BM25±graph), so the weighted default *here* is a
  mechanism-based transfer to re-validate with real backends, not a measured result for this pairing.
  **SM-R13 (#19) owns measuring vector+graph weighted-vs-RRF on this seam** before the default is
  ratified; #19 currently lacks a graph/multi-hop retrieval metric, so that gap and this transfer
  are the same open work.
- **SM-R9 (multi-hop / PPR, ratified):** PPR is exposed as an optional **per-tenant flag, default
  off**, and attributed to SM-R9 (previously mis-cited as SM-R8).
- **SM-R10 (bi-temporal, ratified):** **reads** enforce the **two-sided** "true at T" rule
  `t_valid <= as_of < t_invalid` over current belief (`expired_at IS NULL`), matching SM-R10 §7.
  **Writes** now match SM-R10's ratified model too: `invalidate(scope, edge_id, t_invalid, at=None)`
  carries two times and does **expire-and-reinsert (SCD-2)**, never in-place mutation (SM-R10 §6
  proved in-place wrong). Verified by the swap test ("expire-and-reinsert (SCD-2), no in-place
  clobber": two invalidations accumulate rows, one current belief, tightest bound, original
  untouched). Field names/types track R12's `BiTemporal` spine.
- **SM-R7 (consolidation, ratified spike):** the reference `consolidate` writes the distilled fact
  to **both** substrates (so the "both substrates" claim is true even in the stub); LLM extraction
  and contradiction-resolution are deferred to SM-C*, and any contradiction it produces resolves
  through the **same SM-R10 SCD-2 write path** (one write model, not two).

### 9.1 Pre-review self-check dispositions (against issue #86 + siblings)

| # | Item | Disposition |
|---|------|-------------|
| P1-1a | Signature deviation from issue's `read(tenant,agent,query,types)` | **Made explicit** — loud callout in §5.3 + `interface.py` `read` docstring, so downstream docs stop copying the issue's suggested-signature literal. |
| 1 | SM-R8: fusion hardcoded RRF, no weighted path | **Fixed** — `_weighted` added, defaulted on; `config.fusion["method"]` now takes effect. |
| 2 | SM-R10: `as_of` only checked `t_invalid` (upper bound) | **Fixed** — two-sided `_valid_at`; future-dated test added. |
| 3 | SM-R7: `consolidate` claimed "both substrates" but wrote vector only | **Fixed** — now dual-writes; docstring made accurate. |
| 4 | Internal: §9 claimed fusion "config, not decided" vs hardcoded RRF | **Fixed** — fusion is config-selectable (this section + swap test). |
| 5 | SM-R9: PPR mis-cited as SM-R8 | **Fixed** — relabelled SM-R9 in `interface.py` + here. |
| 6 | SM-R12 field/type alignment | **Re-confirmed** — no record-schema drift; `tenant_id: str`, int epoch-seconds, `t_valid/t_invalid/...` intact (also fixed a stale `valid_from/valid_to` reference in §7). |

### 9.2 Review-response dispositions (PR #20 conditional approval)

| # | Severity | Comment | Disposition |
|---|----------|---------|-------------|
| 1 | **Major** | `GraphSubstrate.invalidate` did in-place `t_invalid` mutation + wrong arity (SM-R10 proved in-place wrong; primitive is `invalidate_edge(edge_id, t_invalid, at)`) | **Fixed** — port widened to `invalidate(scope, edge_id, t_invalid, at=None)` (two times); reference adapter now does **expire-and-reinsert (SCD-2)** — expires the live row on the transaction axis + inserts a bounded copy, never in-place; reads select current belief (`expired_at IS NULL`). New regression test proves no in-place clobber across two invalidations. Real backend delegates to SM-R10's write primitives. |
| 2 | Minor | Weighted default for vector+graph is an unvalidated transfer — name SM-R13 (#19) as owner; ties to #19's missing graph metric | **Fixed** — `_weighted` docstring + SPEC §7/§9 now name **SM-R13 (#19)** as owner of measuring vector+graph weighted-vs-RRF, and note #19's missing graph metric is the same open work. |
| 3 | Minor | `consolidate` contradiction path stubbed + bi-temporal handoff unspecified | **Fixed** — `ConsolidationReport` docstring + SPEC §7 state contradictions resolve through the **same SM-R10 SCD-2 write path** (one write model, not two). |

---

## 10. Grounding & reproducibility

- Grounded in `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` (tiers §1, neuro-symbolic §2,
  store/bi-temporal/hybrid-retrieval §3, implications §5) and issues #86/#76/#87.
- Reproduce: `python -m benchmarks.memory_provider_spec.interface` prints the seam summary;
  `python -m benchmarks.memory_provider_spec.swap_test` runs the swap proof (exit 0, writes
  `results/swap_results.json`, byte-identical across runs).
- Core untouched **by SM-R11**: `git diff origin/main -- libs/` was empty for the design task.
  That claim is scoped to this prototype and no longer describes the tree — SM-C1 shipped the seam
  into `libs/agno/egpt/memory/`, which §11 records.

---

## 11. Implementation record — SM-C1 (#89)

This spec is now implemented in core under `libs/agno/egpt/memory/`. The prototype in this folder
stays as the design artifact and its evidence; **the shipped code is authoritative**.

| Spec artifact | Shipped as |
|---|---|
| `interface.py` envelope types | `egpt/memory/types.py` + `egpt/memory/tiers.py` |
| `VectorSubstrate` / `GraphSubstrate` | `egpt/memory/substrates.py` |
| `MemoryProvider` ABC | `egpt/memory/provider.py` |
| `MemoryProviderConfig` + registry | `egpt/memory/config.py` |
| `CoLocatedMemoryProvider` | `egpt/memory/adapters/co_located.py` |
| *(new)* seed WRAP adapter | `egpt/memory/adapters/seed.py` |
| *(new)* shared write-path guards | `egpt/memory/validation.py` |
| `swap_test.py` | `tests/unit/memory/test_provider_swap.py` |
| `_weighted` / `_rrf` | **not shipped** — see §11.2 |
| `ExternalStubMemoryProvider` | **not shipped** — see §11.2 |

### 11.1 Deviations from this spec, and why

| # | Deviation | Reason |
|---|---|---|
| D1 | `ProviderMode.LOCAL/FRONTIER` replaces `DeploymentMode.CO_LOCATED/EXTERNAL` | `providers/model_provider.py` already means Mode A/B for models. ADR 0003 §B.5 rules against two vocabularies for one concept. Lets SM-C5 reuse the `PolicyKey` → `ProviderConfig.lookup` per-tenant pattern. |
| D2 | `MemoryTier` lives in `tiers.py`; config in `config.py` | Those are the files SM-C2 (#90) and SM-C5 (#93) are chartered to create. Placing them elsewhere would force a duplicate enum and a split config module. |
| D3 | `GraphSubstrate.upsert_fact` + `FactWrite` added | Finding F9: the port exposed only `upsert_episode`, forcing consolidation to write a fact as a flat pseudo-episode. Field names mirror SM-R12's `SemanticFact` so SM-C3 maps 1:1. |
| D4 | Explicit `VectorSubstrate.scan` replaces "empty `query.text` = scan all" | The convention hid the operation behind a magic value and could not be bounded. `scan` orders ascending `created_at`, then `id`, and takes a `limit`. It takes **no cursor** — see §11.2. Graph enumeration is deliberately absent: no consumer in SM-C1, and what it means over a temporal graph is SM-G9's to define. |
| D5 | Provider registry is local to `egpt/memory/`, not a `discovery` KIND | Discovery declarations live in **core**; a provider adapter is **services**, so declaring it there would have core naming a services module. ADR 0001's "config-swappable backends use the same constructor registry" refers to the *backends* a provider composes — those stay `discovery.construct` under SM-V1/SM-G1. |
| D6 | Unknown provider key raises `MemoryProviderConfigError` (a `ValueError`), not `KeyError` | Matches `discovery.resolve`, which raises `ValueError` for an unknown name, and `ModelProviderConfigError` for a bad config. |
| D7 | Merged ties break on `(content, tier, id)`, never on arrival order | Found by the swap proof's determinism check. Arrival order is driven by UUID record ids, so two deployments holding identical content ranked it differently. |

### 11.2 Deliberately not shipped

SM-C1's five acceptance criteria are an **interface**. There is no pgvector (SM-V1 #95) and no graph
engine (SM-G1 #99), so any behaviour built here can only be exercised against doubles written in the
same change — unvalidatable by construction. Each item below was cut for that reason and has a named
owner, not a "later".

| Cut | Why | Owner |
|---|---|---|
| Weighted and RRF fusion, `config.fusion` and its knobs (`method`, `vector_weight`, `k`, `rerank`, `ppr`) | SM-R8 measured dense+BM25, **never this seam's vector+graph pairing**, so no default here is a measured result — and with no backend wired, nothing could measure one. The provider sums the legs' scores and reports `method="score_sum"`, which claims exactly what it does. | SM-H2 #132 |
| The Mode B / external adapter | It would have composed the same two ports over the same in-memory doubles. That proves a class can be subclassed, not that a network seam holds. `ProviderMode.FRONTIER` is accepted and reaches the provider today; the swap test covers that. | SM-X2 #138 |
| A consolidation cursor, watermark, or resumption protocol | Three review rounds of defects lived in the *protocol*, not the implementation: nothing passed the cursor; a time-only cursor could not page past co-second episodes; a no-progress guard fired on a legitimate batch. `consolidate` runs one bounded pass and reports `details["complete"]`. A resumable walk should be designed with the scheduler that drives it. | SM-L1 #122 |
| Read-path over-fetching to compensate for post-merge validity filtering | No fixed multiplier is correct, and it taxes every read. The real fix is a vector projection that is not stale. Stated as a residual in `VectorSubstrate.search`. | SM-V1 #95 / SM-G1 #99 |
| Per-tier read/write policy beyond `SUBSTRATES_BY_TIER` | `tiers.py` ships the write-routing data the seam needs to not guess; turning it into policy is a separate charter. | SM-C2 #90 |
| `MemoryQuery.filters` semantics | No substrate port states a contract for it, so a future backend would have nothing to conform to. A filter that silently does not apply returns an unfiltered slice that looks filtered — refused at the envelope rather than accepted. | SM-V1 #95 |

### 11.2a Additions the spec did not cover

- **`SeedMemoryProvider`** (`adapters/seed.py`) — the WRAP half of ADR 0003 §2, giving a deployment
  the seam over `MemoryManager` and the `learn` stores with no new infrastructure. Most of it is
  refusals, because the seed cannot honour tenancy, valid time, substrates, or an episodic tier, and
  an ignored parameter returns a wrong answer that looks right.
- **`SUBSTRATES_BY_TIER`** (`tiers.py`) — ADR 0003 §1's ratified write routing as data.
- **`validation.py`** — write-path guards shared by both adapters. Invariants implemented per
  adapter drifted; each one here is paired with a conformance-matrix property.
- **`FUSION_KEYS` / `CONSOLIDATION_KEYS`** — a floor on the diagnostics every provider reports, so a
  provider-agnostic consumer does not `KeyError` on whichever adapter it was not written against.

### 11.2b Invariants and where they are proved

Each row is one shared implementation plus one parametrised property in
`tests/unit/memory/test_provider_swap.py`, run against **every** registered provider and wiring —
including the single-substrate configurations, whose absence previously let a correctly-worded
conformance test miss a violation.

| Invariant | Enforced in |
|---|---|
| No record leaves the seam under a tenant that did not ask for it — on read **and** consolidate | `co_located._assert_tenant_isolation`, `seed._guard_tenant` |
| Duplicate ids in one batch are refused before anything is written | `validation.reject_duplicate_ids` |
| `MemoryEvent.scope` must equal the write scope | `validation.reject_foreign_scopes` |
| A named substrate is served or refused, never silently emptied | `co_located._require_answerable`, `seed._reject_unservable_substrates` |
| A scope slot the backend cannot filter on is refused, not ignored | `seed._reject_unfilterable_scope` |
| `MemorySlice.fusion` carries `FUSION_KEYS`; `ConsolidationReport.details` carries `CONSOLIDATION_KEYS` | `types.fusion_diagnostics` / `types.consolidation_details` |
| `limit` is honoured, and `limit=0` means zero rows | both adapters, both `read` and `read_tier` |
| Sync and async halves agree | conformance matrix |
| A partial multi-target write raises `PartialWriteError` carrying the stamped ids | `types.PartialWriteError`, raised by both adapters |
| Point-in-time needs both predicates (`t_valid ≤ at < t_invalid` **and** current belief) | `co_located._valid_at` + `MemoryRecord.is_current_belief` |
| Invalidation is expire-and-reinsert (SCD-2), never in-place | `GraphSubstrate.invalidate` contract |

Substrates filter **transaction time** before ranking; valid time is enforced once, on the merged
set. A substrate that filtered `as_of` itself would withhold the SCD-2 successor row carrying
`t_invalid` — which is the evidence that catches an invalidated fact (finding F2).

Regression tests are **mutation-verified**: each defect was reintroduced and the suite confirmed to
fail. Assertions are on what a substrate was *asked for*, not only on what it returned, because a
result-only assertion cannot detect a wrong request.

### 11.3 Carried forward with named owners

| Item | Owner |
|---|---|
| Rank fusion (weighted / RRF), its configuration, and the measurement that justifies a default | SM-H2 #132 |
| The Mode B adapter, and re-proving config-only swap against a **real** external backend (ADR §5) | SM-X2 #138 |
| The consolidation scheduler, and draining a tier larger than one pass | SM-L1 #122 |
| Measure weighted-vs-RRF on the **vector+graph** pairing — SM-R8 never did | SM-R13 #19 |
| Reject `HuggingfaceCustomEmbedder` for `mode=LOCAL` (ADR §A.3c) | SM-V2 #96 |
| `Curator` is narrower than ADR §2.1 implies (`user_profile` store only, no async) | SM-L4 #125 |
| `MemoryManager.search_user_memories` / `add_user_memory` have no async variant; the bridge hops a thread | SM-C2 #90 |
| Graph enumeration semantics for a `scan` on `GraphSubstrate` | SM-G9 #107 |
| Bounds-by-id lookup on `GraphSubstrate`, to close point-in-time when the graph's top-`limit` misses a row the vector leg returned | SM-G1 #99 |
| Push the current-belief predicate into the substrates so the read path stops needing a merged-set filter | SM-V1 #95 / SM-G1 #99 |
| Explicit supersede when a consolidation group gains a member, so the previous fact is not orphaned | SM-L3 #124 |
| Real tenant isolation for the seed bridge (a `tenant_id` column and its migration) | SM-C3 #91 |
