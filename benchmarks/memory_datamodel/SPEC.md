<!--
SM-R12 memory data-model spec. The record model is executable (model.py) and round-trip
tested (roundtrip_test.py); DDL under ddl/. Core (libs/agno/egpt/**) is untouched -- this
folder is the whole deliverable and SM-C3 promotes the shapes into core schemas + migrations.
-->
# SM-R12 - Memory data model / schemas

- **Status:** Proposed (model + DDL + migration + round-trip complete and verified on real pgvector; pending SM-R1 ADR ratification of provisional targets)
- **Issue:** [Memory] SM-R12 - Saturam-Inc/aicoe-tracker#87
- **Depends on:** SM-R1 (#76 ADR - *not ratified*; targets below are provisional), SM-R10 (#85 bi-temporal)
- **Consistent with:** SM-R5 (#80 decay), SM-R3 (#78 store/partitioning), SM-R4 (#79 embedding), SM-R7 (consolidation prototype)
- **Feeds:** SM-C3 (implements these shapes as core schemas + migrations)
- **Artifact:** [`model.py`](model.py) (pydantic), [`ddl/`](ddl/) *(Phase 2)*, [`roundtrip_test.py`](roundtrip_test.py) *(Phase 3)*

---

## 1. Acceptance criteria (issue #87, verbatim) — where each is satisfied

> Each box is backed by a runnable artifact, not prose.

- [x] **Schemas defined for episodic, semantic (entity/relation/fact), procedural, and links** — [`model.py`](model.py) (7 tier models, §4) + [`ddl/schema.sql`](ddl/schema.sql) (6 tables) + [`roundtrip_test.py`](roundtrip_test.py) (all round-trip on real pgvector).
- [x] **`tenant_id`, embedding, and temporal (created/valid) fields present where needed** — `TenantScoped`+`Embeddable`+`BiTemporal` spine (§4.2); per-tier presence table (§4.4, cross-checked against `model_fields`); persisted + read back losslessly.
- [x] **Extend-vs-new decided for `UserMemory` and the learn schemas** — §5: every cited seed surface has a disposition (extend / normalise / reuse / out-of-scope), no orphan.
- [x] **Consistent with SM-R5 (decay fields) and SM-R10 (bi-temporal)** — `Decaying` (SM-R5 `strength`/`last_accessed_at`/`importance`); `BiTemporal` (SM-R10 four-timestamp) + `TEMPORAL_FIELD_MAP`; round-trip proves invalidate-not-delete + point-in-time "true at T".

## 2. Questions to answer (issue #87) — model-level answers

| Question | Answer |
|---|---|
| Episodic record fields (raw content, ts, context when/where/why, importance, strength, last_accessed_at, links to graph node)? | `EpisodicRecord`: `content` (raw), `created_at`/`context.occurred_at` (ts), `context{occurred_at,location,cause}` (when/where/why), `importance`+`strength`+`last_accessed_at` (SM-R5), `entity_ids[]` + `graph_node_id` (graph links). §4.3. |
| Semantic fact/entity/relation schema (SPO + provenance + bi-temporal from SM-R10)? | Three models: `SemanticFact` (SPO + `source_episode_ids` provenance + bi-temporal), `SemanticEntity` (graph node), `SemanticRelation` (graph edge — the bi-temporal SM-R10 surface). §4.3. |
| Procedural memory representation (rule/skill/prompt fragment)? | `ProceduralRecord` with `kind ∈ {rule, skill, prompt_fragment}`, `trigger`, `content` body; extends seed `LearnedKnowledge`. §4.3. |
| `tenant_id` + embedding + temporal on all relevant tiers? | Presence-by-tier table §4.4 — `tenant_id` on all persisted tiers (SM-R3 partition key); embedding on episodic/semantic-fact/entity/procedural; bi-temporal on fact/relation/link. |
| Extend `UserMemory`/learn vs add new? | §5 disposition: **extend** the seed's shared-base pattern (don't fork); episodic/graph/link tiers are **net-new**; `EntityMemory` internals **normalised** into fact/episode/relation rows. |

## 3. Scope, provisional targets & core-untouched

- **Provisional (SM-R1 to ratify):** 10M vectors, 100k–1M memories/tenant, ≤~50 tenants, English-only, embedding dim 768 (Mode A) / 1024 (Mode B).
- **Working model:** no change to `libs/agno/egpt/**`. Proof (Phase 4): `git diff origin/main -- libs/` empty. This folder is the deliverable; SM-C3 promotes it.
- **KB-vs-memory (§6):** memory and knowledge stay **separate stores** sharing the **same retrieval machinery** (VectorDb + embedder) behind **one interface**.

## 4. The record model

### 4.1 Shape
One shared spine, thin per-tier subclasses — the same specialisation pattern the seed already
uses (`UserProfile`/`SessionContext`/`EntityMemory`/... share base identity+audit fields). This
**extends** the seed's shape rather than forking a parallel hierarchy.

### 4.2 Shared mixins (fields, type, nullability, default)
| Mixin | Field | Type | Null? | Default | Source |
|---|---|---|---|---|---|
| `TenantScoped` | `id` | str | no | `uuid4().hex` | new |
| | `tenant_id` | str | **no (required)** | — | **SM-R3** partition key |
| | `user_id`/`agent_id`/`team_id`/`session_id` | str | yes | None | seed |
| | `created_at` | int (epoch s) | no | now | seed / SM-R10 tx-time-in |
| | `updated_at` | int | yes | None | seed audit |
| | `metadata` | dict | no | `{}` | new |
| `BiTemporal` | `t_valid` | int | yes | None | **SM-R10** valid-time start |
| | `t_invalid` | int | yes | None | **SM-R10** valid-time end |
| | `expired_at` | int | yes | None | **SM-R10** tx-time-out |
| | `superseded_by` | str | yes | None | SM-R7 provenance |
| `Embeddable` | `embedding` | list[float] | yes | None | **SM-R4** |
| | `embedding_dim` | int | yes | None | SM-R4↔SM-R3 coupling (768/1024) |
| | `embedding_version` | str | yes | None | SM-R4 `model/dim/metric/rev` |
| `Decaying` | `importance` | float | no | 0.5 | **SM-R5** |
| | `strength` | float | yes | None | SM-R5 reinforcement |
| | `last_accessed_at` | int | yes | None | SM-R5 |

`BiTemporal.is_valid_at(T)` = `t_valid <= T < t_invalid` (SM-R10 point-in-time). `is_current()` =
row not superseded. **Invalidate-not-delete**: corrections set `t_invalid`/`expired_at` + write a
new row; nothing is destroyed.

### 4.3 Per-tier models (see [`model.py`](model.py))
- **`WorkingMemory`** (working, transient) — `summary/goal/plan/progress`; extends seed `SessionContext`. Not embedded, not bi-temporal.
- **`EpisodicRecord`** (episodic) — `content`, `context{occurred_at,location,cause}`, decay, embedding, `entity_ids[]`, `graph_node_id`. Aligns SM-R7 `Episode`.
- **`SemanticFact`** (semantic/fact) — `content` (statement), `subject/predicate/object`, `confidence`, `source_episode_ids[]` provenance, bi-temporal, embedding. Aligns SM-R7 `SemanticFact`.
- **`SemanticEntity`** (semantic/entity, graph node) — `entity_type`, `name`, `description`, `properties`; embeddable, not bi-temporal (identity is stable).
- **`SemanticRelation`** (semantic/relation, graph edge) — `subject_entity_id`, `predicate`, `object_entity_id`, `confidence`, provenance; **bi-temporal (primary SM-R10 surface)**; not embedded.
- **`ProceduralRecord`** (procedural) — `kind{rule,skill,prompt_fragment}`, `title`, `content` body, `trigger`, `tags`, `success_count`; extends seed `LearnedKnowledge`; embeddable.
- **`MemoryLink`** (links) — `from_id/from_tier`, `to_id/to_tier`, `link_type`; bi-temporal. First-classes the seed `EntityMemory.relationships` inline dicts.

### 4.4 Field-presence by tier (issue Q4)
| Tier | tenant_id | embedding | bi-temporal | decay |
|---|:--:|:--:|:--:|:--:|
| working | ✓ | — | — | — |
| episodic | ✓ | ✓ | ✓ | ✓ |
| semantic.fact | ✓ | ✓ | ✓ | ✓ |
| semantic.entity | ✓ | ✓ | — | — |
| semantic.relation | ✓ | — | ✓ | — |
| procedural | ✓ | ✓ | — | — |
| link | ✓ | — | ✓ | — |

## 5. Seed-context verification & disposition (no orphans)

Verified against **current post-rebrand code** (namespace `agno`→`egpt`).

| Seed surface (issue #87) | Verified location | Reality vs issue | Disposition |
|---|---|---|---|
| `db/schemas/memory.py:9` `UserMemory` | `libs/agno/egpt/db/schemas/memory.py:9` | Issue lists 7 fields; **real code also has `input`, `team_id`, `feedback`** | **Extend** → `EpisodicRecord`/`SemanticFact`. Migration §7 maps every real column. |
| `learn/schemas.py` `UserProfile` | present | long-term user profile | **Out of scope** (identity/profile, not a memory tier); keep as-is. |
| `learn/schemas.py` `SessionContext` | present | session state | **Extend** → `WorkingMemory` (working tier). |
| `learn/schemas.py` `LearnedKnowledge` | present | reusable insight | **Extend** → `ProceduralRecord`. |
| `learn/schemas.py` `EntityMemory` | present | node + inline `facts`/`events`/`relationships`/`properties` | **Normalise** → `SemanticEntity` (core) + `SemanticFact` (facts) + `EpisodicRecord` (events) + `SemanticRelation` (relationships). Strongest reuse anchor. |
| `learn/schemas.py` `DecisionLog` | present | decision audit | **Reuse as-is** (audit log); optionally referenced by procedural provenance. Out of scope to change. |
| (found, not in issue) `learn/schemas.py` `Memories`, `Feedback`, `InstructionUpdate` | present | learn extras | **Out of scope** — noted so nothing is silently ignored. |

**Reuse-first summary:** extend the seed's shared-base dataclass pattern; do not fork. Net-new
tiers (episodic, graph node/edge, link) are additive. No cited seed surface left unaddressed.

## 6. KB-vs-memory separation

Knowledge base (`db/schemas/knowledge.py` `KnowledgeRow`) and memory (`MemoryRecord`) are
**sibling row models** — both pydantic, epoch-int columns, auto-uuid — retrieved by the **same
machinery** (the `VectorDb` interface + `Embedder` from SM-R3/R4) but held in **separate
stores/tables**. One retrieval interface, two backing stores. The learn `LearningStore` protocol
(`recall`/`arecall`, `process`/`aprocess`) is the sync+async seam the memory store extends;
concrete stores (pgvector, Qdrant) are selected by config, never hardcoded at call sites. *(Store
adapter + sync/async proof lands in Phase 3.)*

## 7. Physical schemas & migration
- [`ddl/schema.sql`](ddl/schema.sql): 6 tenant-partitioned tables (`PARTITION BY LIST (tenant_id)`,
  SM-R3), `vector(768)` (SM-R4 Mode A; swap `vector(1024)` for Mode B / `halfvec` at scale),
  four-timestamp bi-temporal (SM-R10), HNSW `vector_cosine_ops` + partial bi-temporal + traversal
  indexes. Working memory has no table (session store). **Applied cleanly on real pgvector 0.6.2.**
- [`ddl/MIGRATION.md`](ddl/MIGRATION.md): every seed surface mapped; `tenant_id` injection;
  embedding lazy-backfill; the two seed time formats (epoch columns vs in-content ISO) normalised;
  additive/idempotent, non-destructive.

## 8. Round-trip test
[`store.py`](store.py) is the plug-and-play seam (abstract `MemoryStore` + `PgMemoryStore` real +
`InMemoryMemoryStore` second adapter), **sync and async**. [`roundtrip_test.py`](roundtrip_test.py)
runs `write -> read -> assert` for all 6 persisted tiers on **real embedded PostgreSQL+pgvector**
(via `pgserver`; unique per-run instance — no Docker), plus bi-temporal point-in-time,
cross-tenant isolation (0 leakage), and the seam swap. Verified on two independent runs — see
[`TEST_LOG.md`](TEST_LOG.md).

## 9. Out of scope & boundaries
- Decay **function**/parameters → SM-R5. Bi-temporal **query engine**/extraction → SM-R10. Store
  **engine choice**/partition mechanics → SM-R3. Embedder choice → SM-R4. Consolidation → SM-R7.
- No production wiring; SM-C3 owns implementation + migrations.

## 10. Consistency with dependencies & siblings
- **SM-R1 (#76):** not ratified — §3 targets flagged provisional.
- **SM-R10 (#85):** canonical temporal names adopted (`t_valid`/`t_invalid`/`created_at`/`expired_at`); relations are the bi-temporal surface; `is_valid_at` matches "true at T".
- **SM-R5 (#80):** reserves the exact fields it named (`strength`, `last_accessed_at`) + `importance`.
- **SM-R7:** `SemanticFact`/`Episode` shapes mirrored; `valid_from/valid_to/superseded_by` mapped via `TEMPORAL_FIELD_MAP`.
- **SM-R3/R4:** `tenant_id` partition key; `embedding_dim`/`embedding_version` coupling.

## 11. Grounding
- Grounded in `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` (per issue #87) — MemoryRecord data model. *(doc lives behind the private `docs/` symlink, not in this checkout.)*
- Reproduce: [`roundtrip_test.py`](roundtrip_test.py) (Phase 3).
