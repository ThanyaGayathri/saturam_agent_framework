# Findings - notes for the implementation task (SM-C3) and cross-task couplings

Non-blocking observations surfaced while building/verifying this spec. None change the model;
they are handoffs so the implementer and sibling tasks don't rediscover them.

## F1 - Seed `UserMemory` has more columns than issue #87 states

The issue lists `memory, memory_id, topics[], user_id, agent_id, created_at, updated_at`. The
**current code** (`libs/agno/egpt/db/schemas/memory.py:9`) also has `input`, `team_id`,
`feedback`. The migration (`ddl/MIGRATION.md`) maps all three (into `metadata.*` / `team_id`) so
nothing is dropped. **Action for SM-C3:** migrate from the real 10 fields, not the issue's 7.

## F2 - Temporal-name conflict between SM-R10 and the SM-R7 prototype

SM-R10 (#85) mandates `t_valid/t_invalid/created_at/expired_at` (Graphiti). The SM-R7
consolidation prototype (`benchmarks/memory_consolidation/harness/schemas.py`) already shipped
`valid_from/valid_to/superseded_by`. This spec standardises on the **SM-R10** names (issue AC #4)
and carries `TEMPORAL_FIELD_MAP` in `model.py` for the rename. **Action:** when SM-C3 and SM-R7's
implementation (SM-L7) land, apply the map so both write the same columns.

## F3 - Two time formats in the seed

Seed **row columns** are epoch-second ints; seed **in-content** entries
(`learn/schemas.py` `EntityMemory.facts/events/relationships`, `Memories.memories`) are ISO-8601
strings. Normalisation parses ISO -> epoch on migration (`ddl/MIGRATION.md` global action 5).

## F4 - `EntityMemory` is the strongest reuse anchor, but must be normalised

The seed `EntityMemory` already models node + `facts` (semantic) + `events` (episodic) +
`relationships` (graph edges) + `properties` inline. This spec **normalises** those inline lists
into first-class rows (`memory_entity` / `memory_semantic_fact` / `memory_episodic` /
`memory_relation` + `memory_link`) so they are independently queryable and bi-temporally
correctable. **Action for SM-C3:** provide the fan-out migration; keep `EntityMemory` readable
until cutover.

## F5 - Docker unavailable -> embedded real Postgres used (not a mock)

This environment has no Docker. The round-trip runs a genuine PostgreSQL 16.2 + pgvector 0.6.2
via `pgserver` (bundled binaries; unix socket in a unique per-run dir). This is the same engine
SM-R3 selected, so DDL fidelity (partitioning, HNSW, `vector`, `TEXT[]`, `JSONB`, bi-temporal) is
proven on the real extension. **Not** covered here: scale/latency at 10M (owned by SM-R3).

## F7 - Lint/type tooling pins: ruff must be 0.15.20; `mypy==2.1.0` is not on public PyPI

`libs/agno/pyproject.toml` pins `ruff==0.15.20` and `mypy==2.1.0`. The ruff pin matters: under a
newer ruff (0.16.0) the default rule set flags UP/FA and even the seed schemas report ~16 errors,
so validation MUST use 0.15.20 (this folder was linted with it -> clean). **`mypy==2.1.0` does not
exist on public PyPI** (latest is 1.19.x) -> could not install the exact pin in this environment;
ran `mypy 1.19.1` with the repo config + pydantic plugin instead (clean). **Action:** whoever runs
CI should confirm the mypy pin resolves on the org's index, or correct the pin.

## F6 - `embedding_dim` is carried on the row but the column width is fixed per deployment

The DDL fixes `vector(768)` (SM-R4 Mode A). `embedding_dim`/`embedding_version` on each row let a
store detect a version/dim mismatch during a re-embed/migration (SM-R4 D8 dual-write). **Action
for SM-C3:** on a dim change (Mode B `vector(1024)` or a new model), add a new column/index at the
new dim and dual-write, per SM-R4 section 7 - do not silently widen in place.

## F8 - `strength` dropped: followed SM-R5's ratified decision over the issue's question text

The issue #87 question text suggests an episodic `strength` field "(from SM-R5)". That predates
SM-R5's actual decision. SM-R5's ratified `memory_decay/DECISION.md` §5 ("Deferred - do NOT add
now") **rejects** `strength` (negligible measured benefit; "`last_accessed_at` + `importance`
capture the behaviour with one fewer stateful field"). An earlier draft of this spec added
`strength` anyway and mis-stated SM-R5 as "reserving" it. **Corrected:** `strength` removed from
`model.py`/`ddl`/`store.py`; `Decaying` now carries only `importance` + `last_accessed_at`; the
SM-R5 misquote is fixed (SPEC §10). Revisit only if SM-E4 shows a need (SM-R5's own escape hatch).

## F9 - SM-R6 episode-identity/segmentation contract added to `EpisodicRecord`

The first PR omitted SM-R6 entirely. SM-R6's `memory_event_segmentation/DECISION.md` §4/§5
mandates a stable-id + supersede/revision identity contract for async re-segmentation. Added to
`EpisodicRecord` + `memory_episodic`: `revision`, `supersedes`/`superseded_by` (uuid **arrays** -
a re-cut can merge/split, so these override the single-id `superseded_by` from `BiTemporal`),
`boundary_method`/`boundary_trigger`/`boundary_confidence`, `refined`, `segmenter_version`.
**Action for SM-C3/SM-R6 impl:** the *segmentation algorithm* (which rule cuts a boundary, the
async re-segmenter) is owned by SM-R6/SM-C*; this spec only carries the identity/metadata columns.

## F10 - SM-R10 guardrails: insert-only `write()` + `expire()` + CHECK constraints

The first PR's only write path was a blanket `INSERT ... ON CONFLICT DO UPDATE SET <all cols>`,
which could silently mutate `t_valid`/`t_invalid` in place (violating SCD-2), and its bi-temporal
test never exercised the UPDATE path. **Corrected:** `write()` is now insert-only (a new version =
a new id; duplicate id raises); the only in-place mutation is `expire()` (sets `expired_at`/
`t_invalid`/`superseded_by`, never `t_valid`/`created_at`, guarded by `expired_at IS NULL`). Added
`t_invalid >= t_valid` and `expired_at >= created_at` CHECK constraints on all four bi-temporal
tables. `roundtrip_test.py` now proves all three (expire-on-persisted-row preserving `t_valid`,
the insert-only rejection, and the constraints firing).

## F11 - SM-R7 `derived_by` provenance added (was a noted gap)

`SemanticFact`/`SemanticRelation` now carry `derived_by` (the strategy/pass that produced the
fact) alongside `source_episode_ids`, matching SM-R7 RESEARCH §7's provisional provenance schema.
SM-R7 flags this as provisional/SM-L7-owned, so the *population* logic stays with SM-L7; the
column is reserved here so SM-C3 doesn't need a follow-up migration.
