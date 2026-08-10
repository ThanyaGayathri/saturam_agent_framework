# TEST_LOG — memory_graph_engine (SM-R2)

Environment: macOS 25.5.0, Python 3.14.6, `.venvs/graphengine`.
Engines under test: `graphiti-core` 0.29.3, `cognee` 1.4.2, `kuzu` 0.18.2,
Neo4j 5.26.28 Community (+ GDS 2.13.11), FalkorDB (module ver 42001).

Ground truth is **imported** from [`../memory_bitemporal/scenarios.py`](../memory_bitemporal/scenarios.py)
(11 scenarios / 30 probes), so every engine is scored against SM-R10's own expected answers.

---

### run_graphiti.py

**Status:** PASS (ran clean) — **result: graphiti-core scores 19/30 = 63.3%**

**Description:** Drives the real `graphiti-core` temporal engine — its `EntityEdge` model, its
`resolve_edge_contradictions` rule, its `EntityEdge.save()` — against real Neo4j 5, scored on
SM-R10's 30 probes. The LLM is bypassed and the read path is the most favourable correct 5a/5b
form, both of which favour Graphiti.

**Result:**

```
  simple_insert 3/3 | supersede_relocation 2/3 | idempotent_noop 1/1 | three_way_chain 1/3
  backfilled_since_jan 1/2 | retract_error 2/2 | predicate_isolation 2/2 | tenant_isolation 2/2
  belief_evolution 2/3 | same_t_valid_tiebreak 1/4 | deep_recorrection 2/5
```

11 failures, three root causes, each traced to library source: (a) in-place mutation with no
bounded reinsert (`edge_operations.py:569-570` + `SET e = $edge_data`), (b) no same-`t_valid` tie
rule (strict `<` at `:567`), (c) no back-dated bounding. For reference, SM-R10's own single-time
baseline — a store with *no* bi-temporal model — scores 56.7%.

---

### forensics.py

**Status:** PASS — root cause confirmed at row level

**Description:** Dumps what Graphiti actually leaves in the graph after NYC→LA→SF.

**Result:** **3 rows stored where SCD-2 stores 5.** Every superseded row carries
`expired_at != NULL`, so it is no longer current belief, and no bounded replacement version
carries the `[Jan, Jun)` window — "where did the user live in Feb 2024?" returns `[]` against a
ground truth of `NYC`. Also observed: `expired_at` is stamped with **wall-clock (2026-08-10)**
rather than an injectable transaction time, which SM-R10 §6 requires for backfill/replay/5b.

---

### run_cognee.py

**Status:** PASS (ran clean) — **result: 8 of 30 probes structurally inexpressible**

**Description:** Structural probe of cognee 1.4.2's temporal model, bi-temporal fields, read path,
and PPR support. Every assertion is re-checked against the installed package at runtime, so it
fails loudly if a future version closes the gap.

**Result:** `Event{name, description, location, time_from, time_to}` — valid-time axis only,
**no transaction axis**, so the 8 5b probes cannot be represented. `valid_at`/`invalid_at` exist
only under `modules/migration/` (import path from Graphiti/Zep); `expired_at` absent even there.
Temporal read path calls `LLMGateway.acreate_structured_output` **per query**. Zero files mention
pagerank.

**Not run:** the full `cognify` pipeline (needs LLM credentials). Recorded as a gap; it cannot
change the data-model finding.

---

### run_ppr_route.py

**Status:** PASS — **Route A confirmed on Neo4j Community**

**Description:** Tests whether SM-R9's assumed `gds.pageRank(sourceNodes=…)` is actually available
on a self-hostable deployment, whether it is genuinely personalized, and whether SM-R10's 5a
predicate composes at projection time.

**Result:** edition `community`, GDS `2.13.11`, 423 `gds.*` procedures.
PPR from A = `{A:0.5, E:0.1406, B:0.125, C:0.0625, D:0.0313}`; from E =
`{E:0.5, rest 0.0}` → **personalization is real** (not a silent fallback to unseeded PageRank).
5a-filtered projection drops 5 relationships to 4 and changes the ranking, so **filter and
ranking compose mechanically on temporal data** — which SM-R9 had established by design only.

---

### Capability probes (inline, not scripted)

**Status:** PASS

| Probe | Result |
|---|---|
| Stock Neo4j 5.26 Community, no plugin | **0** `gds.*` procedures — GDS is a separate plugin, not built in |
| Kuzu 0.18.2 + ALGO extension | `PAGE_RANK` exists (global); `sourceNodes` / `source_nodes` / `personalization` all rejected as *"Unknown optional parameter"* → **no personalized PPR** |
| FalkorDB `dbms.procedures()` | `algo.pageRank` present (global); `algo.personalizedPageRank` *"is not registered"* → **no personalized PPR** |
| `lightrag` in-tree client | 0 temporal fields, 0 pagerank; `async_search` logs *"Filters are not supported in LightRAG"* and drops them |
| GDS licensing state | `gds.debug.sysInfo()` → `gdsEdition: Unlicensed`, yet personalized PageRank runs. Shipped jar contains an evaluation-only `LICENSE.txt` beside a stale 2018 GPL `NOTICE.txt` — **unresolved, flagged for legal** |

---

### GDS projection staleness (inline)

**Status:** PASS — qualification found

**Description:** Checks whether the PPR layer is incremental, since the issue asks for
"incremental updates (no batch recompute)".

**Result:** a write to the live database is **invisible** to an existing projection
(`C: 0.0` before and after adding `B→C`; projection still reports 1 relationship while the DB has
2), and only appears after an explicit re-projection (`C: 0.1084`). So the **bi-temporal store is
incremental** but the **GDS projection is a static snapshot** needing a re-projection cadence.
This applies to Route B equally, so it is not a route differentiator — but it corrects any
assumption that GDS gives incremental PPR. Flagged for SM-H4.

---

### Lint

`ruff format` + `ruff check` on `benchmarks/memory_graph_engine/` — **All checks passed.**
Repo `./scripts/format.sh` / `./scripts/validate.sh` were **not** run: they require `.venv`, which
does not exist in this checkout. The folder contains no core code and lints clean under `ruff`.
