# Migration from the seed schemas (for SM-C3)

How today's rows land in the SM-R12 model. Verified against current post-rebrand code
(`libs/agno/egpt/**`, namespace `egpt`). Nothing here is executed in this task — SM-C3 writes
the actual migrations; this is the mapping contract.

## Global actions (apply to every migrated row)

1. **Inject `tenant_id`** — the single biggest change. No seed schema has a tenant. During
   migration set `tenant_id` from the deployment/org identity (single-tenant installs use one
   constant, e.g. `'default'`). It is the required SM-R3 partition key; create the tenant's
   partitions (see `schema.sql`) before loading.
2. **Backfill embeddings lazily** — `embedding`/`embedding_dim`/`embedding_version` are NULL at
   migration. Re-embed each `content` with the SM-R4 model and stamp the version tag; raw
   `content` is retained so this is always rebuildable (SM-R4 D8).
3. **Bi-temporal defaults** — `created_at` carries the seed's transaction time. `t_valid`/
   `t_invalid`/`expired_at` are NULL (historical world-validity is unknown; rows are treated as
   valid-since-forever / current). Going forward, corrections use invalidate-not-delete.
4. **Net-new fields default cleanly.** The SM-R6 episode-identity columns (`revision=0`,
   `supersedes`/`superseded_by='{}'`, `boundary_*=NULL`, `refined=FALSE`, `segmenter_version=NULL`)
   and SM-R7 `derived_by=NULL` have no seed source; migrated rows take the defaults (a migrated
   episode is treated as an inline, un-refined `revision=0` cut). SM-R5 `strength` is **not** a
   column (deferred by SM-R5); decay uses `last_accessed_at` (default `created_at`) + `importance`
   (default 0.5).
5. **Time-format normalisation (two formats in the seed):**
   - Seed **row columns** (`UserMemory.created_at/updated_at`, `agno_learnings`) are epoch
     seconds — copied as-is to the BIGINT columns.
   - Seed **in-content** entries (`learn/schemas.py` `EntityMemory.facts/events/relationships`,
     `Memories.memories`) carry **ISO-8601 strings** — parse to epoch seconds on normalisation.

## `db/schemas/memory.py:9` `UserMemory` -> `memory_semantic_fact` (default)  [disposition: extend]

Seed `UserMemory` rows are distilled user memories -> default to the semantic/fact tier (a
later SM-R7 consolidation pass can re-tier any that are really episodic).

| Seed field | Target | Note |
|---|---|---|
| `memory_id` | `id` | keep original; if not uuid, also stash in `metadata.legacy_memory_id` |
| `memory` | `content` | the statement |
| `topics[]` | `metadata.topics` | model has no `topics` column; preserved in metadata (lossless) |
| `input` | `metadata.input` | triggering input (**real code has it; issue's field list omits it**) |
| `feedback` | `metadata.feedback` | **real-code field; issue omits it** |
| `user_id`/`agent_id`/`team_id` | same | `team_id` is **real-code; issue omits it** |
| `created_at`/`updated_at` | same | epoch s, as-is |
| — | `tenant_id` | injected (global action 1) |
| — | `embedding*` | backfilled (global action 2) |

## `learn/schemas.py` — per type

| Seed type | Target | Mapping / disposition |
|---|---|---|
| `SessionContext` | `WorkingMemory` (session store) | `summary/goal/plan/progress` map directly. **Transient** — stays in the session store, not a durable memory table. [extend] |
| `LearnedKnowledge` | `memory_procedural` | `title`->`title`, `learning`->`content`, `context`->`trigger`, `tags`->`tags`, `namespace`->`metadata.namespace`; ISO timestamps -> epoch. `kind='rule'` default. [extend] |
| `EntityMemory` | **normalised across 4 tables** | `entity_id`/`entity_type`/`name`/`description`/`properties` -> `memory_entity`; each `facts[]` item -> `memory_semantic_fact` (`content`=fact content, `source`/`confidence` -> columns/metadata, `entity_ids=[entity]`); each `events[]` item -> `memory_episodic` (`content`=event, `occurred_at`=parsed `date`); each `relationships[]` item -> `memory_relation` (`subject_entity_id`=entity, `predicate`=relation, `object_entity_id`=related) **and** a `memory_link` (`about`). In-content ISO timestamps -> epoch. [normalise] |
| `UserProfile` | — | **Out of scope** — identity/profile, not a memory tier. Stays in the learn store. [out-of-scope] |
| `DecisionLog` | — | **Reuse as-is** (decision audit). Optionally referenced from a procedural record's `metadata`. [reuse] |
| `Memories`, `Feedback`, `InstructionUpdate` | — | **Out of scope** (learn extras; not cited by #87). Noted so nothing is silently dropped. [out-of-scope] |

## Rollback / safety

- Migration is additive: it reads seed tables and writes the new `memory_*` tables; seed tables
  are left intact until SM-C3 cuts retrieval over. No destructive step.
- Because embeddings and bi-temporal fields are derived/defaulted, a re-run is idempotent per
  `id` (upsert on `(tenant_id, id)`).
