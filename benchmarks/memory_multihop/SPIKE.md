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

**The recommended "on" configuration is `hybrid` (dense + PPR fused), not pure PPR** — and
on SM-R4's ratified embedder that is now a *measured* result, not a cautious preference.
Headline (k=5, n=30, 285-passage pooled corpus, **SM-R4 Mode A `bge-base-en-v1.5`@768**;
95% bootstrap CIs in brackets):

| method | recall@5 | all-hops-hit@5 | EM | F1 | (chaining-only all-hops / F1) |
| --- | --- | --- | --- | --- | --- |
| vector (dense baseline) | 0.742 [.65–.83] | 0.467 [.30–.63] | 0.467 | 0.489 | 0.333 / 0.361 |
| **hybrid (RRF of both) — recommended config** | **0.883** | **0.700** | **0.667** | **0.719** | **0.625 / 0.708** |
| hippo (pure PPR) | 0.817 | 0.633 | 0.500 | 0.574 | 0.625 / 0.509 |

`all-hops-hit` (did retrieval surface *every* bridge passage — the prerequisite for answering
a multi-hop question) is the discriminative metric. **Headline:** on genuinely-chaining
questions, dense retrieval finds *both* bridge passages only **33%** of the time; the
recommended hybrid config raises that to **63%**, lifts recall@5 by +0.14 and QA F1 by +0.23.

> **The pure-PPR-vs-hybrid ranking flipped when the embedder was corrected.** On the original
> (non-ratified) `text-embedding-3-small` run, pure PPR led at 0.908/0.767 and hybrid trailed.
> On SM-R4's embedder, **pure PPR is fragile and hybrid is robust** — see §Embedder correction.
> The original "pure PPR wins" headline was an artifact of one embedder's threshold calibration.

**Evidence strength — read before using these numbers as thresholds.** hybrid-vs-dense
recall@5 CIs are essentially disjoint (.808–.942 vs .650–.833). The `all-hops-hit` gaps are
large in point estimate but their CIs **overlap** at n=30 (vector .300–.633 vs hybrid
.533–.867; chaining-only .167–.542 vs .417–.833). Since the adopt threshold in §Decision is
*keyed on all-hops*, it is defined on a statistic this eval cannot yet separate — hence **GO
is provisional and the thresholds are not ratifiable at n=30**. See §Decision.

> **On the fusion method.** `hybrid` here is **RRF**. SM-R8 ratified *weighted* fusion for its
> own 2-leg case, so weighted has now been measured here too (§Fusion): it shows **no advantage
> on this leg pair** at any weight, and its in-sample optimum degenerates to "don't blend".
> The RRF-vs-weighted difference is *not* significant at n=30, so RRF is retained because
> weighted showed nothing better — not because RRF is demonstrably superior.

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
| Do target use cases need multi-hop reasoning that vector similarity + BFS cannot infer? | **Yes, for chaining queries.** The answer often sits on a bridge entity the question never names; dense similarity ranks by surface match to the question and misses it — measured: dense surfaces *both* required passages on only 33% of chaining questions vs 63% for the recommended hybrid config, lifting chaining QA F1 0.36→0.71. Plain BFS (SM-G9) can traverse but is *unranked*; PPR is a *ranked* single-step multi-hop over the same edges. |
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
| **Optional PPR (HippoRAG) behind a per-tenant flag** | Yes — full prototype, measured on SM-R4's embedder | **WINNER (provisional).** Recommended config `hybrid`: recall@5 0.88, all-hops 0.70, chaining all-hops 0.63, F1 0.72 — beating pure PPR (0.82 / 0.63 / 0.63 / 0.57). Feasible on-stack; ~$0.0022/passage one-time index (**~$2.2k/tenant at 1M memories** — §Cost at scale); maps to Neo4j GDS **only if SM-R2 picks a GDS-capable engine** (§Integration route). Flag-gated, default off. |
| **PPR default (multi-hop dominates)** | Assessed against the type mix | **Rejected.** Our workload is not multi-hop-dominant; default-on imposes the extraction cost on every tenant. |
| **Pure PPR as the "on" config** | Yes — measured | **Rejected on evidence.** Led on the original (non-ratified) embedder but **lost 0.275 recall@5** when moved to SM-R4's, and does not recover fully even re-tuned. Embedder-fragile; hybrid is not (§Embedder correction). |
| **Weighted fusion of dense + PPR** (SM-R8's ratified method) | Yes — measured, swept 0.0–1.0 | **Rejected for this leg pair.** RRF's point estimate is higher at every weight; weighted's in-sample optimum is degenerate (w=0.0 = don't blend); at SM-R8's w=0.5 it is worse than pure PPR. Not significant at n=30 (p=0.26), so "no advantage shown" rather than "RRF wins". §Fusion. |

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

