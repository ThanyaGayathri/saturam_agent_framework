# SM-R9 — Spike: multi-hop need + HippoRAG PPR

- **Status:** Proposed — **GO (provisional)** (spike complete; measured go/no-go below).
  Provisional because the switch thresholds are keyed on a metric whose CIs overlap at
  n=30, and because the production PPR substrate depends on an engine SM-R2 has not
  selected — see §Decision.
- **Issue:** [Saturam-Inc/aicoe-tracker#84](https://github.com/Saturam-Inc/aicoe-tracker/issues/84)
- **Depends on:** SM-R2 (#77, graph engine) — **⚠️ NOT YET DECIDED** (issue OPEN, 0/5 ACs,
  no output in-repo); SM-G* (temporal graph tier that builds the KG)
- **Feeds:** SM-H4 (HippoRAG integration), the Symbiotic Memory retrieval path
- **Grounded in:** `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` §2 (HippoRAG / HippoRAG 2)
- **Type:** Spike — the deliverable is the **runnable prototype** in this folder; this doc reports feasibility + integration cost.

## TL;DR / Recommendation

**GO (provisional) — adopt Optional PPR (HippoRAG) behind a per-tenant flag, default off**
(see §Decision). The prototype ingests a real 2WikiMultiHopQA corpus, builds a KG via LLM
OpenIE, runs Personalized PageRank seeded from query entities, retrieves passages, and
answers — end to end, through the same `MultiHopRetriever` seam as the vector baseline.

**The recommended "on" configuration is `hybrid` (dense + PPR fused), not pure PPR** — so
the headline below leads with the config that would actually ship. Pure PPR scores higher
on this corpus, but fusion is the safer default wherever dense is competitive, and this
corpus's dense leg is not a general case. Headline (k=5, n=30, 285-passage pooled corpus;
95% bootstrap CIs in brackets):

| method | recall@5 | all-hops-hit@5 | EM | F1 | (chaining-only all-hops / F1) |
| --- | --- | --- | --- | --- | --- |
| vector (dense baseline) | 0.750 [.67–.83] | 0.467 [.30–.67] | 0.567 | 0.583 | 0.333 / 0.479 |
| **hybrid (RRF of both) — recommended config** | **0.892 [.83–.95]** | **0.700 [.53–.87]** | **0.633** | **0.656** | **0.625 / 0.569** |
| hippo (pure PPR) — best on this corpus | 0.908 [.84–.97] | 0.767 [.60–.90] | 0.700 | 0.731 | 0.708 / 0.663 |

`all-hops-hit` (did retrieval surface *every* bridge passage — the prerequisite for answering
a multi-hop question) is the discriminative metric. **Headline:** on genuinely-chaining
questions, dense retrieval finds *both* bridge passages only **33%** of the time; the
recommended hybrid config raises that to **63%** (pure PPR: 71%), and lifts recall@5 by
+0.14 (pure PPR: +0.16) and QA F1 by +0.09 (pure PPR: +0.15).

**Evidence strength — read before using these numbers as thresholds.** The recall@5 CIs are
essentially disjoint (beyond noise). The `all-hops-hit` gaps are large in point estimate but
their CIs **overlap** at n=30 (vector .30–.67 vs hippo .60–.90; chaining-only .167–.542 vs
.50–.875). Since the adopt threshold in §Decision is *keyed on all-hops*, it is defined on a
statistic this eval cannot yet separate — hence **GO is provisional and the thresholds are
not ratifiable at n=30**. See §Decision for what is required first.

> **Caveat on the fusion method.** `hybrid` here is **RRF**, which is a prototype default,
> *not* a ratified fusion method — SM-R8 ratified *weighted* fusion, and its 3-leg
> experiment with a real PPR leg found RRF **loses** to weighted. That is also the likeliest
> explanation for hybrid < pure PPR above. See §Consistency with dependencies & siblings.

## Acceptance criteria (from the issue)

- [x] **Multi-hop need assessed against real use cases (with examples)** — §Need. Established
      third-party set (2WikiMultiHopQA), worked examples, measured baseline gap (dense finds both
      bridge passages on only 33% of chaining questions).
