# Findings — SM-R11 MemoryProvider spec

Design findings and bugs caught while prototyping the seam. These are prototype-level
(they live in this spec folder, not in `egpt` core) but each encodes a **contract requirement
SM-C1 must honor**. No patches against core were needed (this task adds no core code).

## F1 — A logical episode must share ONE id across substrates

**Symptom:** a single write produced two fused records in `read`.
**Cause:** the provider minted a fresh id when writing to the vector substrate and again when
writing to the graph substrate, so RRF (which dedups by id) saw two records.
**Fix:** `MemoryEvent` gained an optional `id`; `write` stamps one id and passes it to both
substrates (`adapters.py: CoLocatedMemoryProvider.write`).
**Contract requirement for SM-C1:** the episode identity is assigned once at write time and is
the dedup key across vector + graph. SM-R12's schema should carry a stable episode/record id.

## F2 — Point-in-time validity must be owned at the fused read path, not per-substrate

**Symptom:** after invalidating a fact on the graph, an `as_of` read still returned it.
**Cause:** `as_of` filtering lived only inside `graph.traverse`; `read` fused in vector hits,
which have no notion of validity, so the invalidated fact leaked back in.
**Fix:** the graph is authoritative for bi-temporal validity; the provider strips `as_of` before
querying substrates, merges the most-restrictive `valid_to` during RRF, and applies the
point-in-time filter on the **fused** set (`adapters.py: CoLocatedMemoryProvider.read` +
`_rrf` merge).
**Contract requirement for SM-C1 / SM-R10:** bi-temporal `valid_to` is set on the graph
(invalidate-not-delete); the provider's read path enforces `as_of` after fusion. The vector
substrate is not the source of truth for validity.

## F3 — Substrate reads must honor `query.tiers` consistently

**Symptom:** a `read(tiers=[SEMANTIC])` returned EPISODIC records.
**Cause:** the vector substrate filtered by `query.tiers` but the graph substrate only honored
an explicit single-`tier` argument, so with `tier=None` it scanned all tiers.
**Fix:** both substrate stand-ins now resolve the tier set the same way
(`{tier} if tier else query.tiers or all`).
**Contract requirement for SM-C1:** every substrate must apply the tier filter identically, or
cross-tier leakage appears only after fusion and is hard to trace.

## F4 — Layering vs SM-R12 (#87): complementary, not overlapping

**Observation:** SM-R12 had already authored its data model
(`benchmarks/memory_datamodel/model.py`) in its worktree. Comparing it to this spec confirmed a
clean split rather than a duplicate: **R12 owns the persisted per-tier row schemas**
(`EpisodicRecord`, `SemanticFact`/`Entity`/`Relation`, `ProceduralRecord`, `WorkingMemory`,
`MemoryLink`) on a `TenantScoped`+`BiTemporal`+`Embeddable`+`Decaying` spine; **this spec owns the
seam envelope** (`MemorySlice`/`MemoryQuery`/`MemoryEvent`/`Scope`), which R12 does not define.
**Action taken (align-now):** renamed the seam's bi-temporal fields to R12's canonical
`t_valid`/`t_invalid`/`created_at`/`expired_at`/`superseded_by`, switched time to int epoch-seconds,
added `team_id` to `Scope`, and reframed `MemoryRecord` as a read-projection over R12's rows.
**Contract requirement for SM-C1:** consume R12's row types as the source of truth; the seam's
`MemoryRecord` is a projection, not a second schema. R12 is unmerged — final reconciliation on
ratification is SM-C1's.

## F5 — SM-R8 consistency: weighted fusion was missing (default was RRF)

**Symptom:** `config.fusion["method"] = "weighted"` silently no-op'd — the provider hardcoded RRF.
**Contradiction:** SM-R8's ratified DECISION.md picks **weighted / relative-score fusion** as the
2-leg default (measured, significant win over RRF on all 4 datasets); RRF is reserved for the
SM-G9-gated 3-leg case *and even there is provisional* (SM-R8's synthetic 3-leg test found
weighted's edge over RRF *widened* +0.023→+0.076, not flipped).
**Fix:** added a real `_weighted` (min-max normalize per leg → `sum(weight_i · norm_i)`), made it
the default (`vector_weight≈0.5`), kept `_rrf` selectable via `config.fusion["method"]="rrf"` for
the graph-join case. `MemoryProviderConfig.fusion` default is now `{"method":"weighted",...}`.
**Contract for SM-C1:** default to weighted; only switch to RRF once SM-G9's real graph retriever
is measured (do not adopt RRF automatically on graph-join).

## F6 — SM-R10 consistency: point-in-time read was one-sided

**Symptom:** `_as_of`/`traverse` only checked the `t_invalid` upper bound, so a **future-dated**
record (`t_valid > as_of`) leaked into an as-of read.
**Contradiction:** SM-R10's ratified "true at T" is the **two-sided** `t_valid <= T < t_invalid`
(§7 explicitly flags both bounds as required).
**Fix:** shared `_valid_at(record, at)` helper enforces both bounds (`t_valid=None`⇒since-forever,
`t_invalid=None`⇒open); used by both `_as_of` and `traverse`. Added a future-dated swap-test case.

## F7 — SM-R7 consistency: `consolidate` claimed dual-write but wrote vector only

**Symptom:** `ConsolidationReport` docstring said "write to BOTH substrates," but the reference
adapter's `consolidate` only upserted to the vector substrate.
**Fix:** `consolidate` now writes the distilled semantic fact to **both** substrates (same id), so
the claim is true even in the stub. Deterministic Jaccard dedup remains; LLM extraction +
contradiction-resolution stay explicitly scoped to SM-C* (`contradictions_resolved` stays 0).

## F8 — SM-R9 attribution: PPR was mis-cited as SM-R8

**Fix:** PPR (personalized PageRank) is SM-R9's ratified decision (optional, per-tenant flag,
default off), not SM-R8's. Relabelled in `interface.py` (`read` docstring, `MemoryProviderConfig`)
and SPEC §5.3/§7/§9. Rerank stays attributed to SM-R8.

## Note — "empty query text = scan" convention

`consolidate` needs to enumerate all episodic records, not run a relevance search. Rather than
add an enumeration method to the substrate ports, the stand-ins treat an **empty `query.text`**
as "scan all (unranked)". SM-C1 may prefer an explicit `scan`/iterator on the port; flagged as a
design choice, not a requirement.