**KG quality — MEASURED, and materially weaker than `0/285` implied.** OpenIE extraction
failures were **0/285**, but that is **JSON-parse success**, not triple precision or recall.
Measured against 2Wiki's gold `evidences` (72 gold triples over the 30 questions) with
`harness/kg_quality.py`, at the granularity PPR actually consumes:

| metric | value | reading |
| --- | --- | --- |
| entity recall | **0.958** | OpenIE almost always finds both endpoints of a gold triple |
| **edge recall** | **0.639** | but only 64% of gold chain edges exist in the KG |
| **full-chain recall** | **0.367** | **only 37% of questions have their whole gold chain walkable** |
| triple recall (relation-strict) | 0.097 | relation phrasing is free-form; near-useless as a metric |
| grounding rate | 0.936 | 6.4% of extracted triples have an endpoint absent from the source text |

Granularity is chosen to match the implementation: `kg.py` builds relational edges with
`_bump_edge(s, o, "relation", 1.0)` — **the relation label is discarded** — and PPR runs over
adjacency and weight only, so a relation-strict score would understate the substrate actually
used. Precision against `evidences` is deliberately **not** reported: 2Wiki's gold lists only
the chain triples, so scoring other correct extractions as false positives would measure gold
sparsity, not extraction error. The **grounding rate** stands in as a faithfulness proxy — it
catches invented entities, not wrong relations between real ones.

**This reframes what PPR is doing here.** For **63% of questions the gold reasoning chain is
not traversable at all**, yet hybrid still reaches 0.700 all-hops. So the measured benefit is
coming largely from entity/passage **membership** structure plus dense fusion — **not** from
walking correct relational edges. SM-H4 should not assume the relational layer is carrying the
result, and should re-measure this before investing in relation quality.

**Episodic memory: measured, and the expectation was only half right** (§Measurements,
`results/summary_measurements.md`). Sampled against real LongMemEval-S dialogue with identical
code: OpenIE does **not** hallucinate more on conversation — grounding is 0.964 vs 0.956 on
Wikipedia, directionally *better*. What does degrade is (a) **fact density**, 7.56 triples per
100 words vs 17.82, i.e. **2.4× more input tokens per extracted triple, so cost per useful
triple is ~2.4× higher than §Cost at scale implies**, and (b) **coreference**, with ~3× more
pronoun/speaker-relative entities (7.7% vs 2.6%) that cannot be linked to a stable KG node and
so are dead weight PPR cannot traverse. The substrate risk for episodic memory is therefore
**density and linkability, not extractor reliability** — a different problem with different
mitigations than this doc previously implied.

## Embedder correction (§Embedder correction)

**The bug.** This branch forked before SM-R4 (embedding model decision) merged, so the
harness defaulted to Azure `text-embedding-3-small` — **neither** of SM-R4's ratified picks
(`bge-base-en-v1.5`@768 Mode A / `text-embedding-3-large`@1024 Mode B). The dense leg is the
baseline PPR's whole margin is measured against. Same root cause and same fix shape as SM-R8
(`e2cd21d`). Fixed in `harness/embeddings.py`: `build_embedder` now resolves SM-R4's spec, a
credential-free Mode A path was added (there was none), and the embed-cache key derives from
the resolved model instead of the constant `"azure-embedding"` — **that constant was a latent
trap: swapping embedders would have silently replayed the previous model's vectors.**

