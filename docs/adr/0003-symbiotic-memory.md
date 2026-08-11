# ADR 0003: Symbiotic Memory — Neuro-Symbolic Memory Architecture

- **Status:** Proposed
- **Date:** 2026-08-10
- **Backlog:** SM-R1 ([aicoe-tracker#76](https://github.com/Saturam-Inc/aicoe-tracker/issues/76))
- **Epic:** Symbiotic Memory (Neuro-Symbolic)
- **Deciders:** Architecture owners (framework)
- **Decisions covered:** D7 (deferred-then-scoped), D8 (neuro-symbolic hybrid: vector + temporal graph)

## Context

This ADR is nominally the root of the Symbiotic Memory milestone ("Dependencies: None"). In
practice it is being written **last**: eleven downstream decision tasks (SM-R3 … SM-R13) have
already shipped, each flagging its own numbers as *"provisional pending SM-R1 to ratify."*
**All eleven are now merged to `main`** — SM-R11 landed last via
[PR #20](https://github.com/Saturam-Inc/saturam-agent-framework/pull/20) (`e266ac30`).

> **Freshness — this ADR consolidates eleven moving documents, so it states its own basis.**
> **Verified against `origin/main` @ `e266ac30` (2026-08-10).** Any sibling merging after this sha
> may invalidate a claim below; re-verify before relying on a specific number. This is not
> hypothetical: **SM-R11 changed materially between the branch tip and its merge** — a **Major**
> write-side fix caught in PR #20 review (§3) — and an earlier revision of this ADR documented the
> superseded version.

So this document is **not a greenfield design**. Its job is to (a) describe the system that was
actually built, (b) ratify or change the provisional targets those eleven tasks are blocked on, and
(c) resolve the cross-decision conflicts that no single sibling had authority to close.

Every claim below is sourced from the real decision document in-repo, not from a PR description.
That distinction is load-bearing: SM-R4's PR body still names `gte-base` while its merged
`DECISION.md` §1 selects `bge-base-en-v1.5`. Where a sibling's own text is quoted, it is quoted.

### The seed's two existing memory systems

| Surface | What it is today |
|---|---|
| `learn/machine.py:53` `LearningMachine` | Orchestrates 6 stores behind the `LearningStore` protocol (`learn/stores/protocol.py:16`): `recall` / `process`+`aprocess` / `build_context` / `get_tools` / `was_updated` / `schema`. `Curator` (`learn/curate.py:28`) does prune/dedup. |
| `memory/manager.py:46` `MemoryManager` | CRUD over `UserMemory`. `search_user_memories` (`:597`) is `last_n` / `first_n` / `agentic` — **not** vector search; no embedder. `SummarizeStrategy` (`memory/strategies/summarize.py:15`) is the only consolidation. |
| `db/schemas/memory.py:9` `UserMemory` | `memory`, `memory_id`, `topics[]`, `user_id`, `agent_id`, `team_id`, `input`, `feedback`, `created_at`, `updated_at`. No embedding, no `tenant_id`, no temporal fields. |
| Graph capability | `vectordb/lightrag/` only (graph-RAG, **not** bi-temporal). `tools/neo4j.py`, `tools/zep.py` are agent **tools**, not memory backends. |

Confirmed absent from the seed (SM-R11 §6.2, verified against current code): episodic tier,
vector-search-over-memory, temporal knowledge graph, bi-temporal model, async consolidation loop.

---

## Decision

### 1. Tier → substrate mapping

The CoALA tiers map onto the two first-class substrates as follows. This mapping is **not
aspirational** — it is cross-checked against the schema SM-R12 already implements (`model.py`,
`ddl/schema.sql`) and the bi-temporal scope SM-R10 already specified.

| Tier | Vector | Graph | Persisted model (SM-R12) | Notes |
|---|:--:|:--:|---|---|
| **working** | — | — | `WorkingMemory` (no table; session store) | Context only. Not embedded, not bi-temporal. Extends seed `SessionContext`. |
| **episodic** | ✓ | **(✓)** | `EpisodicRecord` | Raw non-lossy events. Vector for recall. **Graph presence is a pointer, not yet a node type** — see the qualification below. Carries SM-R6's identity contract (`revision`, `supersedes`/`superseded_by[]`, `boundary_*`, `refined`, `segmenter_version`). |
| **semantic — fact** | ✓ | ✓ | `SemanticFact` | SPO + `source_episode_ids[]` + `derived_by` provenance (SM-R7 §7). Bi-temporal. |
| **semantic — entity** | ✓ | ✓ | `SemanticEntity` | Graph node. Embeddable; **not** bi-temporal (identity is stable). |
| **semantic — relation** | — | ✓ | `SemanticRelation` | Graph edge. **The primary SM-R10 bi-temporal surface.** Not embedded. |
| **procedural** | ✓ | — | `ProceduralRecord` (`rule`/`skill`/`prompt_fragment`) | Rules/prompt. Extends seed `LearnedKnowledge`. Not bi-temporal. **Divergence resolved:** SM-R11 §4's map says "PROCEDURAL → rules/prompt" with no substrate; SM-R12 makes `ProceduralRecord` `Embeddable`. Ratified as **vector ✓** (SM-R12's shipped schema wins — procedural rules are retrieved by similarity, and the field exists). |
| *(links)* | — | ✓ | `MemoryLink` | First-classes seed `EntityMemory.relationships`. Bi-temporal. |

Two corrections to the issue's own strawman diagram, forced by what was actually built:

1. The issue draws `SEMANTIC → VEC` and `SEMANTIC → GRA` as one arrow. The real schema splits
   semantic three ways, and **only `SemanticRelation` is bi-temporal + graph-only**;
   `SemanticEntity` is embeddable but *not* bi-temporal. The ADR adopts SM-R12's three-way split.
2. **The graph is the bi-temporal system-of-record for *semantic contradiction*.** SM-R10 §9 states
   this as "the vector/**episodic** tier carries filter-only `valid_from`/`valid_to` … no
   `t_invalid`, no `expired_at`," which **conflicts with the schema SM-R12 actually shipped**.
   That conflict is real, was not surfaced by either sibling, and is resolved in **§B.6** — not
   here, and not silently. §B.6 is the governing text; this table reflects its outcome.

**Qualification on episodic → graph `(✓)`.** SM-R11 §4's map says "EPISODIC → vector + graph," but
SM-R12's shipped schema does **not** define an episode node: the only graph node type is
`SemanticEntity`, and `model.py` describes `graph_node_id` as an *"optional 1:1 promotion link into
the graph"* — **an untyped pointer with no defined referent.** SM-R9's KG shape does posit
"entity nodes + passage/episode nodes," but that is a prototype over a 2Wiki-derived KG, not
SM-R12's schema. **Ratified as `(✓)` — intended, not yet realised.** Defining the episode node type
(or ruling that episodes reach the graph only via `entity_ids[]`) belongs to **SM-G1** once SM-R2
picks an engine. This is the same class of gap as §B.7's undefined edge→memory join key: cross-tier
links are the least-specified part of the data model, and SM-C3/SM-G1 should settle them together
rather than one at a time.

**Traceability (issue Testing Notes: "every seed memory surface appears in the mapping, no
orphan").** Dispositions are distributed by topic, not all in one section: the six `learn` stores
and `SessionSummaryManager` in **§2.2**; `LearningStore`, `LearningMachine`, `MemoryManager`,
`Curator`, `SummarizeStrategy` in **§2.1**; `lightrag`, `tools/neo4j.py`, `tools/zep.py` in **§4**;
`UserMemory` in **§6**; `VectorDb` / `BaseDb` behind the substrate ports in **§5**. All 12 surfaces
SM-R11 §6.1 verified are covered. No orphans.

### 2. Extend-vs-new for `learn.LearningMachine` and `memory.MemoryManager`

**Decision: WRAP / EXTEND. Neither system is replaced or forked.**

This **ratifies** the calls two siblings already made, and it is a ratification, not an
independent re-derivation:

- **SM-R11 §6** decided *"WRAP / extend. `MemoryProvider` is a superset seam whose tiers delegate
  to wrapped seed components; it does not fork or discard them."* Its §6.1 disposition table
  covers **12 seed surfaces**, every one verified `file:line` against post-rebrand code, and
  **no surface is marked REPLACE**.
- **SM-R12 §5** decided *"extend the seed's shared-base dataclass pattern; do not fork"* —
  `UserMemory` **extends** into `EpisodicRecord`/`SemanticFact` rather than being superseded, and
  `EntityMemory` is **normalised** into entity/fact/episode/relation rows rather than replaced.

I land in the same place, so there is no conflict to flag.

#### 2.1 The path

1. **`LearningStore` (protocol) — EXTEND.** It is the seam to build on:
   `recall → read_tier`, `process → write_tier`, `build_context → MemorySlice.as_context`.
   `MemoryProvider` adds what it lacks: substrate-awareness, cross-tier fusion, `consolidate`.
2. **`LearningMachine` — REUSE (wrap).** Its `custom_stores: Dict[str, LearningStore]` is the DI
   point where the net-new **episodic** store and the vector/graph substrates slot in.
3. **`MemoryManager` — REUSE (wrap) + EXTEND.** Wrapped for write and non-vector search; extended
   with vector search via a `VectorSubstrate` (it has no embedder today).
4. **`Curator` — REUSE** inside `consolidate` (dedup + forgetting lane). The decay *model* is
   SM-R5's.
5. **`SummarizeStrategy` — REUSE + EXTEND** as the distillation step, extended with extraction
   and contradiction-resolution.
6. **Net-new (SM-R11 §6.2):** episodic tier, vector-search-over-memory, temporal knowledge graph,
   bi-temporal model, async consolidation loop.

#### 2.2 Tier ownership of the six `learn` stores

Ratifying SM-R11 §6.1's mapping so no seed store is orphaned:
`session_context → WORKING`; `user_memory` / `entity_memory` / `user_profile → SEMANTIC`;
`learned_knowledge` / `decision_log → PROCEDURAL`. **No EPISODIC store exists → NEW.**
`SessionSummaryManager` (`session/summary.py:63`) feeds WORKING-tier context and consolidation
input; it is noted, not restructured.

### 3. Read / write / async-consolidate data flow

**The interface is SM-R11's shipped `interface.py`, not the issue's sketch.** Issue #76 (and #86)
sketch `read(tenant, agent, query, types)`. SM-R11 §5.3 carries a deliberate-deviation callout
rejecting that literal, and SM-R13 already shipped a draft copying the strawman before correcting
it (SM-R13 §3: *"An earlier draft copied the issue-#86 strawman … now corrected"*). **This ADR
ratifies the real signatures and retires the strawman. Do not reintroduce it.**

Verified directly against `interface.py` on `origin/main` (@ `e266ac30`, post-merge):

```python
read(scope: Scope, query: MemoryQuery)            -> MemorySlice
write(scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt
consolidate(scope: Scope, tiers=None)              -> ConsolidationReport
read_tier(scope, tier, query) -> List[MemoryRecord]
write_tier(scope, tier, events) -> WriteReceipt
substrates() -> Dict[Substrate, bool]
```

`tenant` + `agent` fold into `Scope` (`tenant_id` mandatory on every call); the issue's `types`
becomes `MemoryQuery.tiers`. All verbs ship sync **and** async with the `a`-prefix (`aread`,
`awrite`, `aconsolidate`) matching the `learn`/`memory` convention. `vectordb/base.py` uses
`async_`; the adapter bridges the two prefixes internally.

**The substrate-port write primitive — take the POST-MERGE signature, not the branch one.** PR #20's
review found a **Major** on the write side that SM-R11's own self-review had missed: `invalidate`
took a single time and did an **in-place `t_invalid` mutation**, which SM-R10 §6 had already proved
wrong via its P3 5a/5b eval (a later fact clobbers an already-set bound; belief-evolution stops
reconstructing). The merged port is:

```python
GraphSubstrate.invalidate(scope, edge_id, t_invalid, at=None)   # + ainvalidate
```

**Two times** — `t_invalid` = valid-time the fact became false in the world; `at` = transaction-time
the system recorded it — and **expire-and-reinsert (SCD-2)**, never in-place mutation. A production
backend delegates to SM-R10's `upsert_edge` / `invalidate_edge` / `retract_edge`; reads select
current belief (`expired_at IS NULL`, SM-R10 §7 5a). Verified by the merged swap test
(*"expire-and-reinsert (SCD-2), no in-place clobber"*: two invalidations accumulate rows, exactly
one current belief, tightest bound applied, original untouched).

**ONE WRITE MODEL (ratified).** Merged SM-R11 adds the constraint that **every** bi-temporal graph
write — *including contradictions produced by consolidation (SM-R7)* — resolves through that same
SCD-2 primitive, never a second in-place path. Ratified as stated. See **§B.6** for the row-vs-edge
boundary this must be read against.

**Flow:**

- **write (write-back), request path.** Append raw events, default EPISODIC. Cheap, non-lossy, no
  distillation. Inline segmentation prior only: `time_gap` (900 s) + `max_turns_per_episode` (40),
  O(1), no model call (SM-R6 §4).
- **read (feed-forward), hot path.** `embed query → BM25 → fuse → [optional rerank] → [filter]`.
  **Zero query-time LLM** (SM-R8 §1.4). Fusion is **weighted / relative-score**, `vector_weight
  ≈ 0.4–0.5` (SM-R8 §1.1); reranker **off by default**; PPR **off by default, per-tenant flag**
  (SM-R9). `MemoryQuery.as_of` applies SM-R10's two-sided point-in-time predicate.
- **consolidate (write-back → semantic), async / sleep-time lane.** Async is the primary lane.
  Per SM-R7 §1: **mem0-style extract + merge is the mandatory backbone**, with an **optional**
  sleep-time reflection layer. Whole-corpus/rolling-blob summarization is **rejected as the
  primary mechanism** (provenance precision 0.35 vs 0.85 — SM-R7 §5.1). Provenance
  (`source_episode_ids`) is populated at extraction and **unioned forward** on UPDATE/NOOP.
  Conflicts supersede bi-temporally; nothing is destroyed. This lane also runs SM-R6's async
  re-segmentation under the stable-id + supersede/revision identity contract.

**Ordering rule (ratified from SM-R5 §6):** decay-pruning **must run after** the
consolidation/reflection pass, never before — otherwise an episodic row can be pruned before its
knowledge is consolidated into the semantic tier.

### 4. Temporal-graph placement vs `lightrag`

**Decision: the temporal knowledge graph is a NEW backend behind the `GraphSubstrate` port.
`lightrag` is not promoted to be it.**

Rationale, from source: `lightrag` is graph-RAG (`vectordb/lightrag/lightrag.py` — verified by
SM-R9 as a thin HTTP client to an external LightRAG server, modes local/global/hybrid, **no PPR**)
and is **not bi-temporal**. The bi-temporal four-timestamp model (SM-R10 §5) is the reason to pay
for a graph at all. `lightrag` remains available as a `GraphSubstrate` *candidate* backend and is
not deleted.

**The engine is explicitly NOT chosen here.** Neo4j vs Graphiti vs Cognee vs FalkorDB/Kuzu is
SM-R2 ([#77](https://github.com/Saturam-Inc/aicoe-tracker/issues/77)), which depends on this ADR
and has not run (0/5 ACs).

> ⚠️ **Live risk for SM-R2 to close, recorded here so it is not inherited as settled.** Two
> siblings have already built and measured against **Neo4j specifically, ahead of SM-R2**:
>
> - **SM-R10** proved its spec on real Postgres **and** real Neo4j, and ships Cypher predicates —
>   but kept itself *deliberately engine-agnostic* (§11: the Neo4j use is *"a proof that the model
>   is engine-agnostic, not an engine recommendation"*). Low risk.
> - **SM-R9** did **not**. Its integration route assumes `gds.pageRank(sourceNodes=…)`, and its
>   own §Decision says so bluntly: *"issue #77 is OPEN with 0/5 acceptance criteria … and its own
>   candidate list includes FalkorDB, Kuzu, `lightrag` and Cognee — none of which provide
>   `gds.pageRank(sourceNodes=…)`."* **Three of five candidates are not GDS-capable.** SM-R9's
>   Route B (hand-rolled sparse power-iteration over an exported adjacency) is explicitly
>   *"moderate — unbudgeted; adds a component SM-H4 does not currently plan for."*
>
> **Ruling:** Neo4j is **not** pre-selected by this ADR. SM-R2 must weigh SM-R9's concrete
> capability requirement (*personalized* PageRank with source-node seeding) as an **input**, and
> must cost Route B if it lands on a non-GDS engine. SM-H4 should not begin PPR integration until
> SM-R2 closes, or must scope Route B explicitly.
>
> One further asymmetry SM-R2 must record: Postgres can enforce append-only history with the
> `memory_edge_append_only` trigger (SM-R10 §6.1, measured 8/8); **Neo4j has no
> property-immutability constraint**, so parity needs either APOC triggers (unverified) or a
> write-through repository plus a scheduled invariant audit (verified, but detection-after-the-fact
> and strictly weaker). SM-R10 asks that this asymmetry *"be recorded wherever the engine choice is
> ratified"* — it is recorded here, and SM-R2 owns closing it.

### 5. Dual-mode (Mode A / Mode B) placement behind `MemoryProvider`

**Decision: mode is a config choice at one construction call site. Ratified.**

```python
MemoryProviderConfig(provider, mode=CO_LOCATED|EXTERNAL, vector={}, graph={}, fusion={}, options={})
build_memory_provider(config) -> MemoryProvider     # the ONLY construction call site
register_provider(key)                              # adapters self-register on import
```

- **Mode A** wires co-located in-VPC substrates (pgvector + graph); **Mode B** wires external
  services. Both satisfy the same `VectorSubstrate` / `GraphSubstrate` ports, so the provider and
  all consumers are unchanged.
- Mode also selects the embedder: **Mode A `bge-base-en-v1.5` @768**, **Mode B Azure
  `text-embedding-3-large` truncated to 1024** (SM-R4 §1). Mode B is **optional and not a quality
  upgrade** — it statistically *ties* Mode A (Holm p=0.21).

**Verification — I did not take the config-only claim on faith.** Re-read
`swap_test.py` and `results/swap_results.json` on `origin/main` (@ `e266ac30`, post-merge):

- The proof is **structural**: one function, `consumer_workload(provider, scope)`, is run verbatim
  against providers built from two configs differing **only** in `provider` + `mode`. It touches
  only the abstract type and never branches on provider identity. If the seam leaked, one function
  could not drive both.
- `swap_results.json`: **`"passed": true`, `"deterministic": true`, 12/12 checks ok.** (SM-R11's
  own §Phase-status prose still reads "10/10" — its artifact has since grown to **12**, so the
  narrative now trails its own results file by two. The artifact is authoritative; all 12 pass.
  Flagged upstream; not a defect in the seam.) Checks include
  *"swap is config-only (no call-site change) — consumer_workload run verbatim on 2 providers"*,
  tenant isolation (**leaked=0**), both SM-R10 two-sided bounds, SM-R7 dual-substrate write, and
  sync+async parity.
- Registry confirmed in `adapters.py`: `@register_provider("co_located")` and
  `@register_provider("external_stub")`.

**Honest limit on that proof, stated rather than glossed:** `external_stub` is, per its own
docstring, *"a minimal contract-complete stub standing in for an external service."* The swap test
proves the **seam shape** is config-only. It does **not** prove a real external Mode B deployment
is config-only — no network, no real provider SDK, no real latency is exercised. **SM-C1 must
re-prove config-only swap against a real external backend.** The claim is ratified as far as it
was measured, and no further.

### 6. `UserMemory` gaps for SM-R12 / SM-C3 — already filled; citing where

The issue asks that the gaps be *recorded*. They have since been *closed* by SM-R12, so this ADR
records the actual field list rather than re-deriving a different one. Source: SM-R12 §4.2
(`model.py`) and `ddl/schema.sql`.

| Gap (issue #76) | Filled by | Fields |
|---|---|---|
| No embedding | `Embeddable` mixin | `embedding`, `embedding_dim` (768 A / 1024 B), `embedding_version` (`bge-base-en-v1.5/d768/cos/v1`) |
| No `tenant_id` | `TenantScoped` mixin | **`tenant_id` required** (SM-R3 partition key), `user_id`/`agent_id`/`team_id`/`session_id`, `created_at`, `updated_at`, `metadata` |
| No temporal | `BiTemporal` mixin | `t_valid`, `t_invalid`, `expired_at`, `superseded_by` (+ inherited `created_at` as transaction-time-in). Per **§B.6**, `expired_at`/`superseded_by` on the **episodic** tier are written only by SM-R6 re-segmentation and SM-R5 archival — never by contradiction resolution. |
| *(added by SM-R5)* | `Decaying` mixin | `importance` (default 0.5), `last_accessed_at` |

**`strength` is deliberately absent** — SM-R5 §5 lists it under *"Deferred (do NOT add now)"*
(negligible measured benefit; `last_accessed_at` + `importance` capture the behaviour with one
fewer stateful field), and SM-R12 §10 follows that. See the correction in §9.6 — two open tickets
still ask for it.

Status is stronger than "recorded": SM-R12's `roundtrip_test.py` runs write→read→assert for all 6
persisted tiers on **real embedded PostgreSQL + pgvector**, including point-in-time via SM-R10's
full 5a predicate, the insert-only guardrail, CHECK constraints on all four bi-temporal tables, and
cross-tenant isolation (0 leakage). **SM-C3 promotes these shapes into core schemas + migrations.**

---

## Part A — Ratification of the provisional targets

Eleven tasks are blocked on this section. Each row is **RATIFIED**, **CHANGED**, or **HELD OPEN**.

**Coverage (auditable — every provisional row in every source, so a gap is visible not silent):**

| Source | Provisional rows | Where ratified |
|---|---|---|
| SM-R3 | memories/tenant · tenants · latency · **recall@10** | A.1 · A.1 · A.4 · **A.3b** |
| SM-R4 | scale · language · quality floor · latency · storage · **offline** | A.1 · A.2 · A.3 · A.4 · A.4 · **A.3c** |
| SM-R5 | decay params · acceptance targets · importance-calibration gate | A.7 · A.8.1 · A.8.2 |
| SM-R6 | segmentation params · model/pricing · F1/Pk targets · SLM re-test | A.7 · A.7 · A.7 · A.8.3 |
| SM-R7 | CI quality gate | A.7 |
| SM-R8 | 2-leg fusion · **3-leg fusion** · rerank budget · latency budget | A.6 · **A.6 (HELD OPEN)** · A.7 · A.4 |
| SM-R9 | PPR adopt thresholds · default-off · engine dependency | A.7 · A.7 · §4 |
| SM-R10 | point-in-time accuracy · scale/latency · erasure question | A.7 · A.1/A.4 · B.3 |
| SM-R11 | wrap-vs-replace · seam/mode | §2 · §5 |
| SM-R12 | scale/dim targets | A.1 · B.4 |
| SM-R13 | gate thresholds | A.5 · A.7 |

### A.1 Scale — RATIFIED, with a unit reconciliation and an arithmetic correction

**Units first, because SM-R3 and SM-R4 do not use the same one and nothing downstream reconciles
them.** SM-R4 §2 ratifies *"10M memory **vectors**"*; SM-R3 ratifies *"**memories** per tenant
100k–1M."* These are **not the same quantity**, and treating them as interchangeable — as an
earlier revision of this ADR did — mis-sizes the storage tier.

**Why they differ (structural, from SM-R12 §4.4):** one ingested memory does not produce one
vector. **Four tiers are `Embeddable`** — episodic, semantic.fact, semantic.entity, procedural — so
a single episode yields an episodic vector *plus* the vectors for every semantic fact and entity
consolidation derives from it. Two measured data points from siblings bracket the effect:

| Source | Measured | Direction |
|---|---|---|
| SM-R7 §5.2 (LOCOMO) | 76 episodes → **29** atomic facts | facts **compress** (~0.4× per episode) |
| SM-R9 §Prototype (2Wiki) | 285 passages → **3,393** entities (all embedded) | entities **expand** (~11.9× per passage) |

So the fan-out is **> 1× and entity-dominated**, but its magnitude is genuinely unknown for our
corpora. Two things move it and neither is measured here: **entity dedup** (entity nodes are shared
globally, so the marginal rate falls as a tenant's corpus grows), and **fact density** — SM-R9
§Measurements found dialogue yields **7.56 triples/100 words vs 17.82** on Wikipedia, so SM-R9's
11.9× is an upper bound drawn from entity-dense factual text, not conversational memory.

**Ratified:**

| Parameter | Ratified | Unit |
|---|---|---|
| Total vector **substrate** | **10M** (SM-R4 §2) | **vectors** |
| Memories per tenant | **100k – 1M** (SM-R3) | **memories** |
| Tenants | **up to ~50** (SM-R3) | — |
| memory → vector fan-out | **NOT RATIFIED — must be measured** | ratio |

**HELD OPEN — the fan-out ratio.** SM-C3/SM-V1 must measure vectors-per-memory on real tenant data
(with graph memory both on and off, since entities dominate) and report it before infrastructure is
sized. Until then, **plan capacity in vectors, not memories**, and treat any memory-count target as
un-converted.

**Arithmetic correction (unchanged, and now unit-correct):** the ceilings do not multiply. Even at
a 1:1 fan-out, 50 tenants × 1M memories = **50M vectors**, five times the 10M envelope — and at any
realistic fan-out the gap is far wider. **10M vectors is the v1 sizing envelope; the 1M/tenant and
~50-tenant ceilings are not simultaneously reachable.** Crossing 10M vectors triggers SM-R4 §6's
`halfvec` path and SM-R3's Qdrant escalation — and note this trigger now fires at a **much lower
memory count** than the raw numbers suggest, which is the practical cost of the unit confusion.

Multi-tenancy layout is **RATIFIED as SM-R3 decided**: pgvector **declarative partitioning by
`tenant_id`**; the shared-table `tenant_id` filter is **rejected on data** (recall 0.80 → 0.56 →
0.22 as tenants go 1 → 10 → 50). SM-R12's DDL already implements `PARTITION BY LIST (tenant_id)`.

### A.2 Language — RATIFIED

**English-only** for v1. SM-R4 §6 documents the switch (`bge-m3` / `multilingual-e5-base`, requires
re-benchmark). A multilingual requirement re-opens SM-R4, not this ADR.

### A.3 Quality floor — RATIFIED and met

**nDCG@10 ≥ 0.88** on the memory-representative set. Met: `bge-base` **0.911** [0.888, 0.934].
`bge-base` significantly beats `bge-small`@384 (Holm p=0.002) and `gte-base` (Holm p=0.016), and
ties `bge-large`/`e5-base`/Azure. **Ratified embedder: `BAAI/bge-base-en-v1.5` @768 (Mode A);
Azure `text-embedding-3-large`@1024 (Mode B, optional).**

### A.3b Retrieval recall — RATIFIED, conditional on the operating point

SM-R3's fourth provisional target: **`Recall@10 ≥ 0.95`**. Ratified — **with a condition, because
it is not met at the operating point A.1 ratifies.**

| SM-R3 run | Layout | recall@10 | p95 |
|---|---|---|---|
| Run B — 20k/tenant × 50 | pgvector partition | **1.000** | 13.7 ms |
| Run A — 100k/tenant × 10 | pgvector partition | **0.881** | 15.2 ms |

Run B clears 0.95; **Run A does not** — and Run A (100k/tenant) sits inside the ratified 100k–1M
band. SM-R3 names the cause and the lever: measurements are at `ef_search = 64`, and *"pgvector's
per-tenant recall (0.88–0.95) trails Qdrant's (1.0) — an operating-point gap that a higher pgvector
`ef` narrows at some latency cost."*

**The obvious mitigation is NOT measured — say so rather than assume it.** SM-R3's exact words are
that a higher `ef` **"narrows"** the gap *"at some latency cost."* It does **not** show the gap
**closes** to 0.95 at 100k/tenant, and there is **no `ef_search` sweep in SM-R3's `results/`.** An
earlier revision of this ADR instructed SM-V1 to *"calibrate `ef_search` upward until 0.95 is met"*
— that presumed a reachability the evidence does not establish.

**Ruling — a two-step gate, not a target plus a hope:**

1. **`Recall@10 ≥ 0.95` is ratified as the platform target**, and **`ef_search` is a per-deployment
   tuning parameter, not the benchmark's constant 64.**
2. **It is CONDITIONAL on measurement.** SM-V1 must run an `ef_search` sweep at the deployed
   per-tenant size and report recall **and** p95 together. There is budget headroom to spend
   (15.2 ms measured against A.4.1's 50 ms), but how much recall that buys is unknown.
3. **If the sweep cannot reach 0.95 within the 50 ms budget**, the ratified position becomes:
   pgvector's floor is its **measured 0.881** at that operating point, and **0.95 becomes the
   SM-R3 escalation trigger to Qdrant per-tenant collections** (measured 1.000 / 13–14 ms). It is
   **not** grounds to quietly lower the floor and stay on pgvector.

The shared-table `tenant_id` filter stays rejected regardless (0.217 at 50 tenants).

### A.3c Offline capability for Mode A — RATIFIED, with a named trap

SM-R4's sixth provisional row: **offline capability is REQUIRED for Mode A.** Ratified — Mode A is
the air-gapped deployment path (§5), so this is a hard constraint, not a preference. `bge-base` via
`sentence_transformer` / `fastembed` (ONNX) satisfies it; `vllm` / `ollama` are the in-VPC GPU
server options that also satisfy it (and are the A.4 latency mitigation).

**Carried forward because it is a live trap, not a footnote:** SM-R4 §5 records that
**`HuggingfaceCustomEmbedder` is a remote HF Inference API client despite being listed among the
self-hostable embedders.** Selecting it in a Mode A deployment silently breaks the air-gap.
**SM-C1/SM-V2 must reject it at config validation for `mode=CO_LOCATED`**, not merely document it.
The same §5 records that `SentenceTransformerEmbedder` lacks `trust_remote_code` / `device`
passthrough (patch saved in SM-R4 `findings/`) — needed for CPU pinning and for `nomic`.

### A.4 The p95 ≤ 50 ms latency gap — CHANGED (this is the explicit ruling SM-R4 asked for)

SM-R4 §2 flags this as an **open gate for R1**, with three named options: relax the target, add
GPU/vLLM or a query-embed cache, or accept 384.

**Root cause — the two "50 ms" targets are not the same budget.** Reading both sources:

- **SM-R4's** p95 ≤ 50 ms is **query-embedding** latency. **Not met by any 768-dim model on CPU,
  even idle** (bge-base p95 482 ms; idle gte ≈ 276–316 ms). ONNX does not rescue it (measured
  *slower* per-item than PyTorch, quality-identical). Only **bge-small@384 via ONNX** meets it
  (p50 13 ms / p95 19 ms).
- **SM-R8's** p95 < ~50 ms is the **retrieval hot path** and **explicitly excludes** query
  embedding: *"Query-embedding latency is provider-dependent (SM-R4) and excluded here as
  option-invariant."* Measured 6–24 ms p50 for embed-free BM25 + fusion; SM-R3 measures 13–21 ms
  p95 for the partitioned vector search.

They were never one budget. Ratifying them as one is what produced the apparent contradiction.

**Ruling:**

1. **RATIFIED — retrieval hot path (post-embedding: vector search + BM25 + fusion): p95 ≤ 50 ms
   in-VPC.** Met by both SM-R3 and SM-R8 measurements. This is the budget SM-R13's gate holds.
2. **CHANGED — query embedding is a separate, named budget, and ≤ 50 ms is NOT ratified for it on
   CPU.** The quality floor (A.3) wins over the latency target: **do not drop to bge-small@384.**
   384 is significantly worse (Holm p=0.002) and the nDCG floor is the harder requirement.
3. **Mitigation is mandatory, not optional, for latency-sensitive deployments:** GPU/vLLM-served
   query embedding (SM-R4 confirms `vllm`/`ollama` are present as in-VPC server options), and/or a
   query-embed cache. Mode A's `sentence_transformer` CPU path is for correctness and air-gapped
   fallback, not for a 50 ms SLO.
4. **NAMED, ACCEPTED EXCEPTION — CPU-only air-gapped Mode A does not meet a 50 ms read path and is
   blessed as such for v1.** Such a deployment either accepts a materially higher end-to-end p95
   (measured ~0.5 s, dominated by query embedding) or opts into bge-small@384 with the documented
   quality loss. This is a per-deployment choice, recorded rather than hidden.
5. **No composite end-to-end p95 number is invented here.** SM-C1/SM-H3 must **measure and report**
   `end_to_end_read_p95 = query_embed_p95 + retrieval_p95` on the production embedder and target
   hardware; that measurement, not this ADR, sets the SLO. SM-R4 §7 already requires this
   reconciliation.

Storage budget is **RATIFIED, and it is per-vector**: ≤ ~80 GB for the vector substrate at **10M
**vectors** (768d float32; 40 GB `halfvec`). Per A.1 this is **not** ≤80 GB at 10M *memories* — the
memory→vector fan-out is unratified, so this budget converts only once SM-C3/SM-V1 measure it.

### A.5 The SM-R13 "1500 ms" gate — NO ACTION NEEDED (already corrected upstream)

The 30× discrepancy is **stale**. SM-R13's merged `PLAN.md` §4 reads:
`max_query_p95_ms=50` *("SM-R8 ratified hot-path p95 `<~50 ms`, retrieval read only — **was
mis-cited as 1500**")*, and §2.5 scopes it to `provider.read()` only, with end-to-end QA latency
reported separately and **not** gated on that budget. SM-R13 fixed this before merge.

**Ratified:** SM-R13's gate at `max_query_p95_ms=50` against the **retrieval hot path only** is
correct and consistent with A.4.1. Nothing to resolve. Recorded because the task brief for this ADR
still described the 1500 ms value as live — see §10.

### A.6 SM-R8's RRF-vs-weighted at 3 legs — HELD OPEN (deliberately not ratified)

- **RATIFIED for the 2-leg (dense + BM25) case: WEIGHTED / relative-score fusion**,
  `vector_weight ≈ 0.4–0.5` (consider ~0.3 for memory-heavy tenants — LongMemEval peaks there).
  Weighted significantly beats RRF on **all 4 datasets**, and the finding **survived
  re-validation on SM-R4's actual ratified embedder** on all 3 re-run datasets (p < 0.01).
- **HELD OPEN for the 3-leg (+graph) case.** SM-R8 asked to keep it provisional; I am declining to
  ratify it, because the evidence is genuinely contradictory:

| Measurement | Graph leg | Result |
|---|---|---|
| SM-R8 §3.5 | synthetic k-NN + real PPR | **weighted** +0.076 (p=0.0001); survives decorrelation at +0.060. 3-leg RRF fell *below* 2-leg RRF. |
| SM-R9 §Fusion | **real** LLM+OpenIE KG + PPR | **RRF** numerically ahead at every weight; weighted's optimum degenerate at w=0.0. **n.s. at n=30 (p=0.26)**. |

SM-R9's 95% CI **excludes** SM-R8's effect size, so the two are *not reconcilable as one underlying
effect*. Neither is conclusive. SM-R9 also records that both proposed mechanisms for RRF's local
advantage were **refuted by measurement**, so there is no principled tiebreak either.

**Ruling: HELD OPEN, gated on SM-G9.** SM-H2/H3 must measure 3-leg weighted-vs-RRF (and per-leg
*calibrated* weighted) against SM-G9's real graph retriever before either is ratified. Both
strategies stay wired and N-leg-capable (`fusion.RRFFusion(k=60)`, `fusion.WeightedFusion(w)`).
**Do not adopt RRF automatically when the graph leg joins**, and do not read either result as
settling it. The caveat is carried forward, not discharged.

> **Related, and now MIS-owned — R1 arbitrates.** SM-R11's `co_located` adapter defaults to
> **weighted** for a **vector + graph** pairing. SM-R11 §9 is honest that this is *"a
> mechanism-based transfer to re-validate against real backends, not a measured result for this
> exact pairing"* — SM-R8's legs were dense+BM25(±graph), and SM-R9's were dense+PPR. **No
> measurement covers vector+graph directly.**
>
> Merged SM-R11 §9 assigns that measurement: *"SM-R13 (#19) owns measuring vector+graph
> weighted-vs-RRF on this seam."* **That assignment does not work.** PR #19 merged at **09:50Z, ~3.5
> hours before #20**, and SM-R13's `PLAN.md` §6 explicitly puts *"graph-traversal / PPR retrieval
> provider + bridge-passage graph dataset + bi-temporal-graph PPR probe"* **out of scope, deferred
> to SM-X7 (#143)**. So the work was handed to a closed task that had already declined that exact
> scope — it would sit unowned.
>
> **Ruling: the owner is SM-X7 ([#143](https://github.com/Saturam-Inc/aicoe-tracker/issues/143)),
> not SM-R13.** SM-X7 already owns the graph/PPR retrieval provider, the bridge-passage dataset and
> the bi-temporal-graph PPR probe — the same measurement surface — and SM-R13 §2.6 names it as the
> owner of exactly this deferral. This is the same class of call as **§B.2**, and it is R1's to
> make rather than to leave to two merged documents that disagree. The vector+graph default remains
> **provisional**, gated on that measurement alongside the 3-leg question above.

### A.7 Other provisional values — ratified as-is

| Source | Value | Ruling |
|---|---|---|
| SM-R5 | `base_half_life` **5 d**, `imp_factor` **4.0**, α=β=1.0, prune floor 0.05, default importance 0.5 | **RATIFIED as defaults**, with A.8 below |
| SM-R6 | `time_gap` 900 s, `max_turns` 40, `min_turns` 3, drift `threshold_c` 0.75 / block 3, EM-LLM γ=1.0 / modularity / radius 3 | **RATIFIED** |
| SM-R6 | segmentation acceptance F1 ≥ 0.70, Pk ≤ 0.35 on a held-out **real** set | **RATIFIED**; only the LLM currently approaches it (0.62). LLM is the async **quality default**; cheap methods are the cost fallback. Hybrid stays **withdrawn**. |
| SM-R7 | CI gate: `provenance_validity == 1.0`, `provenance_precision ≥ 0.80`, `conflict_accuracy ≥ 0.90`, `dedup ≥ 0.75`, `fact_recall ≥ 0.95` | **RATIFIED** |
| SM-R8 | rerank budget ≤ 300 ms added, GPU/served only; **off the default hot path** | **RATIFIED** (CPU measured 2.0–2.5 s MiniLM, ~40–50 s bge-reranker-v2-m3) |
| SM-R9 | PPR adopt threshold: chaining all-hops gain ≥ 0.15 over dense; default **off**; on above ~50% multi-hop query mix | **RATIFIED** — the n=30 unratifiability caveat is **discharged**: SM-R9 re-measured at **n=120**: hybrid **+0.208** (p<0.0001), hippo **+0.250** (p=0.0001). Thresholds must be expressed as **gains over dense**, never absolute scores. |
| SM-R10 | point-in-time reconstruction accuracy target **1.00** | **RATIFIED** (measured 30/30 across five backends; single-time baseline 17/30) |
| SM-R13 | `min_qa_accuracy=0.50`, `min_recall_at_k=0.60` | **RATIFIED** as v1 CI floors |

### A.8 Two ratifications carrying explicit conditions

1. **SM-R5's decay targets are ratified as *direction*, not as met.** SM-R5 §7 is candid that
   decay-resistance (idle-30d+ ≥ 0.50) and embedding recall (≥ 0.90) are **not met at the current
   defaults** (0.362 / 0.782 synthetic; 0.029 / 0.703 under real embeddings). Ratified: the
   **model** (importance-weighted exponential recency, ranking-primary) and the **defaults as a
   starting point**. SM-E4 must re-tune `imp_factor` / `base_half_life` upward on the production
   embedder and **enable confirmed-only reinforcement**, which SM-R5 measured lifts idle-30d+
   from 0.362 to **0.642** on its own. The stale-hit and false-forget goals — the actual point of
   decay — **are** met.
2. **Gated on importance-scorer calibration.** SM-R5 §2.4 shows the salience *targeting* benefit
   shrinks ~65% as importance decorrelates from ground truth. **Ratification is conditional on
   SM-E5 delivering a calibrated importance scorer.** Until it exists, the decision degrades
   gracefully to "longer effective half-life" — still ahead of pure-recency peers, without the
   targeting edge. SM-R1 was asked to gate on this; it is gated.
3. **SM-R6's on-prem SLM path is NOT foreclosed, and the re-test is commissioned.** SM-R6 §6 asks
   SM-R1 to commission a GPU + per-window SLM re-test before concluding SLMs are unfit; its only
   measurement (F1 0.335, llama3-8B/CPU/whole-conversation) is explicitly worst-case.
   **Commissioned** — owner SM-E3, and it must not be read as a Mode-A veto. Note SM-R9's sharper
   point: **no sibling has evaluated an SLM on OpenIE triple extraction**, which is a third task
   again, and SM-R7's `AZURE_OPENAI_EXTRACTOR_DEPLOYMENT` hook is a cheaper *Azure* (Mode B)
   deployment, **not** an on-prem Mode-A SLM. Plan graph-extraction economics at frontier prices
   until an SLM is actually measured.

---

## Part B — Explicit conflict resolution

The issue's Testing Notes require internal consistency. **B.1–B.5** are tensions a sibling had
already surfaced but nobody had authority to close. **B.6–B.7** were found by this ADR's own
second-pass audit and had been surfaced by *no one* — B.6 in particular is a live disagreement
between two merged, tested specs. None is left silent.

### B.1 SM-R5 vs SM-R8 on recency — RESOLVED, and the naming question is CORRECTED

**Not a contradiction.** SM-R8 §3.6 already established this by reading SM-R5's actual numbers, and
I confirm it from both sources: **both decisions independently measured that adding recency to
ranking costs retrieval quality.** SM-R5's own `no_decay` baseline has the *highest* nDCG/recall of
every strategy it tested (0.893 → 0.903), and SM-R5 §2.3 says so directly — *"no_decay
significantly beats it on raw recall/nDCG … the expected cost of decay, paid to buy the staleness
and growth wins."* They diverge only in what that trade buys:

- **SM-R8** optimizes single-query nDCG@10 on a static corpus. Nothing there offsets the cost →
  recency weight **0** on the hot path.
- **SM-R5** optimizes a memory-*tier* objective over a 90-day horizon (bounded growth, stale-hit
  reduction). It accepts the same cost deliberately.

**Ratified guidance (one voice, so nothing inherits split direction):** consume SM-R5's
decayed-relevance/importance output as a **write-time prior**. Do **not** additionally re-enable a
per-query additive recency rescore on top of retrieval fusion — SM-R8 measured that hurts
independently of SM-R5's mechanism (`recency_weight` monotonically degrades 0.8934 → 0.7265).
Recency remains available as an **optional pre-filter** for explicitly time-scoped queries.

**The epic-naming question — CORRECTED, not merely confirmed.** SM-R8 §3.6 flagged an unresolved
"epic-reference mismatch" (SM-R8 → SM-E7; SM-R5 → SM-E4/SM-E9) and asked whoever owns epic planning
to confirm whether these are the same task under different names. **They are not the same task, and
there is no mismatch.** Verified against the tracker:

| Ticket | Title |
|---|---|
| SM-E4 ([#111](https://github.com/Saturam-Inc/aicoe-tracker/issues/111)) | Episodic recency-decay scoring |
| SM-E5 ([#112](https://github.com/Saturam-Inc/aicoe-tracker/issues/112)) | Episodic importance/salience scoring |
| SM-E6 ([#113](https://github.com/Saturam-Inc/aicoe-tracker/issues/113)) | Episodic relevance scoring |
| SM-E7 ([#114](https://github.com/Saturam-Inc/aicoe-tracker/issues/114)) | **Composite** episodic scoring |
| SM-E9 ([#116](https://github.com/Saturam-Inc/aicoe-tracker/issues/116)) | Episode forgetting / pruning |

They are a **pipeline, not duplicates**. #111 says its output is *"expose it as a ranking signal to
composite scoring (SM-E7)"*; #114 says it *"combine[s] recency (SM-E4) + importance (SM-E5) +
relevance (SM-E6) into one normalized episodic score."* So SM-R5 correctly hands its **decay curve**
to SM-E4 and its **pruning policy** to SM-E9; SM-R8 correctly hands **placement and weighting** to
SM-E7, which composes E4's output. Integration into retrieval ranking is SM-H5 (#135).

**Ratified division of labour:** SM-E4 owns the decay function and reinforcement-on-access. SM-E7
owns the composite and its weights — and per B.1 it consumes SM-E4's decayed score as a **write-time
prior**, with `w_recency = 0` for any additive per-query rescore on the fused hot path. SM-E9 owns
pruning under the A.7/SM-R5 recency floor. **SM-E7, SM-E4 and SM-E9 do not inherit split guidance.**

### B.2 SM-R9's PPR-over-bi-temporal-projection ownership — CONFIRMED (already assigned)

**Owner: SM-H4 ([#134](https://github.com/Saturam-Inc/aicoe-tracker/issues/134)), with SM-G
supplying the temporal graph. Confirmed by this ADR.**

It was **not** left unowned a second time — SM-R9 records this as **RESOLVED (2026-07-29)**, and
#134 exists and is OPEN. The rationale is right and is ratified: SM-G builds the graph, SM-H4 runs
PPR against it, so ranking quality over a filtered projection belongs with the **consumer**, not
the producer. It is explicitly **not** SM-R10's — SM-R10 §11 excludes ranking from scope (*"This
spec only adds the valid-time filter columns, not ranking"*), which is exactly why the earlier
assignment to SM-R10 was wrong.

**Status stays honest:** ownership is settled, **the measurement is not done**. SM-R9's AC #4
remains `[~]`. Binding constraint carried into SM-H4: the projection **must** use SM-R10 §7's full
5a predicate verbatim, including `coalesce(e.t_invalid, ∞)` and `e.expired_at IS NULL`. Dropping
the first silently removes every currently-true (open-ended) edge; dropping the second readmits
superseded and retracted rows. Both change graph **topology**, which is what PPR ranks over — so
both are **correctness** bugs, not cosmetic. Expect degradation to be worst where edge churn is
highest, i.e. episodic memory.

### B.3 SM-R10's edge-lifecycle-across-siblings — CONSISTENT with the §2 WRAP answer

SM-R10 §12.1(a) reconciled with SM-R5 by quoting SM-R5 §6 verbatim: decay-pruning runs **after**
consolidation and must **archive/tier rather than hard-delete**. **Confirmed consistent with §2's
extend/wrap ruling**, and the check the brief asks for is real — a "new layer that subsumes the
seed" answer *would* have undermined it, because a fresh store could plausibly have been given its
own delete path. It is not.

Ratified, adopting SM-R10's graph-tier refinements unchanged:

- **Decay has no authority over bi-temporal graph edges at all.** SM-R5 is a ranking model over the
  episodic tier's vector substrate; the §6.1 append-only trigger makes edge removal structurally
  impossible.
- **"Archive/tier rather than hard-delete" maps onto `expired_at`** (R2 retract). An archived row
  drops out of 5a but stays reconstructable through 5b.
- **The one sanctioned hard `DELETE`** is the consolidation-owned conflict / explicit-forget path.
  For a bi-temporal edge it should still be expressed as R2 retract wherever the requirement is
  "stop believing this."
- Because `Curator` is **REUSED inside `consolidate`** rather than replaced, the ordering rule is
  enforced at the seam, not by convention.

**Answering SM-R10's direct question to SM-R1** (§12.1(a): *"Flagged for SM-R1 to confirm whether
erasure needs its own path"*): **yes, and it is out of scope for v1.** Right-to-erasure is a
*tenant-data* operation, categorically different from invalidate-not-delete, and it cannot be
expressed as `expired_at` — SCD-2 retains the row by design. **Recorded as a named gap with no
owner yet**; it needs its own ticket before any tenant-facing GDPR/erasure commitment. It is
deliberately not folded into SM-R5, SM-R10, or SM-G5.

### B.4 Dimension/schema simplification (`vector(768)`, Mode A only) — BLESSED for v1 + follow-up

**Confirmed from source, not assumed.** `ddl/schema.sql` hardcodes `vector(768)` on all four
embeddable tables, with the header comment: *"Embedding dim = 768 (SM-R4 Mode A default). Mode B:
swap vector(768) -> vector(1024)."* SM-R12 §9 states `embedding_dim` is *"a per-deployment
constant … not per-row."* So **one deployment cannot serve both modes today.**

**Ruling: BLESSED as acceptable for v1, with a named follow-up.** Rationale: mode is a
*deployment-time* choice (§5), Mode B is optional and a measured quality **tie** with Mode A, and
mixing dimensions in one column family would break HNSW index construction regardless of schema
convenience. A per-deployment constant is the honest shape of the constraint, not a shortcut.

**Follow-up recorded for SM-C3** (does not block v1):
1. Make the dimension a **migration parameter**, not a hardcoded literal, so Mode B provisioning is
   not a manual DDL edit — the current comment-driven "swap it by hand" step is a live footgun.
2. Enforce `embedding_dim` against the **column width** on write (SM-R12 §9 already assigns this to
   SM-C3; the model-level validator only checks vector length against `embedding_dim`).
3. Carry SM-R4's `embedding_version` (`model/dim/metric/rev`) through the dual-write + backfill +
   cutover path, so a dimension change is a versioned migration rather than a schema swap.
4. `halfvec` at >10M (A.1) is a *third* column shape — fold it into the same parameterization.

### B.5 Terminology — DRIFT FOUND AND FIXED HERE

Grepped all 11 source documents. Three real drifts, corrected in this ADR's own text rather than
adding a fourth variant:

1. **"CoALA" appears in ZERO of the 11 documents.** It is used only in the research doc and issue
   #76. The siblings say "tiers," "memory tier," "episodic tier." **Ruling:** "CoALA tiers" is the
   canonical collective name (it is the cited source, arXiv:2309.02427), and the four tier names —
   **working / episodic / semantic / procedural** — are canonical individually. Siblings need not be
   retrofitted; new work uses these names.
2. **The tier axis and the substrate axis are conflated.** Documents write "vector tier" and "graph
   tier" (SM-R4, SM-R10, SM-R9) interchangeably with "episodic tier" and "semantic tier" (SM-R7,
   SM-R10). These are **orthogonal**: a tier is *what kind of memory*; a substrate is *where it is
   stored*. §1 is a matrix precisely because episodic and semantic each live in **both**
   substrates. **Ruling:** say **tier** only for working/episodic/semantic/procedural, and
   **substrate** only for vector/graph. SM-R10's "vector/episodic tier" should be read as "the
   episodic tier's vector substrate."
3. **`semantic` is a homonym across siblings.** SM-R6 uses "semantic" 15 times and **never** means
   the semantic memory tier — it means embedding-similarity ("semantic re-segmenter," "semantic
   drift," "semantic methods"). **Ruling:** unqualified "semantic" means the **tier**. For SM-R6's
   sense write **"embedding-similarity"** or **"semantic-similarity segmentation."** SM-E3 should
   not read SM-R6's "semantic v2" as touching the semantic tier — it does not.

**Seam naming — consistent, no fix needed.** `MemoryProvider` is named identically in SM-R9,
SM-R11 and SM-R13. SM-R12's `MemoryStore` is **not** a competing name: SM-R11 §7 reconciles the
altitudes explicitly — R12 `read/write` = one row by primary key; R11 `read/write` = query →
`MemorySlice` / event write-back. Both stand. `VectorSubstrate` / `GraphSubstrate` appear only in
SM-R11 and are hereby canonical.

### B.6 SM-R10 vs SM-R12 on the episodic tier's temporal fields — CONFLICT SURFACED AND RULED

**This conflict was not surfaced by either sibling.** Both are merged; both are tested; they
disagree, and neither acknowledges the other's position.

| | SM-R10 §9 / AC#5 | SM-R12 `ddl/schema.sql` + `model.py` |
|---|---|---|
| Episodic temporal fields | `valid_from`, `valid_to` — **filter-only**, *"no `t_invalid`/`expired_at`, no invalidation authority"* | `t_valid`, `t_invalid`, `expired_at`, `superseded_by TEXT[]` + both CHECK constraints |

**First: most of it is a naming difference, already unified.** SM-R12's `TEMPORAL_FIELD_MAP` maps
`valid_from → t_valid` and `valid_to → t_invalid` explicitly. Two of the four fields are the *same*
decision under two names, and SM-R11's `MemoryRecord` and SM-R13's `Turn` already follow SM-R12's
canonical names. **The genuine divergence is one field: `expired_at` (and `superseded_by`) on the
episodic row.**

**Ruling: adopt SM-R12's shape; keep SM-R10's constraint, re-scoped.** Four reasons, all from
source:

1. **SM-R10 contradicts itself.** §9 denies episodic an `expired_at`; §12.1(a) — written later,
   specifically to reconcile with SM-R5 — says *"'Archive/tier rather than hard-delete' maps onto
   this model as `expired_at`"* and states that SM-R5's *"prune gate acts on episodic rows."*
   Under §9 those rows have no `expired_at` to set. §12.1(a) needs the field §9 denies.
2. **SM-R10's own two-axis principle argues against its §9.** §5 is emphatic that `t_invalid`
   ("became false in the world") and `expired_at` ("system stopped holding this version") are
   independent and both load-bearing. Without `expired_at`, SM-R7 §7 is forced into *"DELETE sets
   `valid_to` (retire) but keeps the row"* — writing a **belief-time** event onto the
   **world-time** axis. Retiring a row is not a claim the fact became false. Denying `expired_at`
   corrupts the very axis separation SM-R10 exists to protect.
3. **§9's split-brain rationale covers only half of what §9 covers.** It is sound for the *derived
   semantic projection*, whose `valid_to` mirrors graph edge invalidation. But §9 says
   "vector/**episodic** tier." Episodes are not derived — SM-R7 §8.5 makes them the source of
   truth (*"episodes are the source of truth; semantic memory is a derived, rebuildable view"*).
   No graph edge owns an episode's validity, so there is no second writer to be split-brained with.
4. **SM-R6 imposes a requirement SM-R10 never considered.** Async re-segmentation supersedes
   episodes on re-cut (`revision`, `supersedes[]`, `superseded_by[]`, retained-not-deleted). That
   is episodic-local supersede machinery, unrelated to fact contradiction. SM-R12 carries it and
   round-trip tests it on real pgvector through the psycopg driver.

**What is ratified:**

- **Schema:** SM-R12's shape stands. Canonical `t_valid` / `t_invalid` / `created_at` /
  `expired_at` / `superseded_by` everywhere, episodic included. SM-R12's merged DDL needs **no
  correction**. SM-R9's §Composition bug — shipping a predicate that dropped
  `coalesce(t_invalid, ∞)` and `expired_at IS NULL` — is direct evidence that two vocabularies for
  one concept produce correctness bugs, so the unification is a safety property, not tidiness.
- **Authority (SM-R10's rule, preserved and re-scoped):** SM-R10 §9's *decision* narrows from "the
  vector/episodic tier" to **"the derived semantic projection."** The graph remains **sole
  authority for semantic contradiction/invalidation**; a vector copy of a derived semantic fact is
  a projection — never decided locally, never propagated back (§9.1's "never the reverse
  direction" survives intact).
- **Episodic `expired_at` has exactly two sanctioned writers**, neither of which is contradiction
  resolution: **SM-R6** async re-segmentation, and **SM-R5** decay archival. Both are episodic
  lifecycle events, not world-truth claims — so neither reopens split-brain.
- **Schema capability ≠ write authority.** SM-R10 §9 conflated the two; authority is a write-path
  property. **Action for SM-C3:** enforce it structurally, with the same append-only discipline
  SM-R10 §6.1 applies to `memory_edge`. SM-R12's DDL ships the CHECK constraints but **no
  equivalent trigger** on the memory tables, so today the two-writer rule is convention only.

**Row vs edge — the boundary SM-C1 must not blur.** Merged SM-R11 ratifies a **ONE WRITE MODEL**
rule (§3): *"route ALL bi-temporal writes — including consolidation-produced contradictions — through
SM-R10's SCD-2 primitive."* Read literally, "ALL" would send episode archival through
`invalidate_edge`. It must not. SM-R10's primitives (`upsert_edge` / `invalidate_edge` /
`retract_edge`) are **edge** operations on the graph; the two sanctioned writers above act on
**episodic rows**, which are not edges and have no graph edge owning their validity. The rule reads:

| Write | Target | Path |
|---|---|---|
| Semantic contradiction / invalidation | graph **edge** | SM-R10 SCD-2 primitive — **one model, no exceptions** |
| SM-R6 async re-segmentation supersede | episodic **row** | row-level `expire()` + `superseded_by[]` (SM-R12) |
| SM-R5 decay archival | episodic **row** | row-level `expired_at` (SM-R12) |

Both column families are append-only and invalidate-not-delete; they are simply different objects.
**Independent corroboration:** SM-R11's post-merge `findings/F2` — *"the vector substrate is not the
source of truth for validity"* — and F10's one-write-model rule were derived without reference to
this ADR and land on the same place as the ruling above.

Net effect: nothing merged is reverted, SM-R10 §12.1(a)'s self-tension resolves, and SM-C3
inherits one rule instead of two.

### B.7 SM-R10 §9.1's undefined edge→memory join key — RECORDED (owner exists)

SM-R10 §9.1's `valid_to` re-sync statement assumes a join `m.memory_id = e.edge_id` and says
plainly that it *"presumes an edge→memory provenance link written at consolidation time. No such
column is specified in §5 or §9."* It cross-references SM-R12's `source_episode_ids[]` /
`derived_by` on `SemanticRelation` as a candidate (`... WHERE m.memory_id = ANY(e.source_episode_ids)`),
notes the cardinality problem — one edge can produce several memories and one memory can cite
several edges, so this may need a **link table** rather than a column — and asks that **SM-X11
settle it with SM-R12 rather than inventing a third link**, *"flagged so it is not rediscovered
independently."*

**Recorded, not resolved here.** It has a named owner (SM-X11) and is a schema-detail decision, not
a cross-decision conflict, so it does not block this ADR. But a consistency pass that omitted it
would not be complete. Note SM-R12 already ships `MemoryLink` (`from_id`/`from_tier`,
`to_id`/`to_tier`, `link_type`, bi-temporal) — **that is very likely the link table SM-R10 is
reaching for**, and SM-X11 should evaluate it before designing a fourth mechanism. The re-sync
statement itself remains explicitly illustrative and unexecuted.

---

## Consequences

- SM-R3 … SM-R13 can move from "Proposed (provisional)" toward accepted on the targets ratified in
  Part A. **Five items are not fully ratified** and stay explicitly open or conditional:
  3-leg fusion (A.6, held open), the query-embedding latency budget on CPU (A.4, split and
  re-scoped), PPR ranking quality over a bi-temporal projection (B.2, owned/unmeasured), the
  **memory → vector fan-out ratio** (A.1, held open — blocks capacity sizing), and
  **`Recall@10 ≥ 0.95`** (A.3b, ratified *conditional* on an unmeasured `ef_search` sweep).
- **Nobody should size infrastructure off this ADR until A.1's fan-out is measured.** The 10M
  envelope and the ≤80 GB storage budget are both stated **in vectors**; the per-tenant ceilings
  are stated **in memories**, and the conversion between them is unknown and entity-dominated.
- SM-R2 is unblocked, and inherits a **live risk** (§4) rather than a foregone Neo4j conclusion.
- SM-C1 implements the SM-R11 seam and must re-prove config-only swap against a **real** external
  backend (§5), and must **reject `HuggingfaceCustomEmbedder` for `mode=CO_LOCATED`** (A.3c).
- SM-C3 promotes SM-R12's shapes and picks up three follow-ups: the B.4 dimension parameterization,
  the **B.6 append-only trigger** on the memory tables (today the two-writer rule on episodic
  `expired_at` is convention, not enforcement), and `ef_search` calibration per A.3b.
- **SM-R10 §9 is narrowed by B.6** — its filter-only rule now scopes to the derived semantic
  projection, not the episodic tier. SM-R12's DDL is unchanged and correct. Nothing is reverted.
- SM-X11 owns SM-R10 §9.1's undefined edge→memory join key (B.7) and should evaluate SM-R12's
  existing `MemoryLink` before inventing a third mechanism.
- Right-to-erasure (B.3) is a named, unowned gap needing its own ticket.
- Cost reality carried forward: SM-R9 sizes graph extraction at **~$220/tenant at 100k memories,
  ~$2,200/tenant at 1M**, recurring-marginal rather than one-time, and **~2.4× worse per useful
  triple on dialogue** than on Wikipedia. Per-tenant gating is what keeps this bounded.

## Out of scope

Graph engine choice (SM-R2). Vector-store engine thresholds (SM-R3). Decay curve (SM-R5).
Implementation of anything (SM Groups C–X). No `libs/agno/egpt/**` change — this ADR is docs-only.

## Acceptance criteria (issue #76)

- [x] ADR approved with the CoALA tier → substrate mapping — §1
- [x] Extend-vs-new decided for `learn.LearningMachine` and `memory.MemoryManager` (with a path) — §2
- [x] Read / write / async-consolidate data flow specified — §3
- [x] Temporal-graph placement vs `lightrag` decided (feeds SM-R2; engine choice explicitly NOT made here) — §4
- [x] Dual-mode (Mode A/B) placement behind the `MemoryProvider` seam documented — §5
- [x] Gaps in `UserMemory` recorded for SM-R12/SM-C3 (already filled — cited) — §6

## References

| Task | Source read |
|---|---|
| SM-R3 | `benchmarks/memory_vectordb/DECISION.md` |
| SM-R4 | `benchmarks/memory_embedding/DECISION.md` |
| SM-R5 | `benchmarks/memory_decay/DECISION.md` |
| SM-R6 | `benchmarks/memory_event_segmentation/DECISION.md` |
| SM-R7 | `benchmarks/memory_consolidation/RESEARCH.md` |
| SM-R8 | `benchmarks/memory_retrieval/DECISION.md` |
| SM-R9 | `benchmarks/memory_multihop/SPIKE.md` |
| SM-R10 | `benchmarks/memory_bitemporal/SPEC.md` |
| SM-R11 | `benchmarks/memory_provider_spec/SPEC.md` + `interface.py` + `swap_test.py` + `results/swap_results.json` + `findings/README.md` (**merged**; all claims re-verified post-merge — see §3) |
| SM-R12 | `benchmarks/memory_datamodel/SPEC.md` + `model.py` + `ddl/schema.sql` |
| SM-R13 | `benchmarks/memory_eval/PLAN.md` |

Grounding: `MEMORY-RESEARCH-episodic-and-neurosymbolic.md` (D7/D8). **Note:** that file is cited by
every sibling as `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md`, but it is **not present in
`docs/`** in this checkout — SM-R12 §11 and SM-R13 §8 both record the gap. It should be added to the
now-tracked `docs/` directory so the citations resolve.