- [x] **PPR go/no-go decided** — §Decision: **GO**, with switch thresholds.
- [x] **If go: a working prototype over the temporal graph + per-tenant flag design** — the
      runnable prototype (this folder) + §Flag design + §Integration route (Neo4j GDS over SM-G).
      *Caveat:* prototype runs over a 2Wiki-derived KG (SM-G's temporal graph is not built yet);
      the KG shape and the PPR→GDS mapping are specified for SM-H4.
- [~] **Composition with bi-temporal/time-aware filtering — MECHANICALLY composes; ranking
      quality UNTESTED and currently UNOWNED.** §Composition shows the mechanism (PPR ranks
      over a time-filtered graph projection; filter and ranking are orthogonal steps), and
      that much is sound. But it is confirmed *by design only* — never exercised on temporal
      data. PPR's stationary distribution is topology-sensitive, and a bi-temporal projection
      that excludes superseded/invalidated edges is a **sparser, differently-shaped graph**,
      so ranking quality does not automatically transfer from this static-corpus result. It
      could degrade worst exactly where edge churn is highest — our episodic memory.
      **Ownership gap:** this doc previously handed the untested part to SM-R10, but SM-R10
      §11 explicitly puts ranking *out of its scope* ("This spec only adds the valid-time
      filter columns, not ranking"), and SM-R10 mentions PPR nowhere. Nobody currently owns
      measuring PPR quality over a time-filtered projection — **assigning it to SM-H4 (with
      SM-G supplying the temporal graph) is a decision this spike cannot make alone.**

## Questions to answer (from the issue)

| Question | Answer |
| --- | --- |
| Do target use cases need multi-hop reasoning that vector similarity + BFS cannot infer? | **Yes, for chaining queries.** The answer often sits on a bridge entity the question never names; dense similarity ranks by surface match to the question and misses it — measured: dense surfaces *both* required passages on only 33% of chaining questions vs 71% for PPR, lifting QA F1 0.48→0.66. Plain BFS (SM-G9) can traverse but is *unranked*; PPR is a *ranked* single-step multi-hop over the same edges. |
| Is the write-side extraction cost justified for our corpora? | **Probably, for factual/multi-hop corpora; gate it per tenant — but the at-scale number is the one that decides it, and it is not $0.63.** One OpenIE pass/passage costs **~$0.0022/passage**, one-time + async-able, buying +0.14 recall (hybrid) and roughly doubled multi-hop coverage. **At our stated scale that is ~$220/tenant at 100k memories and ~$2,200/tenant at 1M, one-time, × N tenants, and it grows as memory grows** — see §Cost at scale, which is the figure SM-G should size against, not the $0.63 prototype bill. Not justified for single-hop-dominated or ephemeral corpora — hence flag default-off and only pay extraction when graph memory (SM-G) is enabled. |
| Can PPR be an optional per-tenant flag layered on the SM-G graph (default off)? | Yes — see §Flag design. PPR is a *retrieval mode* over the same KG; no schema change, off by default. |
| Does it compose with time-aware/bi-temporal filtering? | **Mechanically yes; quality untested.** §Composition — PPR runs over a time-filtered graph projection, and filter/ranking are orthogonal *steps*. But the projection is a different graph topology, and PPR is topology-sensitive, so retrieval *quality* under time filtering is unmeasured. Needs an owner (see AC #4). |

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
| **Optional PPR (HippoRAG) behind a per-tenant flag** | Yes — full prototype, measured | **WINNER (provisional).** Recommended config `hybrid`: recall@5 0.89, chaining all-hops 0.63, F1 0.66 (pure PPR: 0.91 / 0.71 / 0.73). Feasible on-stack; ~$0.0022/passage one-time index (**~$2.2k/tenant at 1M memories** — §Cost at scale); maps to Neo4j GDS **only if SM-R2 picks a GDS-capable engine** (§Integration route). Flag-gated, default off. |
| **PPR default (multi-hop dominates)** | Assessed against the type mix | **Rejected.** Our workload is not multi-hop-dominant; default-on imposes the extraction cost on every tenant. |
| **Weighted fusion of dense + PPR** | **No — not measured** | **Open gap.** SM-R8's ratified method and its 3-leg PPR experiment both favour weighted over RRF; this spike only ran RRF, so the `hybrid` number is a lower bound on fusion. SM-H4 must measure. See §Consistency. |

## Prototype (what actually runs)

`build eval set -> index -> retrieve -> answer -> score`, all behind `MultiHopRetriever`:

- **`VectorRetriever`** — dense baseline: embed passages, cosine top-k. No LLM at index or
  query time. **Embedder = SM-R4's ratified choice** (`bge-base-en-v1.5`@768 Mode A /
  `text-embedding-3-large`@1024 Mode B). The committed `full30` numbers above were produced
  with `text-embedding-3-small` — neither ratified mode — because this branch forked before
  SM-R4 merged; see §Embedder correction for the re-measurement.
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
linker.

**KG quality is UNMEASURED — do not read `0/285` as a quality number.** OpenIE extraction
failures were **0/285**, but that is **JSON-parse success** (the salvage path from findings/F1
held), *not* triple precision or recall. Since PPR's entire advantage is a function of the KG
substrate, this is the load-bearing unmeasured quantity in the spike. Two consequences:

- A scorer now exists — `harness/kg_quality.py` + `kg_quality_run.py` — measuring gold-chain
  recall against 2Wiki's gold `evidences` (72 gold triples over the 30 questions) at three
  levels: **entity** (both endpoints in the KG), **edge** (endpoints directly connected —
  *the level PPR actually consumes*, since `kg.py` discards relation labels), and **triple**
  (relation-strict). Plus a **grounding rate** as a faithfulness proxy. Precision against
  `evidences` is deliberately **not** reported: 2Wiki's gold lists only the chain triples, so
  scoring the rest as false positives would measure gold sparsity, not extraction error.
  The scorer is verified by a credential-free `--self-test`; **real numbers are outstanding**
  (needs an OpenIE pass — no LLM credentials on the re-run host, and `results/llm_cache/` is
  git-ignored so the original extraction could not be replayed).
- Even once measured on this corpus, it is **Wikipedia prose**. Real episodic memory is short,
  elliptical and coreference-heavy, and should be expected to score materially **worse** —
  compare SM-R6's finding that real dialogue is much harder than clean text. Sampling real
  episodic memory is a prerequisite before SM-H4 commits to the KG substrate.

## Embedder correction (§Embedder correction)

**The bug.** This branch forked before SM-R4 (embedding model decision) merged, so the
harness defaulted to Azure `text-embedding-3-small` — **neither** of SM-R4's ratified picks
(`bge-base-en-v1.5`@768 Mode A / `text-embedding-3-large`@1024 Mode B). The dense leg is the
baseline PPR's whole margin is measured against, so a non-shipping embedder there makes the
reported gain a margin over a config we will not deploy. Same root cause and same fix shape
as SM-R8 (`e2cd21d`). Fixed in `harness/embeddings.py`: `build_embedder` now resolves
SM-R4's ratified spec, a credential-free Mode A path was added (there was none), and the
embed-cache key now derives from the resolved model instead of the constant
`"azure-embedding"` — **that constant was a latent trap: swapping embedders would have
silently replayed the previous model's vectors.**

**Re-measured (`results/summary_smR4ModeA.md`, `results_20260728T104020Z_smR4ModeA.json`):**

| metric | old `text-embedding-3-small` | new `bge-base-en-v1.5`@768 (SM-R4 Mode A) | Δ |
| --- | --- | --- | --- |
| recall@5 (overall) | 0.750 [.667, .833] | 0.742 [.650, .833] | −0.008 |
| all-hops-hit@5 (overall) | 0.467 [.300, .667] | 0.467 [.300, .633] | **0.000** |
| recall@5 (chaining-only) | 0.688 [.604, .781] | 0.677 [.594, .771] | −0.011 |
| all-hops-hit@5 (chaining-only) | 0.333 [.167, .542] | 0.333 [.167, .542] | **0.000** |

**The review's hypothesis did not hold for the dense leg.** The concern was that a stronger
embedder would lift dense most on non-chaining questions, compressing the PPR margin and
possibly flipping pure-PPR-vs-hybrid. Both `all-hops-hit` figures are **identical**, and
recall@5 moves −0.008 (well inside the CI). This is consistent with SM-R4's own eval, where
the two models are a statistical tie on the memory set (bge-base 0.911 vs 3-small 0.898; vs
3-large 0.900, Holm p=0.21 n.s.) — SM-R4 states plainly that "Mode B is now OPTIONAL, not a
quality upgrade." The ratified pick is a deployment/licensing choice, not a quality one.

Two checks so this conclusion is trustworthy rather than convenient:
- **BGE query instruction.** `bge-*-v1.5` normally prefixes queries with *"Represent this
  sentence for searching relevant passages: "*; omitting it could have handicapped dense.
  Measured both ways — the prefix makes it **worse** (recall@5 0.725, all-hops 0.433), so the
  reported numbers are the *favourable* configuration for the dense baseline.
- **Spurious BLAS warning.** macOS Accelerate raises divide/overflow/invalid RuntimeWarnings
  from the cosine matmul. Verified benign before trusting any number — all inputs/outputs
  finite, row norms exactly 1.0, BLAS matches an independent `np.einsum` path to **1.8e-15**.
  Now suppressed narrowly with a hard `isfinite` assert behind it.

**What this does NOT settle.** Only the dense leg was re-run. `hippo`/`hybrid` still carry
their original `text-embedding-3-small` numbers, because rebuilding the KG needs 285 live
OpenIE calls and there are no LLM credentials on the re-run host (`results/llm_cache/` is
git-ignored, so the original extraction cannot be replayed). **The comparison above is
therefore asymmetric and the PPR margin is not yet fully re-validated** — the same embedder
also produces PPR's entity vectors, so its arm would move too, in an undetermined direction.
Two reasons to expect the PPR arm to move *more* than dense did, which is why the full re-run
is a prerequisite and not a formality:

1. `synonym_tau=0.85` and `link_threshold=0.5` are **absolute cosine thresholds tuned on
   `text-embedding-3-small`** and are not portable. Measured under `bge-base`@768 on this
   corpus (285 passage titles as a proxy entity-name set, 40,470 pairs): median off-diagonal
   cosine 0.379, p95 0.483, p99 0.553. At τ=0.85 only **12 pairs (0.03%)** qualify — a
   threshold that far into the tail is highly sensitive to the embedder's cosine scale, so
   the synonymy-edge set, and hence the topology PPR ranks over, changes with the embedder.
   **SM-H4 must re-tune both values per embedder, not inherit them.**
2. Dimension changes 1536 → 768 (Mode A) or → 1024 (Mode B), shifting the entity-linking
   neighbourhood.

## Seed-context verification & disposition (rebrand check)

The rebrand moves the namespace to `egpt` (not `agno`); paths verified against current code.

| Seed surface (issue) | Current location | Verified | Disposition |
| --- | --- | --- | --- |
| `vectordb/lightrag/lightrag.py` — graph-RAG, **no PPR** | `libs/agno/egpt/vectordb/lightrag/lightrag.py` | Yes — thin HTTP client to an external LightRAG server; modes local/global/hybrid; **no PPR** | **Out-of-scope / alt route.** Not reusable for PPR as-is; would need an external server and still lacks PPR. Not the recommended path. |
| SM-G temporal KG (dependency) | not built yet (SM-G*) | n/a | **Extend.** PPR is a retrieval mode layered on the SM-G KG; the prototype's KG shape (entities + passage links) is what SM-G builds. |
| Neo4j path | `libs/agno/egpt/tools/neo4j.py` (`Neo4jTools`, Cypher runner) | Yes — driver + `run_cypher_query`, not a store | **Extend / integration route — conditional.** *If* SM-R2 selects a GDS-capable engine, production PPR = `gds.pageRank(sourceNodes=…)` and the networkx prototype maps 1:1. SM-R2 is still open and has non-GDS candidates; §Integration route carries Route B. |
| Embedder (pgvector / Azure) | `egpt/knowledge/embedder/*` | Yes | **Reuse — pinned to SM-R4's ratified pick** (`bge-base-en-v1.5`@768 Mode A / `text-embedding-3-large`@1024 Mode B), for entity/passage vectors + query→node linking. See §Embedder correction. |
| networkx PPR (prototype substrate) | this spike | n/a | **Replace for production** (won't scale to millions of nodes) → Neo4j GDS / scalable PPR. Migration path in §Integration route. |

## Decision (§Decision)

**GO (provisional) — adopt Optional PPR (HippoRAG) behind a per-tenant flag, default off.**
The prototype demonstrates *measurable* multi-hop value on an established benchmark — recall@5
0.75→0.89 hybrid / 0.91 pure PPR (CIs disjoint), chaining all-hops-hit 0.33→0.63 hybrid / 0.71
pure PPR, QA F1 0.58→0.66 hybrid / 0.73 pure PPR — at a modest, one-time, async-able write cost
and a query-time PageRank step that is <100ms. PPR is **additive** to SM-G9 BFS (a ranked
multi-hop mode), not a replacement, and is gated per tenant so non-multi-hop tenants pay
nothing. Recommend **hybrid (dense + PPR)** as the "on" configuration — fusion is the safer
default wherever dense is competitive.

**Why "provisional", specifically.** Three things must land before this is ratifiable:

1. **The adopt threshold is keyed on a statistic this eval cannot separate.** At n=30 the
   `all-hops-hit` CIs overlap (chaining-only: dense .167–.542 vs PPR .50–.875). Only recall@5
   is beyond noise. **Any switch threshold keyed on all-hops needs n≥100, or a power analysis
   establishing the n required to detect the threshold effect size, before SM-R1 ratifies it.**
2. **The production PPR substrate assumes an engine that has not been chosen.** See below.
3. **KG quality — the substrate PPR depends on — is unmeasured**, and the dense-vs-PPR
   comparison is currently asymmetric on the embedder (§Embedder correction, §Prototype).

**⚠️ Engine dependency (SM-R2 / #77) is unresolved and this is load-bearing.** Earlier drafts
of this doc stated the prototype "maps 1:1 onto Neo4j GDS `gds.pageRank(sourceNodes=…)`" as
settled fact. It is not: **issue #77 is OPEN with 0/5 acceptance criteria and no output in the
repo**, and its own candidate list includes **FalkorDB, Kuzu, `lightrag` and Cognee — none of
which provide `gds.pageRank(sourceNodes=…)`**. The honest statement is: *PPR maps 1:1 onto
Neo4j GDS **if** SM-R2 selects a GDS-capable engine.* If SM-R2 lands elsewhere, the integration
route must be rebuilt around a hand-rolled sparse power-iteration over an exported adjacency
(i.e. this prototype's networkx approach promoted to a service) — feasible, but a different
cost and latency story than a native procedure call. §Integration route carries that fallback.
Compare SM-R10, which kept itself deliberately engine-agnostic and proved its spec on two
backends; this spike did not, and should be read accordingly.

**Output back to SM-R2:** multi-hop tenants require *personalized* PageRank with source-node
seeding. That is a concrete capability requirement this spike is well-placed to contribute as
an **input to the engine decision**, and SM-R2 should weigh it.

Honest caveat retained: the corpus is Wikipedia-factual — a proxy for a factual memory KG, not
conversational/episodic memory.

**Switch thresholds (PROVISIONAL — NOT ratifiable at n=30; SM-R1 ratifies):**
- **Adopt PPR (flag) if** all-hops-hit@5 gain over dense on chaining questions is ≥ **0.15** at
  an index cost ≤ ~1 LLM OpenIE call/passage — point estimate **met here (+0.375, 0.333→0.708
  pure PPR; +0.292, 0.333→0.625 hybrid)**, **but the CIs for this metric overlap at n=30, so
  the criterion is checked against a point estimate the eval cannot resolve. Re-measure at
  n≥100 (or run a power analysis) before ratifying.**
- **Default PPR off** unless multi-hop chaining questions exceed ~**50%** of a tenant's query
  mix (our mix is not multi-hop-dominant).
- **Re-evaluate** if a cheaper SLM extractor drops OpenIE cost below the threshold, or if a
  tuned SM-G9 BFS closes the ranked-multi-hop gap. **SLM status: unvalidated — do not assume
  it is cheap.** SM-R6 §6 keeps the Mode-A on-prem SLM path explicitly open, but its only
  measurement was a *failure* (boundary F1 **0.335±0.08** vs **0.877±0.07** for the LLM, on
  llama3-8B/CPU/whole-conversation) and it defers a GPU/windowed re-test to SM-R1. SM-R7 §10
  *proposes* an SLM extractor for fact extraction but never measures one — and its
  `AZURE_OPENAI_EXTRACTOR_DEPLOYMENT` hook is a cheaper *Azure* deployment (Mode B), not an
  on-prem Mode-A SLM. Neither sibling has evaluated an SLM on **OpenIE triple extraction**,
  which is a third task again.

## Cost at scale (§Cost at scale)

The prototype's **$0.63** is a 285-passage bill and should not be read as the deployment cost.
At ~$0.0022/passage-equivalent and the project's stated scale:

| memories / tenant | one-time OpenIE cost / tenant | × 10 tenants | × 100 tenants |
| --- | --- | --- | --- |
| 100k | ~$220 | ~$2.2k | ~$22k |
| 1M | ~$2,200 | ~$22k | ~$220k |

Three things this table understates, all of which SM-G should size properly:
- **It is not truly one-time.** Cost grows with memory, so it is a *recurring marginal* cost on
  new memories plus a one-time backfill.
- **Re-extraction risk.** Changing the OpenIE prompt or model re-incurs the full backfill.
- **The mitigation is unbuilt.** The "cheaper SLM extractor" lever is unvalidated (above), so
  the economics should be planned at frontier-model prices until an SLM is actually measured
  on OpenIE.

Per-tenant gating (flag default-off, extraction only when SM-G graph memory is enabled) is what
keeps this bounded — the flag economics are the decision, not the prototype bill.

## Integration route (§Integration route)

The prototype's networkx PPR is a **prototype substrate only** — it will not scale to millions
of nodes and must be replaced for production. Which replacement depends on an engine decision
that has not been made (SM-R2 / #77 is OPEN), so both routes are specified:

| | **Route A — GDS-capable engine** (Neo4j family) | **Route B — non-GDS engine** (FalkorDB, Kuzu, `lightrag`, Cognee) |
| --- | --- | --- |
| PPR step | native `gds.pageRank(sourceNodes=…)` over a projected graph | sparse power-iteration over an exported adjacency — this prototype's `kg.py` promoted to a service |
| Mapping cost | 1:1 with the prototype | new service: projection export, iteration loop, cache/invalidations |
| Time filter | projection predicate (see §Composition) | must be applied at export time |
| Risk | low | **moderate — unbudgeted; adds a component SM-H4 does not currently plan for** |

**Route B is the live risk**: three of SM-R2's five candidates are not GDS-capable. SM-H4 should
not begin the PPR integration until SM-R2 closes, or should scope Route B explicitly. Either
way, the retrieval contract in §Couplings is unchanged — `MultiHopRetriever` hides which route
is in use, which is the point of the seam.

## Flag design (per-tenant, default off)

`MemoryProvider` retrieval config gains `multihop_ppr: {enabled: bool=false, alpha: float=0.5,
seed_top_m: int=3, synonym_tau: float=0.85}`. When off, retrieval is dense+BFS (SM-G9) — zero
added cost, no KG-extraction requirement. When on, the same KG is additionally queried by PPR
and fused (RRF) with dense. The flag is a **retrieval-mode** switch, not a schema change: the
write-side KG extraction is gated by whether *any* tenant needs graph memory (SM-G), not by
this flag. This matches the abstraction standard — call sites see `MultiHopRetriever`, and the
concrete strategy (dense / PPR / hybrid) is config-selected.

## Composition with bi-temporal / time-aware filtering (§Composition)

PPR and bi-temporal filtering compose **mechanically**: bi-temporal validity
(`t_valid`/`t_invalid`, `created_at`/`expired_at`) is an **edge filter**; PPR is a **ranking**
over whatever subgraph survives that filter. Adding the time predicate is a projection filter,
not a change to the PPR step. Provenance is preserved: seed entities and the ranked passages
carry their source ids.

**Use SM-R10's ratified predicate, not the simplified one.** An earlier draft of this doc wrote
the projection as `WHERE e.t_valid <= T < e.t_invalid`, which is **wrong on two axes** against
SM-R10 §7 (5a). The ratified form is:

```cypher
WHERE e.t_valid <= datetime($T)
  AND datetime($T) < coalesce(e.t_invalid, datetime('9999-12-31T00:00:00Z'))
  AND e.expired_at IS NULL
```

- Without `coalesce(e.t_invalid, ∞)`, every **open-ended edge** (`t_invalid IS NULL` — i.e.
  every currently-true fact) silently drops out of the projection.
- Without `e.expired_at IS NULL`, the projection includes **retracted and superseded rows**.
  SM-R10 §6 mandates expire-and-reinsert (SCD-2), so superseded versions are *kept*; omitting
  the transaction-axis filter inflates PPR mass on stale facts.

Both errors change graph **topology**, which is precisely what PPR ranks over — so they are
correctness bugs in the composition story, not cosmetic ones.

**Quality remains untested and unowned.** The mechanism is sound; the *ranking quality* over a
time-filtered projection is not measured. A bi-temporal projection is sparser and
differently-shaped than this static corpus, PPR's stationary distribution is topology-sensitive,
and degradation would be worst where edge churn is highest — our episodic memory. This doc
previously assigned that measurement to SM-R10, but **SM-R10 §11 explicitly excludes ranking
from its scope** ("This spec only adds the valid-time filter columns, not ranking") and does not
mention PPR anywhere. **Recommended owner: SM-H4, with SM-G supplying the temporal graph** — but
that reassignment needs confirming, since it is currently nobody's.

## Couplings & handoff to SM-H4

- **KG shape:** entity nodes + passage/episode nodes; membership edges (IDF-weighted),
  relational edges (from OpenIE triples), synonymy edges (embedding cosine ≥ τ). SM-G supplies
  entities/relations; SM-H4 adds passage-linkage + PPR.
- **Retrieval contract:** `MultiHopRetriever.retrieve(query, k) -> [Retrieved(pid,title,score)]`,
  fused with dense. Both sync + async. **Fusion method is an open decision, not RRF-by-default**
  — the prototype uses RRF (k≈60) but SM-R8's evidence favours weighted; measure both (§Consistency).
- **Query-time seeding:** LLM NER on the query → link to KG nodes by embedding cosine (top-m,
  threshold) → personalization vector weighted by cosine × IDF → PPR with source-node seeding
  (`gds.pageRank(sourceNodes)` on a GDS engine; see §Integration route for the non-GDS fallback).
  **`synonym_tau` / `link_threshold` are embedder-specific absolute cosines — re-tune, don't
  inherit** (§Embedder correction).
- **Write-side robustness (findings/F1, F2):** treat OpenIE as best-effort per passage (JSON
  repair + failure metric); batch entity embeddings. Keep extraction on the async/sleep-time
  lane so the read path stays cheap (research doc §5.7).
- **Provisional targets (SM-R1 ratifies):** the switch thresholds above.

## Out of scope & boundaries

- **Graph engine choice** → SM-R2 (#77, **still OPEN**). This spike's integration route assumes
  a GDS-capable engine but does **not** select one, and SM-R2's candidate list includes engines
  that are not GDS-capable — §Integration route specifies both routes.
- **KG construction / temporal model** → SM-G*; **decay/recency** → SM-R5; **bi-temporal
  schema** → SM-R10. This spike confirms PPR composes with bi-temporal filtering
  *mechanically only*; **ranking quality under time filtering is untested and unowned**, since
  SM-R10 §11 excludes ranking from its scope (§Composition).
- **Fusion method for dense+PPR** → open. RRF is the prototype default; SM-R8's evidence favours
  weighted; neither is ratified for this leg pair (§Consistency).
- **Production wiring / core code** → SM-H4. Core (`libs/agno/egpt/**`) is untouched here.

## Consistency with dependencies & siblings

Consistent with the locked neuro-symbolic hybrid design (research doc §5: vector + temporal KG,
optional HippoRAG PPR for multi-hop, no query-time LLM on the hot path — the prototype keeps the
read path LLM-free except the query NER, which SM-H4 can replace with a cheap entity linker).
PPR is additive to SM-G9 BFS, not a replacement. Provisional targets flagged for SM-R1.

Where this spike touches a sibling's ratified decision, stated explicitly rather than left
implicit:

| Sibling | Status | Detail |
| --- | --- | --- |
| **SM-R4** (embedder) | **was inconsistent — now fixed** | Harness defaulted to `text-embedding-3-small`, neither ratified mode. Fixed + re-measured: §Embedder correction. |
| **SM-R8** (fusion) | **not governed, but our RRF choice points against the only evidence** | See below. |
| **SM-R10** (bi-temporal) | **gap — orphaned open item + predicate corrected** | §Composition. SM-R10 excludes ranking from scope, so PPR-over-time-filtered-projection is unowned. |
| **SM-R2** (engine) | **⚠️ unresolved dependency** | §Decision, §Integration route. Issue OPEN, 0/5 ACs; GDS assumption is conditional. |
| **SM-R6 / SM-R7** (SLM path) | **claim was overstated — corrected** | §Decision switch thresholds. Accurate for SM-R6; SM-R7 proposes but never flags or measures an SLM extractor. |

**On fusion (SM-R8).** SM-R8 ratified **weighted / relative-score fusion**, not RRF, for the
2-leg dense+BM25 case, and explicitly says *"do not adopt RRF automatically when SM-G9's graph
leg joins."* Strictly, **SM-R8 does not govern this spike**: its scope covers dense+BM25 and it
lists graph/PPR retrieval as out of scope (SM-G9), whereas `HybridRetriever` fuses dense+PPR
with no BM25 leg. So SM-R9's RRF is not a formal contradiction.

It is, however, **pointing against the only direct evidence we have**, and that is worth being
blunt about rather than resting on the scope technicality:

- SM-R8's §3.5 three-leg experiment added a **real Personalized-PageRank leg** — i.e. exactly
  this shape — and found **weighted beat RRF by +0.076 (p=0.0001)**, *wider* than the 2-leg gap
  (+0.023), surviving an adversarial decorrelation control (+0.060, p=0.0001). Three-leg RRF fell
  **below** even 2-leg RRF, because RRF's rank-only equal vote lets a noisier graph leg drag the
  fused ranking down, while weighted's per-leg normalized sum absorbs it.
- **This spike's own result replicates that failure mode.** hybrid-RRF (0.892) < pure PPR (0.908).
  Earlier drafts attributed this to a weak dense leg — but §Embedder correction shows the dense
  leg is *not* the explanation (correcting the embedder left all-hops unchanged). SM-R8 §3.5's
  fusion-math explanation now looks like the better one.
- The citation this spike originally leaned on — "RRF, the research doc's default fuse" — was
  **downgraded by SM-R8 §9**: *"The research's 3-leg RRF recommendation is not yet confirmed."*

**Consequence:** RRF here is a prototype default, **not a ratified fusion method**, and the
`hybrid` row in the headline table should be read as a *lower bound* on what fusion can do.
**SM-H4 must measure weighted-vs-RRF for dense+PPR before either is treated as settled**; SM-R8
§6 already names the artifacts to reuse (`fusion.RRFFusion(k=60)` / `fusion.WeightedFusion(...)`,
both N-leg-capable). Weighted fusion was **not** measured in this spike — that is a real gap in
the evidence, not an oversight of scope.

(Related, for SM-R8's own tracking: SM-R8 §9 flags `memory_provider_spec/adapters.py`'s
`co_located` adapter for hardcoding RRF on vector+graph fusion. `HybridRetriever` is the same
pattern; SM-R8 flagged this spike only on the embedder.)

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

**Credential-free subset** (SM-R4 Mode A is a local sentence-transformers model, and retrieval
metrics score against the dataset's gold titles, so no LLM is needed for the dense leg):

```bash
PYTHONPATH=. python run.py --methods vector --k 5 --retrieval-only --tag smR4ModeA
PYTHONPATH=. python kg_quality_run.py --self-test    # verifies the KG-quality scorer
```

Committed artifacts: `data/eval_set.json` (seeded corpus), `results/results_*full30*.json`
(summary + CIs), `results/detail_*full30*.json` (per-question predictions + retrieved titles),
`results/results_*smR4ModeA*.json` + `results/summary_smR4ModeA.md` (the SM-R4 embedder
re-measurement). Grounding: `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` §2. Core
untouched: `git diff origin/main -- libs/` is empty.

**Reproducibility gap found while addressing review.** The original run recorded neither the
embedder used nor the per-question retrieved titles, which is *how a non-SM-R4 embedder went
unnoticed to review* and why the `hybrid` leg could not be re-fused offline from committed
artifacts. Both are now recorded (`summary.embedder`, `per_item.retrieved_titles`). The LLM and
embedding caches remain git-ignored (they are large), so a re-run on a fresh host still needs
credentials for the `hippo`/`hybrid` legs.