All three methods were re-run end to end on Mode A. Full write-up:
`results/summary_smR4ModeA.md`.

### The dense leg was *not* the problem — pure PPR was

| config / method | recall@5 | all-hops@5 | chaining all-hops |
| --- | --- | --- | --- |
| **original** — 3-small, link=0.5 | | | |
| vector | 0.750 | 0.467 | 0.333 |
| hippo | **0.908** | **0.767** | **0.708** |
| hybrid | 0.892 | 0.700 | 0.625 |
| **SM-R4 bge-base, link=0.5** (thresholds inherited) | | | |
| vector | 0.742 | 0.467 | 0.333 |
| hippo | **0.633** | **0.467** | 0.417 |
| hybrid | 0.817 | 0.600 | 0.500 |
| **SM-R4 bge-base, link=0.65** (re-tuned) | | | |
| vector | 0.742 | 0.467 | 0.333 |
| hippo | 0.817 | 0.633 | 0.625 |
| **hybrid** | **0.883** | **0.700** | 0.625 |

**The dense baseline is flat** (0.750 → 0.742; all-hops *identical* at 0.467/0.333). That
retires the review's stated concern — the PPR margin was not resting on a strawman dense leg.
It is consistent with SM-R4's own eval, where the models are a statistical tie on the memory
set (bge-base 0.911 vs 3-small 0.898; vs 3-large 0.900, Holm p=0.21 n.s.); SM-R4 says outright
that "Mode B is now OPTIONAL, not a quality upgrade."

**But the review's predicted *outcome* — that the pure-PPR-vs-hybrid result would flip — is
confirmed, via the opposite mechanism.** Dense did not get stronger; **pure PPR got weaker**,
losing 0.275 recall@5 at inherited thresholds. Hybrid is barely affected. So:

- **Pure PPR is embedder-fragile.** Its headline was an artifact of one embedder's calibration.
- **Hybrid is robust** — 0.892 (original) / 0.817 (untuned) / 0.883 (tuned), all-hops 0.700 in
  both the original and tuned runs. Shipping hybrid is now a measured recommendation.

### Root cause: `link_threshold` is embedder-specific; 0.5 is badly miscalibrated

The query-entity → KG-node cutoff is an **absolute cosine**. Entity–entity cosine distribution
under bge-base over the real KG (3,413 entities, 5.8M pairs): **p50 0.4115**, p90 0.4944,
p99 0.5878, p99.9 0.6984. A 0.5 cutoff therefore sits near the **median of the random-pair
distribution** — ~5% of all pairs falsely qualify as links, so PPR is seeded on spurious
entities and diffuses its mass. The sweep (`tau_sweep.py`) is monotone through that region:

| link_threshold | 0.30 | 0.40 | 0.50 | 0.60 | **0.65** | 0.70 | 0.80 | 0.90 | 0.95 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| recall@5 | .633 | .633 | .633 | .733 | **.817** | .808 | .792 | .783 | .750 |
| all-hops@5 | .467 | .467 | .467 | .533 | .633 | **.667** | .633 | .667 | .633 |

**`synonym_tau` turns out not to matter at all** — flat at .633–.650 across 0.70→0.95 while
the synonymy-edge count moves 5,516 → 26. Only `link_threshold` is load-bearing, which the
original §Flag design did not anticipate: it exposed `synonym_tau` as a tunable and left
`link_threshold` implicit.

### Honest out-of-sample: re-tuning recovers some, not all

`link_threshold=0.65` was chosen on the same 30 questions it is scored on, so the tuned row is
in-sample. Leave-one-out selection (tune on the other 29, score the held-out one):

