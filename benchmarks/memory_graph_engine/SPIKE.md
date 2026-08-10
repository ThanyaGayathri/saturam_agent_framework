# SM-R2 — Spike: temporal-graph engine prototype

- **Status:** Proposed — **GO: raw Neo4j (Community) + SM-R10's custom bi-temporal layer, Route A.**
- **Issue:** [Saturam-Inc/aicoe-tracker#77](https://github.com/Saturam-Inc/aicoe-tracker/issues/77)
- **Depends on:** SM-R1 (#76, ADR — [PR #54](https://github.com/Saturam-Inc/saturam-agent-framework/pull/54)
  is **OPEN / not merged**, therefore **not ratified**). Every place this doc leans on an SM-R1
  output is marked **provisional pending SM-R1**.
- **Feeds:** **SM-G1** (per the issue), and downstream SM-G4/G5 (bi-temporal edges/invalidation),
  SM-H4 (PPR integration), SM-X11 (persistence)
- **Grounded in:** `MEMORY-RESEARCH-episodic-and-neurosymbolic.md` (decisions D7 + D8).
  **Note:** as SM-R1 records, that file is cited by every sibling as
  `docs/MEMORY-RESEARCH-...` but is **not present in `docs/`** in this checkout, so it is cited
  second-hand through the siblings that quote it, never quoted directly here.
- **Type:** Spike — deliverable is the runnable comparison in this folder plus this recommendation.

---

## TL;DR

**Adopt raw Neo4j + SM-R10's custom bi-temporal layer. Do not adopt the Graphiti library.
Do not adopt Cognee. `lightrag` is a complement, not a replacement — and not a graph engine
for this purpose.**

The issue framed this as a four-way choice. Measured, it is not close:

| Candidate | Bi-temporal score (SM-R10's 30 probes) | Personalized PPR | Route |
|---|---|---|---|
| **Raw Neo4j + custom (SM-R10, already shipped)** | **30/30 = 100%** | ✅ `gds.pageRank(sourceNodes)` | **A** |
| Graphiti library over Neo4j | **19/30 = 63.3%** (measured here) | ✅ (same engine) | A |
| Cognee | **8/30 inexpressible** (no transaction axis) | ❌ zero pagerank in package | B |
| `lightrag` | n/a — no temporal fields at all | ❌ | B |
| *(single-time baseline, for scale)* | 17/30 = 56.7% (SM-R10) | — | — |

**Graphiti scores 63.3%, i.e. 6.6 points above a store with no bi-temporal model at all.**
The gap is not tuning — it is three structural defects in its write path (§2.1).

> **The issue names Graphiti-over-Neo4j as "preferred".** That preference is not adopted. It is
> overturned on measurement, not on taste: the engine underneath it (Neo4j) *is* the
> recommendation — what is rejected is the **library's temporal layer**, which scores 63.3% where
> the layer we already own scores 100%. So this lands close to the stated preference on
> infrastructure and against it on the pipeline.

---

## Questions to answer (from the issue)

| Question | Answer |
|---|---|
| Graphiti library directly (over Neo4j) vs the framework's `lightrag` vs Cognee vs raw Neo4j + our own bi-temporal layer? | **Raw Neo4j + our own bi-temporal layer** (SM-R10's, already built). Measured: Graphiti **19/30**, our layer **30/30**, Cognee **8/30 inexpressible**, `lightrag` has no temporal surface at all. §2. |
| Self-host footprint + license fit: Neo4j Community vs alternatives (FalkorDB, Kuzu)? | **Neo4j Community (GPLv3), self-hosted as a separate Bolt server.** Kuzu (MIT, embedded) is lighter and FalkorDB is mid-weight, but **neither has personalized PPR**, which is a hard requirement from SM-R9. ⚠️ The GDS plugin's own license text is contradictory and needs legal review. §5.3. |
| Can we get bi-temporal edges, **incremental** updates (no batch recompute), and a **no-query-time-LLM** read path? | **Yes, yes (with one qualification), yes.** Bi-temporal: 30/30. Incremental: the store is; **GDS projections are static snapshots**, so the PPR layer needs a re-projection cadence — measured, §Part 3. Read path: rule-based extraction at write time, pure-Cypher predicates at read time. |
| How much of Graphiti's pipeline (entity/edge/temporal extraction) can we reuse vs must wrap? | **None of Graphiti's pipeline is reused.** Its temporal extraction/invalidation is the part measured unfit (§2.1). We reuse **Neo4j** (storage, Cypher, GDS PageRank) and wrap every temporal semantic ourselves. §Reused vs wrapped. |

## Options evaluated (from the issue)

| Option | Exercised? | Result / disposition |
|---|---|---|
| **Graphiti over Neo4j** — *the issue's preferred option* | **Yes** — real `graphiti-core` 0.29.3 vs real Neo4j 5.26, scored on SM-R10's 30 probes | **REJECTED on measurement. 19/30 (63.3%)** vs 56.7% for a store with no bi-temporal model. Three structural write-path defects (§2.1): in-place mutation with no bounded reinsert, no same-`t_valid` tie rule, no back-dated bounding. Ships no 5a/5b predicate (0 `COALESCE`, 0 `expired_at IS NULL`). |
| **Framework `lightrag` graph-RAG** | **Yes** — source inspection of the in-tree client | **REJECTED as an engine; retained as a complement.** No temporal fields, no PPR, and `async_search` **silently discards filters**, so a point-in-time predicate cannot even be sent (§2.2). |
| **Cognee** | **Yes** — cognee 1.4.2 probed structurally against the same scenarios | **REJECTED on data model.** Single valid-time axis (`Event{time_from,time_to}`); **8 of 30 probes inexpressible**. Bi-temporal fields are import-only; its own docstring says bi-temporal search has not landed. Query-time LLM on the read path. Also Route B (no pagerank). §2.3. |
| **Raw Neo4j + custom bi-temporal layer** | **Yes** — already built and measured by SM-R10; cited, not rebuilt | **CHOSEN. 30/30** across five backends, SQL-native and Cypher-native probe-for-probe identical to the reference; guardrail 8/8; 5c 10/10. §2.4. |
| *(Zep Cloud — named in the issue's seed context)* | **Yes** — source inspection of `ZepTools` | **REJECTED on self-host.** Hosted SaaS requiring `ZEP_API_KEY`; incompatible with the self-host axis the issue asks about, and with Mode A offline operation. §Seed-context. |

## Seed-context verification & disposition

The issue's three named seed surfaces, verified against **current** post-rebrand code. All three
of the issue's paths are now **deprecated compatibility shims** — the real implementations moved
under `integrations/`.

| Seed (issue) | Current location | Verified | Disposition |
|---|---|---|---|
| `vectordb/lightrag/lightrag.py` — graph-RAG, not bi-temporal | real: [`egpt/integrations/vectordb/lightrag/lightrag.py`](../../libs/agno/egpt/integrations/vectordb/lightrag/lightrag.py) (391 lines); `egpt/vectordb/lightrag/lightrag.py` is a 29-line **deprecated shim (EGPT-1.7)** | Yes — thin HTTP client to an external LightRAG server; 0 temporal fields, 0 PPR; **filters silently dropped** | **COMPLEMENT — not promoted, not deleted.** Cannot carry a time predicate (§2.2). Matches SM-R1 §4 (provisional). |
| `tools/neo4j.py` — Neo4j as an agent tool | real: [`egpt/integrations/tools/neo4j/neo4j.py`](../../libs/agno/egpt/integrations/tools/neo4j/neo4j.py) (134 lines); `egpt/tools/neo4j/neo4j.py` is a **deprecated shim** | Yes — `GraphDatabase` driver + Cypher runner; a *tool*, not a store | **REUSE the driver dependency; the memory backend is NEW.** Confirms the issue's framing that the tier is net-new. |
| `tools/zep.py` — Zep as an agent tool | real: [`egpt/integrations/tools/zep/zep.py`](../../libs/agno/egpt/integrations/tools/zep/zep.py) (454 lines); `egpt/tools/zep/zep.py` is a **deprecated shim** | Yes — `zep_cloud` client, requires `ZEP_API_KEY`; exposes `graph.search`; **0 temporal fields locally** | **OUT — SaaS, fails self-host.** Zep Cloud is the managed form of Graphiti's model, so it inherits §2.1's semantics *and* adds a hosted dependency. Excluded on the issue's own self-host/license axis and incompatible with Mode A offline (SM-R1 §A.3c, provisional). |

> **Path-citation drift, for the record.** SM-R9's disposition table cites
> `libs/agno/egpt/tools/neo4j.py` and SM-R1 §4 cites `vectordb/lightrag/lightrag.py`. Both now
> resolve only to deprecated shims (and neither is a flat `.py` — each is a package directory).
> Cosmetic, but downstream tasks reading those tables will land on the shim rather than the
> implementation.

---

## Part 1 — Ground-truth findings (read before the comparison)

Everything below was read from source on `origin/main`, not from the issue text or a summary.
Three inherited premises turned out to be wrong or stale.

### 1.1 Neither existing prototype uses the Graphiti library — confirmed

- **SM-R9's KG/PPR is plain `networkx`.** [`harness/kg.py`](../memory_multihop/harness/kg.py)
  imports `networkx as nx` and calls `nx.pagerank(..., personalization=pers)`. No Graphiti.
- **SM-R10's Cypher store is hand-written.**
  [`adapters/cypher_native_store.py`](../memory_bitemporal/adapters/cypher_native_store.py) is
  literal Cypher string constants (`_SUPERSEDE`, `_BOUND`, `_INSERT`, `_RETRACT`). No Graphiti.
  It cites Graphiti's *conceptual* model only — SPEC §6 calls expire-and-reinsert
  "the canonical Graphiti/SCD-2 model", and §13 grounds the four timestamps in the research doc's
  Graphiti reference.

So "raw Neo4j + custom bi-temporal layer" is **not a hypothetical fourth option** — it is
built, measured, and the incumbent. This spike's job was to test whether an actual dependency
beats it. It does not.

### 1.2 The PPR-ownership gap is already closed — the brief's premise is stale

The task brief asked this spike not to "leave it unowned a second time." Read from source, it
was never left unowned:

- **SM-R9 `SPIKE.md` §Composition:** *"**Owner: SM-H4 ([aicoe-tracker#134]), with SM-G supplying
  the temporal graph — confirmed 2026-07-29.**"* Its AC #4 stays `[~]` because the **measurement**
  is outstanding, not the ownership.
- **SM-R1 ADR §B.2** (unratified, PR #54): *"Owner: SM-H4 (#134) … **Confirmed by this ADR.**
  It was **not** left unowned a second time."*

**One real inconsistency remains, and it is a stale document.** SM-R10 `SPEC.md` §12.1(b) still
reads *"Ranking quality over a time-filtered projection is untested, and is currently
**nobody's**"*, and its §12.1(c) table still says **"untested, unowned"** with *"needs
confirming"*.

SM-R9 characterises SM-R10 **accurately** — it says SM-R10 §12.1(b) *"records the same SM-H4
handoff **pending confirmation**"* and *"was awaiting exactly this confirmation."* (An earlier
draft of this section accused SM-R9 of misciting SM-R10; re-reading the source, that was wrong,
and it is corrected here rather than left standing.) The one overstatement is SM-R9's concluding
sentence — *"Both documents now describe one open measurement with one named owner"* — which is
aspirational: SM-R10 was never updated, so it still literally says "nobody's" / "unowned".

**Disposition (§Part 4):** ownership is **SM-H4 (#134)**, settled twice. The open item is a
**stale document**, not a stale decision — SM-R10 §12.1(b)/(c) should be updated to match. Filed
as a doc fix, not re-escalated. See §4.

### 1.3 Probe-count correction

The brief cited SM-R10's probe set as "21+9". Counted from
[`scenarios.py`](../memory_bitemporal/scenarios.py): **11 scenarios, 22 5a probes + 8 5b probes
= 30**. The total (30) and the 100% headline are right; the split is not. All scores in this
doc use the source counts.

### 1.4 `lightrag`'s path moved in the rebrand

SM-R9's disposition table and SM-R1 §4 both cite `libs/agno/egpt/vectordb/lightrag/lightrag.py`.
The file is now at
[`libs/agno/egpt/integrations/vectordb/lightrag/lightrag.py`](../../libs/agno/egpt/integrations/vectordb/lightrag/lightrag.py).
Cosmetic, but both citations are stale.

---

## Part 2 — The comparison (measured, not cited)

Reproduce: §Reproducibility. Raw results in [`results/`](results/).

### 2.1 Graphiti over Neo4j — **19/30 (63.3%). REJECTED.**

Tested with the **real `graphiti-core` 0.29.3** against the **same Neo4j 5.26 container SM-R10
measured on**, scored on **SM-R10's own scenarios and expected answers** (imported, not
re-derived). The adapter routes every load-bearing step through library code:
`graphiti_core.edges.EntityEdge`, its real contradiction rule
`resolve_edge_contradictions`, and its real `EntityEdge.save()`.

Two choices deliberately **favour** Graphiti: the LLM is bypassed (valid-times are handed in from
the timeline, removing extraction error as a confound), and the read path is the *most favourable
correct* 5a/5b predicate — both bounds, open-interval branch, transaction-axis filter. Graphiti
ships none of that itself (see §2.1.4). **Every failure below is therefore a write-model failure,
not a strawman query.**

```
graphiti-core on SM-R10 scenario set: 19/30 = 63.3%

  simple_insert              3/3
  supersede_relocation       2/3   <-- FAIL
  idempotent_noop            1/1
  three_way_chain            1/3   <-- FAIL
  backfilled_since_jan       1/2   <-- FAIL
  retract_error              2/2
  predicate_isolation        2/2
  tenant_isolation           2/2
  belief_evolution           2/3   <-- FAIL
  same_t_valid_tiebreak      1/4   <-- FAIL
  deep_recorrection          2/5   <-- FAIL
```

Three distinct root causes, each traced to a line of library source.

#### (a) In-place mutation, no bounded reinsert — the past is destroyed

`utils/maintenance/edge_operations.py:569-570`:

```python
edge.invalid_at = resolved_edge.valid_at
edge.expired_at = edge.expired_at if edge.expired_at is not None else utc_now()
```

The superseded edge is mutated **in place** and gets `expired_at` set **at the same time** as
`invalid_at`. No bounded copy is inserted. It is then persisted by
`models/edges/edge_db_queries.py` (Neo4j branch):

```cypher
MERGE (source)-[e:RELATES_TO {uuid: $edge_data.uuid}]->(target)
SET e = $edge_data
```

`SET e = $edge_data` is a blanket full-property overwrite on the same uuid. This is precisely
the write shape **SM-R10 §6.1 names as the footgun**: *"a blanket `INSERT ... ON CONFLICT DO
UPDATE SET <every column>` … silently rewrites `t_valid`/`t_invalid` in place, which destroys
exactly the history SCD-2 exists to preserve — 5b stops reconstructing and the audit trail is
gone, with no error and no failing query."* SM-R10's `memory_edge_append_only` trigger (measured
8/8) exists to **reject** this exact statement shape.

Forensic confirmation ([`forensics.py`](forensics.py)), NYC→LA→SF:

```
  rows stored by Graphiti: 3          (SM-R10's model stores 5: 3 current + 2 bounded history)
  object valid_at     invalid_at   created_at   expired_at
  NYC    2024-01-01   2024-06-01   2024-01-01   2026-08-10
  LA     2024-06-01   2024-09-01   2024-06-01   2026-08-10
  SF     2024-09-01   NULL         2024-09-01   NULL

  "Where did the user live in Feb 2024?"  ->  []      (ground truth: ['NYC'])
```

Because the superseded row is *simultaneously* bounded and expired, it is no longer current
belief — so the past window `[Jan, Jun)` is unreachable from 5a. Past truth does not survive a
correction. This single defect accounts for 5 of the 11 failures.

**Also visible in that dump:** `expired_at = 2026-08-10` — Graphiti stamps `utc_now()` (wall
clock), not an injectable transaction time. SM-R10 §6 requires injectability explicitly
(*"An earlier revision of this spec hardcoded `now()`, which made those cases inexpressible"*),
because backfill, replay, and every 5b reconstruction depend on it.

#### (b) Same-`t_valid` tie-break: two live contradictory answers

`edge_operations.py:564-567` invalidates only when `edge_valid_at < resolved_edge_valid_at` —
**strictly** less-than. At equal `valid_at` the branch is skipped and nothing is invalidated, so
both facts stay current:

```
[5a] same_t_valid_tiebreak  t=JUN  expected='LA'  got=['LA','NYC']
[5a] same_t_valid_tiebreak  t=DEC  expected='LA'  got=['LA','NYC']
```

SM-R10's **R1-TIE** resolves this as last-writer-wins, deriving it from the half-open interval
(`valid_at` contains its own left endpoint) — and measures it. Graphiti has no tie rule at all.
A 5a query that must have exactly one answer returns two.

#### (c) No back-dated bounding

Graphiti only invalidates when the *existing* edge is older than the incoming one. A back-dated
insert (new edge older than an existing one) is never bounded, so windows overlap:

```
[5a] backfilled_since_jan  t=JUL  expected='LA'  got=['NYC','LA']
[5a] deep_recorrection     t=JUL  expected='LA'  got=['SF','LA']
```

SM-R10's rule — `E_new.t_invalid := min(E_new.t_invalid, earliest later conflict's t_valid)` —
has no Graphiti counterpart.

#### (d) The 5a/5b predicates are not shipped at all

Verified across the whole package: **`COALESCE` appears 0 times**, and **`expired_at IS NULL`
appears 0 times**. Graphiti provides the four timestamp *fields* and a generic `SearchFilters`
OR-of-AND DSL; it provides no "true at T" predicate. The NULL/open-interval correctness burden
that SM-R10 measured and fixed (its 5c bare-predicate counterexample scores 7/10 vs 10/10) is
left entirely to the caller.

#### (e) No SCD-2 mode was missed

Checked before concluding: `grep -niE "scd|version_history|bounded_version|reinsert|append.only|
immutab"` over the package returns **nothing**. There is no configuration that turns on
expire-and-reinsert. The defect is architectural.

> **What Graphiti is genuinely good at, stated fairly.** It ships maintained drivers for Neo4j,
> FalkorDB, Kuzu and Neptune; an LLM-based extraction/dedup pipeline; and community detection.
> If the requirement were "get a temporal-ish KG standing quickly", it is a reasonable pick.
> Our requirement is a **bi-temporal system-of-record**, and on that it is measurably unfit.

### 2.2 `lightrag` — **not extensible to bi-temporal. Complement, not replacement.**

SM-R9/SM-R10's assessment stands, and inspection makes it stronger than "graph-RAG, not
bi-temporal". In
[`lightrag.py`](../../libs/agno/egpt/integrations/vectordb/lightrag/lightrag.py):

- **Zero** occurrences of `valid_at`/`invalid_at`/`t_valid`/`t_invalid`/`expired_at`. No temporal
  surface to extend.
- **Zero** occurrences of `pagerank`/`personalization`. Confirms SM-R9's "no PPR".
- It is a thin HTTP client implementing the `VectorDb` interface. `async_search` posts only
  `{"query", "mode": "hybrid", "include_references"}` to an external server — and **explicitly
  discards filters**:

  ```python
  if filters is not None:
      log_warning("Filters are not supported in LightRAG. No filters will be applied.")
  ```

**This is the decisive point:** there is no channel through which a point-in-time predicate could
even be *sent*, and the one argument that could carry it is accepted and silently dropped.
Bi-temporal filtering is structurally inexpressible at this seam, and fixing it would mean
changing the upstream LightRAG **server**, not our client.

**Relationship (AC): COMPLEMENT — and out of the engine role.** `lightrag` stays what SM-R1 §4
(provisional) calls it: an available `GraphSubstrate` candidate backend, not promoted, not
deleted. It does not replace the temporal graph and the temporal graph does not replace it.
It is *not* a Route B PPR fallback either — it has no PPR to fall back to.

### 2.3 Cognee — **REJECTED on data model; also Route B.**

Prototyped structurally against SM-R10's scenario set
([`run_cognee.py`](run_cognee.py), cognee 1.4.2, all assertions re-checked at runtime):

```
  Event model fields         : ['description', 'location', 'name', 'time_from', 'time_to']
  has valid-time axis        : True
  has TRANSACTION axis       : False   <-- 5b needs this
  temporal read path uses LLM: True
    -> interval = await LLMGateway.acreate_structured_output(query, system_prompt, QueryInterval)
  files mentioning pagerank  : NONE

  SM-R10 probes total        : 30
    expressible (5a)         : 22
    INEXPRESSIBLE (5b)       : 8
```

Four disqualifying findings:

1. **Single valid-time axis only.** `Event{time_from, time_to}` — no belief axis. The 8 5b probes
   are not *failed*, they are **inexpressible**. Structurally this is SM-R10's rejected
   single-time option (56.7%).
2. **Its bi-temporal fields are import-only, and cognee says so.** `valid_at`/`invalid_at` appear
   solely in `modules/migration/` (a path for ingesting *from* Graphiti/Zep).
   `COGXFact`'s own docstring: *"Temporal validity … is always carried, even where the loader
   **cannot yet query it natively** — it is stored as edge properties so the data is in place
   **when bi-temporal search lands**."* The library states bi-temporal search has not landed.
   Note `expired_at` is absent even there.
3. **Validity is degraded to prose.** `modules/migration/loader.py:97-100` renders temporal bounds
   into the fact *string*: `"... (valid from 2024-01-01, invalid since 2024-06-01)"`. That is not
   a queryable predicate; answering a point-in-time question means an LLM reading English.
4. **Query-time LLM on the read path.** `TemporalRetriever.extract_time_from_query` calls
   `LLMGateway.acreate_structured_output` **per query** to parse the interval. This violates the
   locked design constraint (research doc §5 / SM-R9: no query-time LLM on the hot path) — see §3.

**Honest limit:** cognee's full `cognify` pipeline was **not** run end-to-end (it needs LLM
credentials). That does not change the finding — items 1 and 4 are properties of the data model
and read path, and no pipeline run can add a transaction axis that the model does not define.
The probe is written to fail loudly if a future version adds one.

### 2.4 Raw Neo4j + custom — **INCUMBENT, cited not rebuilt. 30/30.**

Per instruction, not re-derived. SM-R10's measured evidence, quoted from its SPEC §1:

> the bi-temporal model scores **100% (30/30)** point-in-time reconstruction across all five
> bi-temporal backends — including the SQL-native and Cypher-native paths, which are
> **equivalent to the Python reference on every probe**. The single-time baseline scores
> **56.7% (17/30)**. The 5c vector-tier filter scores **10/10** on both the reference and real
> SQL, and the §6.1 append-only guardrail **8/8**.

**One caveat carried forward, in SM-R10's own words** — the 100% is *one shared rule over three
storage layers*, not three independent derivations:

> The invalidation rule and the query semantics live *once*, in `store.py` … So a matching score
> across those three is **one shared rule over three storage layers**, not three independent
> derivations of the semantics.

The SQL-native and Cypher-native adapters are the stronger claim (probe-for-probe identical to
the reference), and they are what SM-G4/G5/X11 actually consume.

---

## Part 3 — Feasibility confirmed for the recommendation

Confirmed for the recommended engine, not re-derived.

| Requirement | Verdict | Evidence |
|---|---|---|
| **Bi-temporal edges** | ✅ | SM-R10's four-timestamp SCD-2 model, 30/30 on real Neo4j + real Postgres. |
| **Incremental updates (no batch recompute)** | ✅ **store** / ⚠️ **PPR projection** | See below — a real qualification. |
| **No query-time LLM on the read path** | ✅ | See below. |

**Incremental — the store is, the PPR projection is not.** SM-R10's expire-and-reinsert is
inherently incremental (one expire + two inserts per contradiction; no global recompute), and
Neo4j imposes no batch pattern on it. **But GDS graph projections are static snapshots**, which
is measured here rather than assumed:

```
PPR before new edge      : {'A': 0.15, 'B': 0.1275, 'C': 0.0}
PPR after  new edge      : {'A': 0.15, 'B': 0.1275, 'C': 0.0}     <- write invisible
projection relationships : 1 (live DB now has 2)
after RE-projection      : {'A': 0.15, 'B': 0.1275, 'C': 0.1084}
```

A write to the live database is **invisible to an existing projection** until it is re-projected.
So "incremental" holds for the bi-temporal system-of-record (the AC), but **SM-H4 must budget a
re-projection cadence** for the PPR layer. This is not a Route A penalty — Route B's exported
adjacency has the same property — but it corrects any assumption that "Neo4j GDS gives you
incremental PPR." Flagged for SM-H4; not previously recorded by SM-R9.

**No query-time LLM.** SM-R10's `ValidTimeExtractor` is rule-based by default (LLM optional, at
**write** time only) — SPEC §8, measured 6/6. SM-R9's OpenIE runs at **index** time. The 5a/5b
predicates are pure Cypher. The one residual is SM-R9's query-time **NER**, which SM-R9 itself
says *"SM-H4 can drop … by replacing the LLM NER with a cheap entity linker"* — a known,
owned item, not a new one. **Contrast:** Cognee's temporal read path calls an LLM per query and
cannot be configured out of it (§2.3), which is an independent reason it fails this requirement.

---

## Part 4 — PPR ownership (resolve or escalate)

**RESOLVED — no escalation needed. Owner: SM-H4 ([aicoe-tracker#134]), with SM-G supplying the
temporal graph.**

The brief's premise (that SM-R9 "could not decide unilaterally" and this risks being unowned a
second time) is **stale**: SM-R9 §Composition records it as *"confirmed 2026-07-29"*, and SM-R1
§B.2 confirms it again. This spike adds no new assignment and does not reopen it.

What this spike **does** close, as the engine decision it owns:

1. **The stale document.** SM-R10 §12.1(b)/(c) still says *"nobody's"* / *"untested, unowned"* /
   *"needs confirming"*. That is now the only source claiming the item is unowned. **Action: a
   doc-only fix to SM-R10 §12.1(b)/(c)** to cite #134. Not done in this PR (SM-R10 is a merged
   sibling's artifact); raised for its owner. Escalate only if SM-R10's owner disputes it.
2. **The binding constraint the measurement inherits**, which is an engine-decision output:
   the projection **must** use SM-R10 §7's full 5a predicate verbatim, including
   `coalesce(e.t_invalid, ∞)` and `e.expired_at IS NULL`. Dropping either changes graph
   **topology** and is a correctness bug. Route A makes this a projection-time `WHERE` clause —
   demonstrated working in [`run_ppr_route.py`](run_ppr_route.py) (5 rels → 4, ranking changes).

**Scope boundary, stated plainly:** this spike proves the composition **mechanically** on
temporal data — SM-R9 had only established it *by design* ("confirmed by design only — never
exercised on temporal data"). **Ranking *quality* over a bi-temporally-filtered projection
remains unmeasured and remains SM-H4's.** Filtering the projection is not the same as knowing
PPR still ranks well over it.

[aicoe-tracker#134]: https://github.com/Saturam-Inc/aicoe-tracker/issues/134

---

## Part 5 — Route A / Route B, and the license + self-host comparison

### 5.1 Which route applies: **Route A**, and it is verified on Community

SM-R9 made the low-risk path conditional on "a GDS-capable engine" but never checked one. Checked
here on a real deployment ([`run_ppr_route.py`](run_ppr_route.py)):

```
Neo4j edition           : community
GDS version             : 2.13.11
gds.* procedures        : 423
PPR seeded from A       : {'A': 0.5, 'E': 0.140625, 'B': 0.125, 'C': 0.0625, 'D': 0.03125}
PPR seeded from E       : {'E': 0.5, 'A': 0.0, 'B': 0.0, 'C': 0.0, 'D': 0.0}
personalization is real : True
```

The personalization check matters: different seeds must give different rankings, so a silent
fallback to unseeded PageRank cannot pass as success. It does not.

**Because the recommendation is a Neo4j-family engine, Route A applies and SM-R9's "moderate,
unbudgeted" Route B cost is *not incurred*.** The prototype's `networkx`
`nx.pagerank(personalization=…)` maps 1:1 onto `gds.pageRank(sourceNodes=…)`.

**Sizing Route B anyway (what we avoid).** Had this landed on FalkorDB/Kuzu/Cognee, SM-R9's Route
B requires a new service: projection export, sparse power-iteration loop, cache + invalidation,
plus applying the time filter at export time. Concretely that is a new deployable component with
its own scaling and failure modes, owned by nobody today — SM-R9: *"moderate — unbudgeted; adds a
component SM-H4 does not currently plan for."* Choosing Route A retires that risk item outright.

### 5.2 Native PPR capability — measured on each engine

Not cited. Each engine was stood up and probed.

| Engine | Global PageRank | **Personalized (source-seeded) PPR** | Evidence |
|---|:--:|:--:|---|
| **Neo4j CE + GDS 2.13.11** | ✅ | ✅ `gds.pageRank(sourceNodes:[…])` | run above; personalization verified |
| FalkorDB (v4.2, `algo.*`) | ✅ `algo.pageRank` | ❌ | full `dbms.procedures()` list has no PPR; `algo.personalizedPageRank` → *"is not registered"* |
| Kuzu 0.18.2 + ALGO ext | ✅ `PAGE_RANK` | ❌ | `sourceNodes` / `source_nodes` / `personalization` all → *"Unknown optional parameter"* |
| `lightrag` | ❌ | ❌ | no `pagerank` in source |
| Cognee 1.4.2 | ❌ | ❌ | zero files mention pagerank |

**This confirms and sharpens SM-R9's warning.** SM-R9 §Integration route says *"three of SM-R2's
five candidates are not GDS-capable"* while its §Decision names four non-GDS engines (FalkorDB,
Kuzu, `lightrag`, Cognee). Both are consistent if FalkorDB/Kuzu count as one candidate slot — so
this is **not** an error in SM-R9, and it is recorded that way deliberately rather than as a
"found bug". The measured substance: **only the Neo4j family provides source-seeded PPR**, and
plain global PageRank is useless for HippoRAG, which is defined by query-seeded personalization.

### 5.3 License and self-host

**Neo4j Community — GPLv3.** From the shipped `NOTICE.txt` in the running container:

> The Software developed and owned by Neo4j is licensed under the GNU GENERAL PUBLIC LICENSE
> Version 3 … to all third parties

- **Practical read:** we deploy Neo4j as a **separate server process** reached over the Bolt
  protocol; we do not link or redistribute it. That is the ordinary self-host posture and is why
  GPLv3 is workable here. ⚠️ **This is an engineering observation, not legal advice — legal
  should confirm**, especially if Neo4j is ever embedded or redistributed in a customer artifact.
- **Community edition limits** that matter operationally: single database, no clustering/HA, no
  hot backups, no RBAC beyond basics. For the v1 envelope (**provisional pending SM-R1**;
  note SM-R1 §A.1 surfaces an unreconciled 50M-vs-10M scale arithmetic) this is acceptable, but
  **HA/backup requirements are the thing that would force Enterprise**, not the query features.

**⚠️ GDS licensing — flagged, needs legal confirmation before production.** The plugin
auto-installed by `NEO4J_PLUGINS='["graph-data-science"]'` (from `graphdatascience.ninja`) ships
**two contradictory license files**:

- `LICENSE.txt` = *"NEO4J, INC. PRE-RELEASE AGREEMENT"* — evaluation-only, 30-day, referring to
  "NEO4J ENTERPRISE SOFTWARE";
- `NOTICE.txt` = a stale GPLv3 notice (copyright 2002-**2018**).

The jar also contains `com/neo4j/gds/extension/LicenseChecker*` classes, i.e. license-gated
features. Functionally, `gds.debug.sysInfo()` reports **`gdsEdition: Unlicensed`** and
**personalized PageRank runs fine in that state** — so the capability we need is in the free
tier. **But the bundled license text does not say so**, and this is unresolved by measurement.
**Action: legal to confirm which terms govern the GDS jar before production**, and pin a specific
GDS version rather than letting the Docker plugin mechanism fetch "latest". This is a genuine
finding — no sibling has looked at GDS licensing.

**Alternatives, for completeness:**

| | Neo4j CE + GDS | FalkorDB | Kuzu |
|---|---|---|---|
| License | GPLv3 (kernel); GDS jar terms ⚠️ unclear | Server-Side Public License (SSPL) — verify with legal | MIT |
| Footprint | JVM server, heaviest of the three | Redis module, light | **embedded** library, lightest (no server) |
| Ops burden | highest — JVM tuning, separate service | moderate | lowest — in-process |
| **Personalized PPR** | **✅** | ❌ | ❌ |
| Bi-temporal | via SM-R10's layer (proven on it) | would need porting | would need porting |
| Verdict | **CHOSEN** | rejected — no PPR ⇒ Route B | rejected — no PPR ⇒ Route B |

Kuzu is genuinely attractive on license (MIT) and ops (embedded, no server) and is worth
remembering if the PPR requirement is ever dropped. It is rejected **only** on personalized PPR.
FalkorDB's SSPL is the more restrictive license of the three and it also lacks PPR, so it loses
on both axes.

---

## Reused vs wrapped (AC)

**How much of the chosen engine's pipeline we reuse vs wrap:**

| Layer | Disposition | Note |
|---|---|---|
| Neo4j storage, Cypher, indexes, transactions | **REUSE as-is** | no wrapper; the engine does this well |
| `gds.pageRank(sourceNodes)` + `gds.graph.project` | **REUSE as-is** | replaces SM-R9's `networkx` prototype 1:1 |
| Bi-temporal semantics (4 timestamps, R1/R1-TIE/R2, back-date bounding, 5a/5b) | **WRAP — ours, SM-R10's `store.py` + native adapters** | the engine supplies none of this; Graphiti's attempt scores 63.3% |
| Valid-time extraction | **WRAP — SM-R10 `ValidTimeExtractor`** | rule-based default, write-time only |
| Entity/triple extraction (OpenIE) | **WRAP — SM-R9's `openie.py`** | index-time |
| Append-only enforcement | **WRAP + ⚠️ gap on Neo4j** | see below |
| Graphiti's ingest/dedup/community pipeline | **NOT USED** | rejected §2.1 |

So: **the engine is reused for storage and ranking; every temporal semantic is ours.** This is a
thin-wrapper posture, not a framework adoption — which is exactly why the Graphiti dependency
buys nothing: the part we would import is the part that is wrong.

**Carried-forward gap (SM-R10 §6.1, re-stated because it is now the ratified engine):** Postgres
enforces append-only history with the `memory_edge_append_only` trigger (8/8 measured); **Neo4j 5
has no property-immutability constraint**. SM-R10 asked implementers to *"record that asymmetry
wherever the engine choice is ratified"* — **recorded here**. SM-G4 must pick one of SM-R10's two options:
APOC trigger (parity, unverified, adds an APOC dependency — permitted under this recommendation)
or write-through repository + the scheduled invariant audit (verified, but detection-after-the-fact
and strictly weaker). **This spike does not decide it**; it confirms the engine choice does not
remove the need to.

---

## Acceptance criteria (verbatim from #77)

- [x] **Prototype ingests a sample corpus into a temporal graph and answers a point-in-time and a
      multi-hop query.** Point-in-time: SM-R10's 30 probes, re-run here for Graphiti (19/30) and
      cited for the incumbent (30/30). Multi-hop: SM-R9's PPR prototype, plus a **new** measured
      `gds.pageRank(sourceNodes)` run over an SM-R10 5a-filtered projection
      ([`run_ppr_route.py`](run_ppr_route.py)) — the first time the two compose on temporal data.
      *Honest scope:* the corpora are the siblings' (SM-R10's constructed timelines, SM-R9's
      2Wiki-derived KG), not a new one — deliberately, so the engines are scored on one ruler.
- [x] **Engine recommendation documented with license + self-host notes (Neo4j vs FalkorDB/Kuzu)**
      — §5.3, including the GDS license flag and the measured PPR-capability table.
- [x] **Confirms bi-temporal + incremental + no-query-time-LLM feasibility** — §Part 3, with the
      GDS-projection-staleness qualification stated rather than glossed.
- [x] **States how much of the chosen engine's pipeline we reuse vs wrap** — §Reused vs wrapped.
- [x] **Notes relationship to the existing `lightrag` backend (replace / complement)** — §2.2:
      **COMPLEMENT**, on measured evidence (filters silently dropped; no temporal fields; no PPR),
      not the inherited assumption.

**Also delivered (not issue ACs — added by the task brief):**

- [x] **Resolves or escalates the SM-R9 PPR-ownership gap** — §Part 4: already resolved to SM-H4
      (#134) before this spike; the residue is a stale SM-R10 §12.1, raised for its owner.
- [x] **States which Route applies and sizes Route B** — §5.1: **Route A**, verified on a real
      deployment; Route B sized as the avoided cost.

### Latency & integration cost (issue Deliverable asks for latency notes)

The issue's deliverable line asks for "license, self-host, **latency**, and integration-cost
notes". Stated honestly, because **this spike did not run a latency benchmark**:

| Source | Number | Status |
|---|---|---|
| PPR compute (SM-R9, 285-passage KG) | **<100 ms** — hybrid retrieves in 0.056 s mean once query embed/NER are cached | measured by SM-R9, cited |
| SM-R9 end-to-end query p95 | vector 2.2 s / hippo 3.8 s — **dominated by live query-time API calls**, not PageRank | measured by SM-R9, cited |
| Neo4j per-op (SM-R10 prototype) | ~0.7–4.7 s for the whole set — SM-R10 calls this **"a naive-session artifact, not a tuned number"** | measured by SM-R10, explicitly not a target |
| GDS projection build | **not measured here** — the §Part 3 graphs are 3–5 nodes; they establish capability, not performance | **gap** |
| Integration cost | **Route A = 1:1 mapping**, `nx.pagerank(personalization=)` → `gds.pageRank(sourceNodes=)`; no new component. Route B avoided. | §5.1 |

**The one latency item this spike newly creates is the re-projection cadence** (§Part 3): GDS
projections are snapshots, so PPR freshness is bounded by how often the projection is rebuilt.
Sizing that against corpus size is **SM-H4's**, and it needs a real graph — flagged, not faked.
No latency target is claimed or ratified here; **targets are SM-R1's** (provisional).

---

## Out of scope & boundaries

- **PPR ranking *quality* over a bi-temporal projection** → SM-H4 (#134). This spike proves the
  mechanism composes; it does not measure ranking quality.
- **KG construction / temporal graph build** → SM-G*. **Bi-temporal schema** → SM-R10 (unchanged).
- **Append-only enforcement mechanism on Neo4j** → SM-G4 (options in SM-R10 §6.1).
- **GDS license terms** → legal. Flagged, not resolved.
- **Scale / latency targets** → SM-R1 (**provisional, not ratified** — PR #54 open).
- **Core code** → untouched. `git diff origin/main -- libs/` is empty.

## Consistency with dependencies & siblings

| Sibling | Status | Detail |
|---|---|---|
| **SM-R1** (#76) | **NOT RATIFIED** (PR #54 open) | §4's ruling ("engine explicitly NOT chosen; weigh SM-R9's PPR requirement as an input; cost Route B if non-GDS") is followed. All SM-R1-derived claims marked provisional. |
| **SM-R9** (#84) | **consistent; its conditional is now discharged** | Its GDS assumption was conditional on SM-R2 picking a GDS-capable engine. It does — Route A. Its "three of five non-GDS" framing is reconciled, not contradicted (§5.2). |
| **SM-R10** (#85) | **ratified as the engine's temporal layer; one stale section** | 30/30 cited, not rebuilt. §12.1(b)/(c) is now the only doc calling PPR ownership open (§1.2). Its §6.1 Neo4j-enforcement gap is recorded here as it asked. |
| **SM-R11** (#20, provider spec) | **consistent — and this spike supplies the answer it deferred** | SM-R11 §"Out of scope" says *"Graph engine choice → SM-R2"* and its disposition table lists `tools/neo4j.py` / `tools/zep.py` as *"Repurpose-candidates as `GraphSubstrate` backends (SM-R2 / SM-G1)"*. **Answered here:** Neo4j **in**, Zep **out** (SaaS, fails self-host). Neo4j also matches its Mode A wiring (*"in-VPC pgvector + Neo4j"*). **One label to fix:** its substrate diagram reads `GraphSubstrate (Neo4j+Graphiti / Zep …)` — this decision **splits that pair**, taking Neo4j and rejecting the Graphiti library. Illustrative text, not a decision, but it will mislead if read as one. |
| **SM-R12** (#18, data model) | **no conflict** | Zero engine mentions in its SPEC; `SemanticRelation` is the bi-temporal graph edge, which is exactly what SM-R10's layer implements. Nothing here changes its schema. |
| **SM-R3** (#78, vectordb) | **no conflict** | Explicitly out of scope on its side: *"graph/temporal tier (SM-R2/R10) — not decided here."* |
| **SM-R4 / SM-R8** | untouched | no embedder or fusion claim made here. |

## Reproducibility

```bash
python3 -m venv .venvs/graphengine
.venvs/graphengine/bin/python -m pip install graphiti-core cognee kuzu

# engines under test
cd benchmarks/memory_bitemporal/docker && docker compose -p smr10 -f neo4j-compose.yml up -d
docker run -d --name smr2-gds -p 7690:7687 -e NEO4J_AUTH=neo4j/gdstestpassword \
  -e NEO4J_PLUGINS='["graph-data-science"]' neo4j:5
docker run -d --name smr2-falkor -p 6390:6379 falkordb/falkordb:latest

PYTHONPATH=. .venvs/graphengine/bin/python benchmarks/memory_graph_engine/run_graphiti.py
PYTHONPATH=. .venvs/graphengine/bin/python benchmarks/memory_graph_engine/forensics.py
PYTHONPATH=. .venvs/graphengine/bin/python benchmarks/memory_graph_engine/run_cognee.py
PYTHONPATH=. .venvs/graphengine/bin/python benchmarks/memory_graph_engine/run_ppr_route.py
```

Committed artifacts: [`results/graphiti_bitemporal.json`](results/graphiti_bitemporal.json)
(per-probe detail), [`results/cognee_probe.json`](results/cognee_probe.json),
[`results/ppr_route_a.json`](results/ppr_route_a.json).

**Honest limits.** (a) Graphiti was driven without its LLM extraction layer — favourable to it,
but it means extraction quality is untested (irrelevant to the verdict, which is about the write
model). (b) Cognee was probed structurally, not run end-to-end (needs credentials); the finding
rests on its data model and its own docstring. (c) The GDS/PPR runs use tiny synthetic graphs —
they establish **capability and composition**, not performance or ranking quality. (d) SM-R10's
30/30 is one shared rule over multiple storage layers, per its own caveat (§2.4). (e) Neo4j and
GDS license readings are engineering observations pending legal review.
