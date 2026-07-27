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
strings. Normalisation parses ISO -> epoch on migration (`ddl/MIGRATION.md` global action 4).

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