| metric | inherited (0.5) | in-sample best | **LOOCV (honest)** |
| --- | --- | --- | --- |
| recall@5 | 0.633 | 0.817 (@0.65) | **0.750** |
| all-hops@5 | 0.467 | 0.667 (@0.70) | **0.500** |

Re-tuning genuinely recovers recall (+0.117 out-of-sample) but **all-hops barely moves
(0.467 → 0.500)**, and pure PPR does not return to 0.767 under any honest procedure. LOOCV
picked several different thresholds across folds (0.65/0.70/0.85/0.90) — the optimum is not
sharply identified at n=30.

### Consequences for SM-H4

1. **Ship hybrid, not pure PPR.** Pure PPR's advantage did not survive an embedder change.
2. **`link_threshold` must be calibrated per embedder** — inheriting a value costs ~0.18
   recall@5 here, silently. Measured across three embedders (§Measurements): background
   entity–entity cosine p50 is 0.41 for bge-base but 0.15 for both Azure models, so a single
   constant means opposite things. A **distribution-relative cutoff (p99.9 of the observed
   background distribution) had zero regret on two of three embedders** and is now implemented
   (`HippoRetriever(link_percentile=...)`). Honest limit: its worst-case regret (0.033) only
   *ties* a well-chosen constant (0.65), so the evidence supports *"0.5 was the wrong
   constant"*, **not** *"percentiles beat constants"*. Prefer the percentile because it needs
   no per-embedder retuning — an operational argument, not a measured win.
3. **Promote `link_threshold` into the flag config** alongside `synonym_tau` — and consider
   dropping `synonym_tau`, which bought nothing here.

Two checks so the dense-leg conclusion is trustworthy rather than convenient:
- **BGE query instruction.** `bge-*-v1.5` normally prefixes queries with *"Represent this
  sentence for searching relevant passages: "*; omitting it could have handicapped dense.
  Measured both ways — the prefix makes it **worse** (recall@5 0.725, all-hops 0.433), so the
  reported numbers are the *favourable* configuration for the dense baseline.
- **Spurious BLAS warning.** macOS Accelerate raises divide/overflow/invalid RuntimeWarnings
  from the cosine matmul. Verified benign — all inputs/outputs finite, row norms exactly 1.0,
  BLAS matches an independent `np.einsum` path to **1.8e-15**. Now suppressed narrowly with a
  hard `isfinite` assert behind it.

**EM/F1 are not comparable across the original and these runs.** The original LLM cache was
git-ignored, so reader calls were re-issued: `vector`'s EM moved 0.567 → 0.467 while its
retrieval barely changed, which is reader drift, not retrieval. **Retrieval metrics are the
sound comparison.** Separately, one reader call per run is refused by the Azure content filter
(a 2Wiki passage, `sexual: medium`); it lands in `hybrid`, scores as no-answer, and is reported
as `reader_content_filtered` — so hybrid's EM/F1 are a lower bound by up to 1/30.

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

**GO (provisional) — adopt Optional PPR (HippoRAG) behind a per-tenant flag, default off,
in the `hybrid` (dense + PPR) configuration.** On SM-R4's ratified embedder the prototype
shows measurable multi-hop value on an established benchmark — recall@5 0.742→0.883,
all-hops-hit 0.467→0.700, chaining all-hops 0.333→0.625, chaining QA F1 0.361→0.708 — at a
one-time, async-able write cost and a query-time PageRank step that is <100ms. PPR is
**additive** to SM-G9 BFS (a ranked multi-hop mode), not a replacement, and is gated per tenant
so non-multi-hop tenants pay nothing.

**Ship hybrid, not pure PPR — this is now an evidence-based choice, not a hedge.** Pure PPR led
on the original non-ratified embedder (0.908) and **lost 0.275 recall@5** when moved to SM-R4's
at inherited thresholds (0.633), recovering only to 0.817 in-sample / ~0.750 leave-one-out after
re-tuning. Hybrid moved 0.892 → 0.883 over the same change. **Pure PPR's headline was an
artifact of one embedder's threshold calibration; hybrid is robust to it** (§Embedder correction).

