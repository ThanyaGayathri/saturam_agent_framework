# SM-R9 — Spike: multi-hop need + HippoRAG PPR

- **Status:** Proposed — **GO** (spike complete; measured go/no-go below)
- **Issue:** [Saturam-Inc/aicoe-tracker#84](https://github.com/Saturam-Inc/aicoe-tracker/issues/84)
- **Depends on:** SM-R2 (#77, graph engine), SM-G* (temporal graph tier that builds the KG)
- **Feeds:** SM-H4 (HippoRAG integration), the Symbiotic Memory retrieval path
- **Grounded in:** `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` §2 (HippoRAG / HippoRAG 2)
- **Type:** Spike — the deliverable is the **runnable prototype** in this folder; this doc reports feasibility + integration cost.

## TL;DR / Recommendation

**GO — adopt Optional PPR (HippoRAG) behind a per-tenant flag, default off** (see §Decision).
The prototype ingests a real 2WikiMultiHopQA corpus, builds a KG via LLM OpenIE, runs
Personalized PageRank seeded from query entities, retrieves passages, and answers — end to end,
through the same `MultiHopRetriever` seam as the vector baseline. Headline (k=5, n=30,
285-passage pooled corpus; 95% bootstrap CIs in brackets):

| method | recall@5 | all-hops-hit@5 | EM | F1 | (chaining-only all-hops / F1) |
| --- | --- | --- | --- | --- | --- |
| vector (dense baseline) | 0.750 [.67–.83] | 0.467 [.30–.67] | 0.567 | 0.583 | 0.333 / 0.479 |
| **hippo (HippoRAG PPR)** | **0.908 [.84–.97]** | **0.767 [.60–.90]** | **0.700** | **0.731** | **0.708 / 0.663** |
| hybrid (RRF of both) | 0.892 [.83–.95] | 0.700 [.53–.87] | 0.633 | 0.656 | 0.625 / 0.569 |

`all-hops-hit` (did retrieval surface *every* bridge passage — the prerequisite for answering
a multi-hop question) is the discriminative metric. **Headline:** on genuinely-chaining
questions, dense retrieval finds *both* bridge passages only **33%** of the time; PPR raises
that to **71%**, and lifts recall@5 by +0.16 and QA F1 by +0.15. The recall@5 CIs are
essentially disjoint (beyond noise); the all-hops gaps are large in point estimate but their
CIs partially overlap at n=30 — directionally strong, and a larger set would firm them
(stated honestly, not overclaimed).

## Acceptance criteria (from the issue)

- [x] **Multi-hop need assessed against real use cases (with examples)** — §Need. Established
      third-party set (2WikiMultiHopQA), worked examples, measured baseline gap (dense finds both
      bridge passages on only 33% of chaining questions).
- [x] **PPR go/no-go decided** — §Decision: **GO**, with switch thresholds.
- [x] **If go: a working prototype over the temporal graph + per-tenant flag design** — the
      runnable prototype (this folder) + §Flag design + §Integration route (Neo4j GDS over SM-G).
      *Caveat:* prototype runs over a 2Wiki-derived KG (SM-G's temporal graph is not built yet);
      the KG shape and the PPR→GDS mapping are specified for SM-H4.
- [x] **Composition with bi-temporal/time-aware filtering confirmed** — §Composition (PPR ranks
      over a time-filtered graph projection; the two are orthogonal). Confirmed by design +
      the engine's GDS semantics, not yet exercised on temporal data (SM-G/SM-R10 own that).

## Questions to answer (from the issue)

| Question | Answer |
| --- | --- |
| Do target use cases need multi-hop reasoning that vector similarity + BFS cannot infer? | **Yes, for chaining queries.** The answer often sits on a bridge entity the question never names; dense similarity ranks by surface match to the question and misses it — measured: dense surfaces *both* required passages on only 33% of chaining questions vs 71% for PPR, lifting QA F1 0.48→0.66. Plain BFS (SM-G9) can traverse but is *unranked*; PPR is a *ranked* single-step multi-hop over the same edges. |
| Is the write-side extraction cost justified for our corpora? | **Yes for factual/multi-hop corpora; gate it per tenant.** One OpenIE pass/passage cost **~$0.0022/passage** ($0.63 for 285) and is one-time + async-able, buying +0.16 recall and doubled multi-hop coverage. Not justified for single-hop-dominated or ephemeral corpora — hence flag default-off and only pay extraction when graph memory (SM-G) is enabled. |
| Can PPR be an optional per-tenant flag layered on the SM-G graph (default off)? | Yes — see §Flag design. PPR is a *retrieval mode* over the same KG; no schema change, off by default. |
| Does it compose with time-aware/bi-temporal filtering? | Yes — §Composition. PPR runs over a time-filtered graph projection; the two are orthogonal. |

## The multi-hop need (§Need)

**Why vector-only under-answers.** A genuinely multi-hop question names entity A but the
answer lives on entity C, reached only via a bridge B that the *question never mentions*.
Dense similarity ranks passages by surface similarity to the question, so it reliably finds
A's passage but often misses B's — and without B the reader cannot chain to C. Worked example
from the eval set:

> Q: *"What is the place of birth of Æthelgifu, Abbess of Shaftesbury's mother?"*
> Gold chain: `[Æthelgifu → mother → Ealhswith]`, `[Ealhswith → place of birth → Mercia]`.
> The answer (*Mercia*) is on the **Ealhswith** passage, which is not lexically similar to
> the question. Retrieval must reach it *via* Æthelgifu — exactly the PPR pattern-completion
> step.

**Evidence base (data-quality, §5 of the standard).** Source: `voidful/2WikiMultihopQA`
validation — an established benchmark (Ho et al., COLING 2020); we author none of the
questions, passages, or labels (non-circular). Seeded subset: **30 questions** (12
compositional, 6 inference, 6 bridge_comparison = 24 genuinely-chaining; 6 comparison as a
parallel-fact contrast), pooled into **285 passages** (one shared corpus; ~9.5 distractors/q).
Retrieval recall is scored against the dataset's gold supporting passages; answers against the
gold short answer (SQuAD-style EM/F1). Honest limits: (a) n=30 is small — we report bootstrap
CIs and only rank when the gap clears noise; (b) Wikipedia factual multi-hop is a proxy for a
*factual* memory KG, **not** conversational/episodic memory; (c) cross-reference only: HippoRAG
reports ~+20% multi-hop recall and 10-30x lower cost than iterative retrieval on 2Wiki/MuSiQue
(cited, not re-measured here).

## Options evaluated (from the issue)

| Option | Exercised? | Result / disposition |
| --- | --- | --- |
| **No PPR — graph BFS only (SM-G9)** | Dense baseline measured (dense is the deployed default; BFS is unranked traversal over the same edges) | **Rejected as sole path for multi-hop tenants.** recall@5 0.75, chaining all-hops 0.33 — under-answers multi-hop; unranked BFS gives no principled top-k. Retained as the *default* for non-multi-hop tenants (zero added cost). |
| **Optional PPR (HippoRAG) behind a per-tenant flag** | Yes — full prototype, measured | **WINNER.** recall@5 0.91, chaining all-hops 0.71, F1 0.73; feasible on-stack; ~$0.63 one-time index for 285 passages; maps to Neo4j GDS. Flag-gated, default off. |
| **PPR default (multi-hop dominates)** | Assessed against the type mix | **Rejected.** Our workload is not multi-hop-dominant; default-on imposes the extraction cost on every tenant. Pure PPR also *beat* RRF-hybrid here only because dense was weak — fusion is safer as a general default when dense is competitive. |

## Prototype (what actually runs)

`build eval set -> index -> retrieve -> answer -> score`, all behind `MultiHopRetriever`:

- **`VectorRetriever`** — dense baseline: embed passages (Azure `text-embedding-3-small`),
  cosine top-k. No LLM at index or query time.
- **`HippoRetriever`** — HippoRAG-style: LLM OpenIE per passage → KG (entity nodes + passage
  nodes; membership edges IDF-weighted, relational edges from triples, synonymy edges above a
  cosine threshold) → Personalized PageRank seeded from query entities (linked to KG nodes by
  embedding cosine) → passage nodes ranked by stationary probability.
- **`HybridRetriever`** — RRF fusion (k≈60) of the two (the research doc's default fuse).

LLM = Azure OpenAI **Responses** (`gpt-5.4` deployment; house rule: Responses, not Chat), all
calls through a record/replay cache so the run is reproducible and offline after warm-up. Both
sync and async variants throughout. Reproduce: see §Reproducibility.

**Measured cost / latency** (285-passage pooled corpus): OpenIE index = **285 LLM calls,
107,637 tokens, $0.63** (~$0.0022/passage); KG = **3,393 entities, 7,199 edges**; entity
embedding batched (**43 requests** for 3,393 entities, vs 3,393 sequential — findings/F2).
Index wall-clock **~1058s**, of which OpenIE **~889s** (sequential; trivially parallelizable).
Query latency (retrieval only): vector p95 2.2s, hippo p95 3.8s — but these are dominated by
the **live query-time API calls** (query embedding / NER), not PageRank: the hybrid path, whose
query embeds + NER were already cached, retrieves in **0.056s mean**, showing **PPR + cosine
compute is <100ms**. SM-H4 can drop query latency by replacing the LLM NER with a cheap entity
linker. OpenIE extraction failures: **0/285** (the JSON-salvage from findings/F1 held).

## Seed-context verification & disposition (rebrand check)

The rebrand moves the namespace to `egpt` (not `agno`); paths verified against current code.

| Seed surface (issue) | Current location | Verified | Disposition |
| --- | --- | --- | --- |
| `vectordb/lightrag/lightrag.py` — graph-RAG, **no PPR** | `libs/agno/egpt/vectordb/lightrag/lightrag.py` | Yes — thin HTTP client to an external LightRAG server; modes local/global/hybrid; **no PPR** | **Out-of-scope / alt route.** Not reusable for PPR as-is; would need an external server and still lacks PPR. Not the recommended path. |
| SM-G temporal KG (dependency) | not built yet (SM-G*) | n/a | **Extend.** PPR is a retrieval mode layered on the SM-G KG; the prototype's KG shape (entities + passage links) is what SM-G builds. |
| Neo4j path | `libs/agno/egpt/tools/neo4j.py` (`Neo4jTools`, Cypher runner) | Yes — driver + `run_cypher_query`, not a store | **Extend / integration route.** Production PPR = **Neo4j GDS `gds.pageRank(sourceNodes=…)`** over the SM-G graph; the networkx prototype maps 1:1. |
| Embedder (pgvector / Azure) | `egpt/knowledge/embedder/*` | Yes | **Reuse as-is** for entity/passage vectors + query→node linking. |
| networkx PPR (prototype substrate) | this spike | n/a | **Replace for production** (won't scale to millions of nodes) → Neo4j GDS / scalable PPR. Migration path in §Integration route. |

## Decision (§Decision)

**GO — adopt Optional PPR (HippoRAG) behind a per-tenant flag, default off.** The prototype
demonstrates *measurable* multi-hop value on an established benchmark — recall@5 0.75→0.91
(CIs disjoint), chaining all-hops-hit 0.33→0.71 (more than doubled), QA F1 0.58→0.73 — at a
modest, one-time, async-able write cost ($0.63 / 285 passages) and a query-time PageRank step
that is <100ms. It is feasible on our stack (ran end-to-end through egpt's embedder + Azure
Responses) and maps 1:1 onto Neo4j GDS `gds.pageRank(sourceNodes=…)` over the SM-G KG. PPR is
**additive** to SM-G9 BFS (a ranked multi-hop mode), not a replacement, and is gated per tenant
so non-multi-hop tenants pay nothing. Recommend **pure PPR fused with dense via RRF** as the
"on" configuration, defaulting to hybrid where dense is competitive (pure PPR beat hybrid here
only because dense was weak on this corpus). Honest caveat: n=30 — the recall@5 gain is beyond
noise; the all-hops gains are directionally strong with partially-overlapping CIs, and the
corpus is Wikipedia-factual (a proxy for a factual memory KG, not conversational/episodic).

**Switch thresholds (provisional — SM-R1 ratifies):**
- **Adopt PPR (flag) if** all-hops-hit@5 gain over dense on chaining questions is ≥ **0.15** at
  an index cost ≤ ~1 LLM OpenIE call/passage — **met here (+0.375, 0.333→0.708)**, with the CI
  caveat above.
- **Default PPR off** unless multi-hop chaining questions exceed ~**50%** of a tenant's query
  mix (our mix is not multi-hop-dominant).
- **Re-evaluate** if a cheaper SLM extractor drops OpenIE cost below the threshold, or if a
  tuned SM-G9 BFS closes the ranked-multi-hop gap.

## Flag design (per-tenant, default off)

`MemoryProvider` retrieval config gains `multihop_ppr: {enabled: bool=false, alpha: float=0.5,
seed_top_m: int=3, synonym_tau: float=0.85}`. When off, retrieval is dense+BFS (SM-G9) — zero
added cost, no KG-extraction requirement. When on, the same KG is additionally queried by PPR
and fused (RRF) with dense. The flag is a **retrieval-mode** switch, not a schema change: the
write-side KG extraction is gated by whether *any* tenant needs graph memory (SM-G), not by
this flag. This matches the abstraction standard — call sites see `MultiHopRetriever`, and the
concrete strategy (dense / PPR / hybrid) is config-selected.

## Composition with bi-temporal / time-aware filtering (§Composition)

PPR and bi-temporal filtering are **orthogonal and compose**: bi-temporal validity
(`t_valid`/`t_invalid`, `created_at`/`expired_at`) is an **edge filter**; PPR is a **ranking**
over whatever subgraph survives that filter. Concretely, in Neo4j GDS the integration projects
a graph of edges valid at time T (`WHERE e.t_valid <= T < e.t_invalid`) and runs
`gds.pageRank` with `sourceNodes` on that projection — point-in-time multi-hop retrieval. The
prototype confirms the mechanism (PPR over a passage/entity graph); adding the time predicate
is a projection filter, not a change to the PPR step. Provenance is preserved: seed entities and
the ranked passages carry their source ids.

## Couplings & handoff to SM-H4

- **KG shape:** entity nodes + passage/episode nodes; membership edges (IDF-weighted),
  relational edges (from OpenIE triples), synonymy edges (embedding cosine ≥ τ). SM-G supplies
  entities/relations; SM-H4 adds passage-linkage + PPR.
- **Retrieval contract:** `MultiHopRetriever.retrieve(query, k) -> [Retrieved(pid,title,score)]`,
  fused with dense via RRF (k≈60). Both sync + async.
- **Query-time seeding:** LLM NER on the query → link to KG nodes by embedding cosine (top-m,
  threshold) → personalization vector weighted by cosine × IDF → `gds.pageRank(sourceNodes)`.
- **Write-side robustness (findings/F1, F2):** treat OpenIE as best-effort per passage (JSON
  repair + failure metric); batch entity embeddings. Keep extraction on the async/sleep-time
  lane so the read path stays cheap (research doc §5.7).
- **Provisional targets (SM-R1 ratifies):** the switch thresholds above.

## Out of scope & boundaries

- **Graph engine choice** → SM-R2 (this spike assumes a Neo4j-family GDS-capable engine as the
  production PPR substrate but does not select it).
- **KG construction / temporal model** → SM-G*; **decay/recency** → SM-R5; **bi-temporal
  schema** → SM-R10 (this spike only confirms PPR *composes* with them).
- **Production wiring / core code** → SM-H4. Core (`libs/agno/egpt/**`) is untouched here.

## Consistency with dependencies & siblings

Consistent with the locked neuro-symbolic hybrid design (research doc §5: vector + temporal KG,
optional HippoRAG PPR for multi-hop, no query-time LLM on the hot path — the prototype keeps the
read path LLM-free except the query NER, which SM-H4 can replace with a cheap entity linker).
Depends on SM-R2 (engine) and SM-G (KG); PPR is additive to SM-G9 BFS, not a replacement.
Provisional targets flagged for SM-R1.

## Reproducibility

```bash
python3.11 -m venv .venvs/multihop
.venvs/multihop/bin/python -m pip install -e libs/agno datasets networkx numpy scipy openai python-dotenv
cp benchmarks/memory_multihop/.env.example benchmarks/memory_multihop/.env   # add Azure creds
cd benchmarks/memory_multihop
PYTHONPATH=. ../../.venvs/multihop/bin/python -m harness.dataset --seed 7          # build eval_set.json
PYTHONPATH=. ../../.venvs/multihop/bin/python run.py --methods vector hippo hybrid --k 5 --tag full30
# offline replay (no creds, deterministic) after a warm cache:
HARNESS_LLM_MODE=replay HARNESS_EMBED_MODE=replay PYTHONPATH=. \
    ../../.venvs/multihop/bin/python run.py --methods vector hippo hybrid --k 5
```

Committed artifacts: `data/eval_set.json` (seeded corpus), `results/results_*full30*.json`
(summary + CIs), `results/detail_*full30*.json` (per-question predictions). Grounding:
`docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` §2. Core untouched:
`git diff 25ea759 -- libs/ empty`.