**Why "provisional", specifically.** Three things must land before this is ratifiable:

1. **The adopt threshold is keyed on a statistic this eval cannot separate.** At n=30 the
   `all-hops-hit` CIs overlap (chaining-only: dense .167–.542 vs PPR .50–.875). Only recall@5
   is beyond noise. **Any switch threshold keyed on all-hops needs n≥100, or a power analysis
   establishing the n required to detect the threshold effect size, before SM-R1 ratifies it.**
2. **The production PPR substrate assumes an engine that has not been chosen.** See below.
3. **KG quality is now measured and it is mediocre:** only **37% of questions have their
   full gold chain walkable** in the KG (edge recall 0.639). PPR's measured benefit therefore
   rests largely on membership structure, not correct relational edges (§Prototype).
4. **`link_threshold` needs per-embedder calibration on a held-out set.** Inheriting it cost
   ~0.18 recall@5 here, silently — an operational risk for SM-H4, not just a tuning detail.

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
  an index cost ≤ ~1 LLM OpenIE call/passage — point estimate **met on SM-R4's embedder
  (+0.292, 0.333→0.625 hybrid)**, **but the CIs for this metric overlap at n=30, so the
  criterion is checked against a point estimate the eval cannot resolve. Re-measure at n≥100
  (or run a power analysis) before ratifying.** Note the criterion is also *embedder- and
  threshold-conditional*: the same criterion evaluated on pure PPR at inherited thresholds
  gives +0.084 and would **fail**.
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

## Fusion method: weighted vs RRF (§Fusion)

SM-R8 ratified **weighted / relative-score** fusion for its 2-leg case and said explicitly *"do
not adopt RRF automatically when SM-G9's graph leg joins."* SM-R9 originally only ever ran RRF.
Both are now measured with the **same code** — `harness/fusion.py` is ported from SM-R8's
`benchmarks/memory_retrieval/fusion.py`, not reimplemented. Full write-up:
`results/summary_fusion.md`.

| method | recall@5 | all-hops@5 | EM | F1 | chaining all-hops / F1 |
| --- | --- | --- | --- | --- | --- |
| hybrid — weighted (w=0.5, SM-R8's ratified default) | 0.800 | 0.567 | 0.467 | 0.475 | 0.458 / 0.385 |
| hybrid — **RRF (k=60)** | **0.883** | **0.700** | **0.667** | **0.719** | **0.625 / 0.708** |

Swept across `vector_weight` 0.0→1.0, **RRF's point estimate is above every weighted setting**
(best weighted: .817 recall / .633 all-hops, both at **w=0.0** — i.e. *discard the dense leg*,
which is just pure PPR). Within SM-R8's ratified 0.3–0.7 band weighted is worse still. At w=0.5
weighted is beaten even by not fusing at all.

**But this is a tie, not an RRF win.** Paired bootstrap (10k resamples, SM-R8's test):
recall@5 +0.067, CI [−0.033, +0.175], **p=0.26**; all-hops +0.067, CI [−0.100, +0.267],
**p=0.58**. n=30 cannot resolve it. Two things can still be said:

1. Weighted never exceeded RRF at any weight, and LOOCV over the weight also lands below RRF
   (recall .783, all-hops .633) — consistent, if underpowered.
2. **SM-R8's effect does not reproduce here.** SM-R8 measured weighted beating RRF by +0.076
   (p=0.0001); in this sign convention that is −0.076, which falls **outside** our CI. So the
   two results cannot be explained as one underlying effect — SM-R8's §3.5 caveat (one dataset,
   one *synthetic* graph leg built from k-NN over dense embeddings) is vindicated, and its
   instruction to measure against a real graph retriever first was correct.

**Mechanism — and a hypothesis I had to discard.** The obvious story is that min-max
normalization mangles PPR's skewed stationary probabilities. **Measured, and it is false:** mean
normalized score over ranks 2–10 is dense .193 vs PPR .163, i.e. near-identical after
normalization. The real difference is **rank-decay steepness** — RRF at k=60 scores rank 1 at
1/61 and rank 10 at 1/70, a ratio of just **1.15** (nearly flat, so what dominates is whether an
item appears in *both* legs), whereas weighted min-max runs 1.00 → ~.15 across the same range, a
ratio of **6.7** (one leg's top hit outweighs an item mid-ranked in both). **RRF rewards
cross-leg agreement; weighted rewards single-leg confidence.** For `all-hops-hit`, where the
second bridge passage is usually mid-ranked in both legs rather than top in either, agreement is
the right bias — and that is where RRF's margin is largest (+0.133 vs +0.083 on recall). This is
consistent with the data, not proven; an `rrf_k` ablation would confirm it.

**Disposition:** keep RRF as the recommended fusion **because weighted showed no advantage**,
not because RRF is demonstrably better. Neither is ratifiable at n=30; SM-H4 should re-test both
against SM-G9's production graph retriever.

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
  fused with dense via a pluggable `harness/fusion.py` strategy (both sync + async). **RRF (k=60)
  is the recommended default** — weighted was measured and showed no advantage on this leg pair,
  though not significantly so at n=30 (§Fusion). Re-test both on the real graph retriever.
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
- **Fusion method for dense+PPR** → measured (§Fusion): weighted shows no advantage over RRF on
  this leg pair, but the difference is not significant at n=30, so neither is *ratified*. SM-H4
  should re-test both against SM-G9's real graph retriever.
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
| **SM-R8** (fusion) | **measured — SM-R8's result does not transfer** | Weighted swept 0.0–1.0 using SM-R8's own code: no advantage on dense+PPR, optimum degenerate at w=0.0. Not significant at n=30, but our CI excludes SM-R8's effect size. §Fusion. |
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
- **On the original embedder this spike appeared to replicate that failure mode** — hybrid-RRF
  (0.892) < pure PPR (0.908). **The corrected re-run removes that evidence:** on SM-R4's
  embedder hybrid-RRF (0.883) now clearly *beats* pure PPR (0.817), so RRF is not visibly
  dragging the fusion down here. The original inversion is better explained by pure PPR being
  over-favoured by `text-embedding-3-small`'s threshold calibration than by fusion mechanics
  (§Embedder correction). Stated plainly because an earlier draft of this section argued the
  opposite from the pre-fix numbers.
- That **weakens SM-R9's local evidence for SM-R8's §3.5 effect but does not contradict it** —
  SM-R8 measured weighted-vs-RRF directly, whereas this spike only ever ran RRF. The two
  results are compatible: RRF can beat pure PPR while still losing to weighted.
- The citation this spike originally leaned on — "RRF, the research doc's default fuse" — was
  **downgraded by SM-R8 §9**: *"The research's 3-leg RRF recommendation is not yet confirmed."*

**Now measured** (§Fusion). Weighted fusion was ported from SM-R8's own `fusion.py` and swept
across `vector_weight` 0.0–1.0. **SM-R8's result does not transfer to this leg pair:** RRF's
point estimate is higher at every weight (recall@5 .883 vs .817 best-weighted), and weighted's
in-sample optimum is *degenerate* at w=0.0 — "don't blend". At SM-R8's ratified w=0.5, weighted
hybrid (.800/.567) is worse than RRF **and worse than not fusing at all** (pure PPR .817/.633).
The difference is **not significant** at n=30 (paired bootstrap p=0.26/0.58), so this is a tie
rather than an RRF win — but our 95% CI [−0.033, +0.175] **excludes** SM-R8's −0.076 effect,
so the two results are not reconcilable as one underlying effect. SM-R8's own §3.5 caveat (one
dataset, one *synthetic* graph leg) is vindicated, and its instruction to measure against a real
graph retriever before adopting either was the right call.

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
