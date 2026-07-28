<!--
SM-R8 decision document. All numbers are produced by this benchmark (benchmarks/
memory_retrieval) on real labeled data through the real egpt abstractions. Regenerate
with run.py / sweep.py; see README.md. Raw results in results/.
-->
# SM-R8 — Decision: retrieval fusion + reranker

- **Status:** Proposed
- **Tracking issue:** [Memory] SM-R8 — Saturam-Inc/aicoe-tracker#83
- **Decisions referenced:** D7 + D8
- **Depends on:** SM-R1 (#76) — the scale/latency/quality targets below are *provisional*
  for the SM-R1 ADR to ratify.
- **Feeds:** SM-H2, SM-H3 (hybrid retrieval implementation).
- **Couples to:** SM-R3 (store hybrid support), SM-R4 (embeddings), SM-E7 (time-aware
  episodic scoring), SM-G9 (graph retriever).

Grounded in `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` §3 "Hybrid retrieval
mechanics" (RRF k≈60 over dense+sparse[+graph]; two-stage cross-encoder rerank; composite
time-aware scoring).

---

## 1. Decision (summary)

1. **Fusion method — WEIGHTED / relative-score fusion now, ratified for the 2-leg
   dense+BM25 case.** Weighted fusion is the *measured, statistically-significant* winner
   over RRF on every dataset (including the memory set). **The 3-leg (+graph, SM-G9/D8)
   fusion choice is PROVISIONAL, not asserted** — the issue's theory ("RRF's rank-basis
   wins once a third, differently-scaled scorer joins") predicts a switch to RRF, but a
   real 3-leg measurement (§3.5, PR #16 review: a synthetic PPR graph leg) found the
   **opposite direction** — weighted's edge over RRF *widened* (+0.023 → +0.076) rather
   than flipping. That result is one dataset / one synthetic graph construction and does
   not settle the production case, so: **keep pgvector's weighted hybrid now**
   (`vector_score_weight ≈ 0.4–0.5`); **do not adopt RRF automatically when SM-G9's graph
   leg joins** — measure weighted-vs-RRF (and per-leg-calibrated weighted) against the
   real graph retriever first (§3.5, §6).
2. **Reranker — no cross-encoder on the default hot path; optional, GPU-served, Mode-A
   self-hosted stage when enabled.** A cross-encoder rerank was neutral-to-harmful in our
   measurements (it **significantly hurt** the memory set) and cost **~2.0–2.5 s/query on
   CPU** — an order(s) of magnitude over the research's 50–300 ms budget (which assumes GPU).
   Weighted fusion beats reranking at ~100× lower latency. If reranking is enabled, the
   Mode-A (air-gapped) choice is a **self-hosted cross-encoder** (`sentence_transformer`
   `BAAI/bge-reranker-v2-m3` or `infinity`), **GPU-served, behind a ≤300 ms budget**; Cohere
   is Mode-B only.
3. **Time-aware episodic scoring — not an additive query-time re-ranker.** Blending recency
   into the relevance score **monotonically degraded** quality and **significantly hurt** the
   memory set. Placement: recency as an *optional pre-filter* for explicitly time-scoped
   queries, and **importance as a write-time signal (SM-E7)** — never a global hot-path
   re-score.
4. **No query-time LLM on the hot path — confirmed.** The measured pipeline is
   embed-query → BM25 → fuse → [optional rerank]; recency/importance are arithmetic or
   precomputed. Zero LLM calls at read time.

---

## 2. Acceptance criteria (from #83)

- [x] **Fusion method chosen for the 2-leg case, with rationale** — §1.1, §4, evidence §3,
  sweeps §3.3. Weighted now (significant win, 2-leg). **The 3-leg/graph-join fusion choice
  is provisional, not settled** — §3.5 measured a synthetic 3-leg case and found weighted's
  edge over RRF widens, not flips; real measurement against SM-G9's graph retriever is a
  gate before ratifying either method for 3-leg (PR #16 review).
- [x] **Reranker chosen per mode (self-hosted for Mode A) + latency budget set** — §1.2, §4.
  Mode A: self-hosted `bge-reranker-v2-m3`/`infinity`, GPU-served; Mode B: Cohere. Budget
  ≤300 ms (GPU); measured CPU ~2.0–2.5 s shows it is off the default hot path.
- [x] **Placement of time-aware episodic scoring decided** — §1.3, §5 (SM-E7 coupling):
  write-time importance + optional recency pre-filter, not an additive re-ranker. **Reconciled
  with sibling decision SM-R5's ranking-primary decay** — §3.6 (PR #16 review): not a
  contradiction, both measured the same quality cost; they optimize different layers.
- [x] **Confirms no query-time LLM on the hot path** — §1.4.

---

## 3. Questions answered (from #83)

| Question | Answer |
| --- | --- |
| **RRF (k≈60) vs weighted score fusion, esp. once a graph retriever joins?** | **Weighted wins the 2-leg (dense+BM25) case now** — significant over RRF on all 4 datasets (§3.1). **The graph-join switch to RRF is NOT confirmed** — §3.5's synthetic 3-leg (dense+BM25+PPR-graph) measurement found the opposite of the theorized direction (weighted's edge widened, +0.023→+0.076, p<0.001); treat as provisional and measure against SM-G9's real graph retriever before switching. |
| **Which reranker + self-hosted vs provider (dual-mode)?** | **Mode A (air-gapped): self-hosted cross-encoder** — `sentence_transformer` `BAAI/bge-reranker-v2-m3` (offline) or `infinity` (served). **Mode B: Cohere `rerank-v3.5`.** But **off by default** — see latency. |
| **Latency budget for the rerank stage (research +50–300 ms)?** | Adopt **≤300 ms added** *as a gate*: only enable rerank where it can be met (GPU/served). Measured on this CPU host: **~2.0–2.5 s/query p50** (real docs) — so rerank is **not** on the default CPU hot path. |
| **Where does time-aware episodic scoring (SM-E7) apply?** | **Not** as an additive query-time score (it hurt). As an **optional recency pre-filter** for time-scoped queries + **write-time importance** consumed as a light prior. Keeps the read path LLM-free. |

---

## 4. Options evaluated

All five run through one config-driven `RetrievalPipeline` (swap fusion/reranker/scorer by
config, no call-site change). Embedder = local `all-MiniLM-L6-v2` for the full §3.1 matrix
below — **this predates SM-R4's ratified embedder choice and is superseded; see the §3.4
correction**, which re-validates the method ranking against the actual chosen embedder
(`bge-base-en-v1.5`) on 2 of the 4 datasets. Strong-embedder Azure cross-check in §3.2.
nDCG@10, paired-bootstrap significance. Reranker (d) was run on the 3 smaller sets (fiqa d
skipped — its conclusion is settled and CPU rerank of the 57k-corpus set is prohibitively
slow; see §8).

### 3.1 Fusion + rerank + composite — nDCG@10 (4 datasets, ~1,741 queries)

| option | scifact | nfcorpus | longmemeval | fiqa | p50 latency |
| --- | --- | --- | --- | --- | --- |
| (a) vector-only | 0.6451 | 0.3160 | 0.8271 | **0.3687** | <1–8 ms |
| (b) hybrid + RRF (k=60) | 0.6768 | 0.3354 | 0.8934 | 0.3385 | 6–290 ms |
| (c) hybrid + **weighted** (w=0.5) | **0.7113** | **0.3452** | **0.9209** | 0.3657 | 6–280 ms |
| (d) hybrid + RRF + rerank (MiniLM) | 0.6956 | 0.3535 | 0.8414 | — | **1953–2459 ms** |
| (e) hybrid + RRF + composite recency | 0.6768 | 0.3354 | 0.8337 | 0.3385 | 2–270 ms |

R@100 (large-corpus recall, where it discriminates): fiqa vector 0.706, RRF 0.692,
weighted 0.691. (BEIR small sets + LongMemEval saturate R@100; see README.)

**Key paired comparisons (nDCG@10, 95% CI, "ranked"=CI excludes 0):**

| comparison | scifact | nfcorpus | longmemeval | fiqa |
| --- | --- | --- | --- | --- |
| **weighted vs RRF** | **+0.034 ✓** | **+0.010 ✓** | **+0.028 ✓** | **+0.027 ✓** |
| RRF vs vector | +0.032 ✓ | +0.019 ✓ | +0.066 ✓ | **−0.030 ✓ (loss)** |
| weighted vs vector | +0.066 ✓ | +0.029 ✓ | +0.094 ✓ | −0.003 tie |
| rerank(MiniLM) vs RRF | +0.019 tie | +0.018 ✓ | **−0.052 ✓ (loss)** | — |
| composite vs RRF | tie (no time) | tie (no time) | **−0.060 ✓ (loss)** | tie (no time) |

**Winner: weighted fusion.** It **significantly beats RRF on all 4 datasets**, and unlike RRF
it never significantly *loses* to vector-only (on fiqa's strong-dense domain RRF significantly
hurts, while weighted ties vector — because its tunable `vector_weight` lets dense dominate,
i.e. weighted fusion *subsumes* vector-only at w→1). **Reranker (MiniLM): rejected on the hot
path on latency alone** — ~100× slower than the ≤300 ms budget, which is sufficient by itself
(see §3.1b: the stronger Mode-A candidate is ~150-200× over budget). The *quality* verdict is
weaker than "neutral/harmful" implies: it's dataset-specific and n=1 for the harm —
**significantly hurt the memory set** (longmemeval −0.052), **significantly helped nfcorpus**
(+0.018), tied scifact (PR #16 review; see §3.1c for what that implies for "which reranker if
enabled"). **Composite recency: rejected as an additive score** — significant loss on the only
set where recency is real.

Note: whether to fuse BM25 *at all* is domain/embedder-dependent (helps scifact/nfcorpus/
longmemeval; neutral-to-harmful on fiqa) — but *given* fusion, **weighted is the better method**.

### 3.1b Stronger self-hosted reranker (bge-reranker-v2-m3) — latency spot-check

`BAAI/bge-reranker-v2-m3` (568M params; the egpt default, self-hostable/air-gap-capable) was
run on CPU as the stronger Mode-A candidate. **It is latency-prohibitive on CPU: ~40–50 s/query**
(reranking 50 candidates; a bounded 50-query scifact slice did not finish in 40 min), i.e. ~150–200×
the MiniLM cross-encoder and ~150× over the 300 ms budget. This confirms the reranker conclusion
independent of quality: **any cross-encoder rerank requires GPU/served hosting to meet the budget**;
on the air-gapped CPU target it cannot sit on the hot path. (A quality comparison at scale was not
completed for this reason; on the 50-query subset weighted fusion still led RRF, 0.838 vs 0.820,
before rerank. MPS was ruled out — it OOMs on this model; see the device note in `rerank.py`.)

### 3.1c Reranker coverage — measured vs reasoned (transparency)

The `RerankStage` wraps **any** egpt `Reranker` (adapter chosen by config), so all five
reranker adapters are wired. What was **empirically run**: the self-hosted **`sentence_transformer`**
cross-encoder — `ms-marco-MiniLM-L-6-v2` (full, 3 datasets) and `bge-reranker-v2-m3` (latency +
subset). What was **not run, and why**: `infinity` serves the *same* cross-encoder models
(bge-reranker), so its quality is identical to the measured `sentence_transformer` path — only the
serving latency differs; `cohere` (Mode B) needs an API key and is off the air-gapped critical
path; `aws_bedrock` likewise. Since the **Mode-A self-hosted path is the deployment-critical one
and it was measured**, and the overall verdict is "rerank off the default hot path," the Mode-B
adapter choice (`cohere`) is selected by **reasoning, not measurement** — flagged here so it isn't
read as benchmarked. If reranking is later enabled, `infinity` should be latency-benchmarked served.

**PR #16 review — "which reranker if enabled" is unbenchmarked, not just Mode-B:** the §3.1
*quality* verdict (rerank hurts/helps/ties per dataset) was measured on `ms-marco-MiniLM-L-6-v2`,
a **different, weaker cross-encoder** than §1.2's actual recommended Mode-A reranker
(`bge-reranker-v2-m3`) — whose quality was never measured at all, only its latency (§3.1b, CPU
only, ~40–50 s/query). So "off by default" is solidly latency-justified (any cross-encoder blows
the budget on this CPU host), but if/when GPU-served reranking is revisited, the *quality*
question should be treated as **fully open**, not settled by the MiniLM numbers above — measure
`bge-reranker-v2-m3` (and `infinity`, GPU-served) on quality *and* latency in SM-H3 before
picking a reranker to enable.

### 3.2 Strong-embedder cross-check (Azure `text-embedding-3-small`, BEIR only)

| dataset | vector | RRF | weighted |
| --- | --- | --- | --- |
| scifact | 0.7296 | 0.7188 | **0.7471** |
| nfcorpus | **0.3838** | 0.3619 | 0.3721 |

With a strong embedder the fusion advantage **narrows** (weighted still best on scifact; on
nfcorpus vector-only edged ahead; significance not computed for these partials). This mirrors
the fiqa result with the local embedder (strong-dense domain → BM25 doesn't help). **Implication:**
whether to fuse BM25 and at what weight is embedder-/domain-dependent — SM-R4's production
embedder should re-validate `vector_weight` and whether hybrid clears noise. The *fusion method*
conclusion (weighted > RRF) is stable across both embedders. (LongMemEval could not be embedded
on Azure — TPM rate limit, §8 / findings.)

### 3.3 Parameter sweeps (switch thresholds) — nDCG@10

- **RRF k** ∈ {10,30,60,100}: mildly *decreasing* in k on all sets (e.g. longmemeval
  0.9015→0.8925); gaps ~0.01. **k=60 is a safe, research-aligned default**; k∈[10,60] fine.
- **Weighted `vector_weight`** ∈ {0.3..0.7}: BEIR peaks ~**0.5**; **LongMemEval peaks at 0.3**
  (0.9273) — memory retrieval wants *more* BM25/lexical weight. **Default 0.4–0.5**; consider
  ~0.3 for memory-heavy tenants.
- **Composite recency_weight** ∈ {0..1.0} (LongMemEval): monotonically **hurts**
  (0.8934 → 0.7265). **Default 0** on the hot path.

### 3.4 PR-review correction — re-validated against SM-R4's ratified embedder

**Issue (PR #16 review):** §3.1's matrix and this task's code (`run.py::build_embedder`)
defaulted to a placeholder embedder (`all-MiniLM-L6-v2` local / `text-embedding-3-small`
Azure) instead of **SM-R4's ratified choice** (`benchmarks/memory_embedding/DECISION.md`
§1: `BAAI/bge-base-en-v1.5` Mode A / `text-embedding-3-large`@1024 Mode B) — because this
branch forked before SM-R4 merged. This is not a neutral placeholder: SM-R4's own eval
ranks `minilm-l6` **last** of 11 embedders tested (nDCG 0.861 vs bge-base's 0.911), so the
§3.1 matrix measured fusion behavior on the weakest embedder in the family, not the one
that will actually ship. Fixed in `run.py`/`embcache.py` (defaults now match SM-R4 §1).

**Re-validated (real, measured — not reasoned):** re-ran options a/b/c on scifact,
nfcorpus, **and longmemeval** with the corrected local embedder (`BAAI/bge-base-en-v1.5`,
real HF download, real inference, same harness/significance test as §3.1). LongMemEval
was re-run specifically because the PR review flagged it as the most load-bearing case —
the biggest measured fusion gain (weighted +0.094 vs vector, MiniLM) sat on the weakest
embedder, with no strong-embedder cross-check (Azure hit a TPM limit on it, §3.2/§8):

| dataset | a-vector | b-rrf | c-weighted | c vs b | b vs a | c vs a |
| --- | --- | --- | --- | --- | --- | --- |
| scifact | 0.7376 | 0.7217 | **0.7445** | **+0.023 ✓ (p=0.003)** | −0.016 tie | +0.007 tie |
| nfcorpus | 0.3693 | 0.3565 | **0.3671** | **+0.011 ✓ (p=0.006)** | −0.013 tie | −0.002 tie |
| **longmemeval** | **0.8717** | **0.9104** | **0.9297** | **+0.019 ✓ (p<0.001)** | **+0.039 ✓ (p<0.001)** | **+0.058 ✓ (p<0.001)** |

(`results/results_20260728T084006Z_smR4EmbedderVerify.json` +
`results/results_20260728T090818Z_smR4EmbedderVerifyLME.json` and their `summary_*.md`.)

**What holds everywhere:** the decision's central claim — **weighted fusion significantly
beats RRF** — is confirmed on the *correct* embedder on all 3 re-run datasets (p<0.01), so
§1.1/§4's method ranking is not an artifact of using the wrong embedder.

**What changes — and it's dataset-dependent, not a blanket "narrows":** on the two general-IR
BEIR sets, RRF's and weighted's edge *over vector-only* **collapse from significant wins
(§3.1, MiniLM) to ties** with the stronger embedder — the same embedder-strength-dependence
§3.2/§6 already flagged from the Azure side-check, now confirmed on Mode A's actual
production embedder. **But on longmemeval — the memory-shaped, most-relevant set — every
relationship stays significant with the correct embedder too**, just at smaller magnitudes
(weighted vs RRF +0.028→+0.019; RRF vs vector +0.066→+0.039; weighted vs vector
+0.094→+0.058, all still p<0.001). So the reviewer's specific worry — "the biggest,
most-relevant gain sits on the weakest embedder" — is **not confirmed**: the memory-relevant
gain shrinks somewhat but survives the embedder correction, while the general-IR gain (which
was smaller to begin with) is what actually washes out. Practical read: whether to fuse
BM25 *at all* is embedder- **and domain**-dependent — real for memory-shaped queries even on
a strong embedder, closer to a wash on general-IR text — and **§6's existing handoff to
SM-R4 to "re-validate `vector_weight` and whether hybrid still clears noise"** should be
read per-domain, not as a single verdict.

**Not re-run (scope of this correction, flagged not faked):** fiqa (57k-doc corpus,
~68 min one-time embed per `TEST_LOG.md` — a full-matrix re-run is follow-up work, not
this fix); the reranker (d) and composite-recency (e) options were not re-run against the
corrected embedder on any dataset because their conclusions (CPU cross-encoder latency;
recency-as-additive-score hurting quality) are properties of the reranker/scorer stage, not
the bi-encoder, so they don't depend on which embedder produced the dense leg.
**Recommendation before ratification:** re-run the full a–e matrix on fiqa (the one
remaining dataset) with the corrected embedder to replace §3.1 outright; this correction is
a targeted, honest re-check, not a substitute for that.

### 3.5 PR-review correction — 3-leg fusion (dense+BM25+graph), weighted vs RRF

**Issue (PR #16 review):** §1.1/§3 measure fusion for the **2-leg** (dense+BM25) case only,
then *assert* — on theoretical grounds (RRF's rank-basis handles incomparable score scales)
— that RRF should replace weighted fusion the moment a graph leg (SM-G9, D8's 3-leg
production target) joins. The reviewer noted this is "a design bet: the 3-leg configuration
where RRF is claimed to win is exactly the one not built or tested," and asked for a 3-leg
measurement — "even synthetic" — before that switch is ratified.

**What was measured:** SM-G9's graph retriever doesn't exist yet, and the one real PPR
prototype in this repo (SM-R9's `HippoRetriever`) needs LLM-based OpenIE to build its KG —
out of scope here (no Azure spend without sign-off; it was also evaluated on a different
corpus). So this adds a **lightweight, no-LLM synthetic graph leg**: a k-NN similarity graph
built from the corpus' own dense embeddings, then real Personalized PageRank (power
iteration, seeded from the dense retriever's own top-20 hits per query) —
`graph_leg_experiment.py`. This is a real graph algorithm producing a genuinely
differently-scaled score distribution (PPR's power-iteration diffusion is heavily
right-skewed / near-zero-for-most-nodes, unlike bounded cosine or BM25) — an honest proxy
for the **fusion-math** question (does a third, incomparable-scale scorer flip weighted vs
RRF), explicitly **not** a claim about real graph-topology retrieval quality (that needs
SM-G9's actual entity/citation graph).

**Result (scifact, n=300, real PPR, real significance test):**

| config | nDCG@10 | vs 2-leg equivalent |
| --- | --- | --- |
| 2-leg RRF (dense+BM25) | 0.7217 | — |
| 2-leg weighted (dense+BM25) | 0.7445 | — |
| 3-leg RRF (+graph) | 0.6635 | **−0.058** (adding the graph leg via RRF *hurt* vs 2-leg RRF) |
| 3-leg weighted (+graph) | 0.7394 | −0.005 (near-flat; weighted absorbed the 3rd leg cleanly) |

| comparison | Δ nDCG@10 | 95% CI | p | ranked? |
| --- | --- | --- | --- | --- |
| 3-leg weighted vs 3-leg RRF | **+0.076** | [+0.056, +0.096] | 0.0001 | **yes** |
| 2-leg weighted vs 2-leg RRF | +0.023 | [+0.007, +0.039] | 0.003 | yes |

**This is the opposite of what §1.1's theoretical argument predicts.** Adding a third,
differently-scaled (PPR) leg did **not** flip the advantage to RRF — weighted fusion's edge
over RRF **widened** (+0.023 → +0.076), because RRF's equal, rank-only vote let the noisier
graph leg drag the fused ranking down (3-leg RRF fell *below even 2-leg RRF*), while
weighted's bounded, per-leg-normalized sum absorbed the extra leg without much cost. This is
exactly the failure mode the reviewer flagged as untested: "it's possible weighted ... still
wins 3-way." On this synthetic leg, it does.

**What this does and doesn't settle:** this is one dataset, one synthetic graph construction,
equal (untuned) per-leg weights on both sides, and an embedding-derived graph leg that is
correlated with dense by construction (unlike a real entity/citation graph would be) — so it
is evidence against the "RRF wins once graph joins" assumption, not proof either method wins
with SM-G9's real graph retriever. **§1.1's fusion-method recommendation for the 3-leg case
is downgraded from an asserted switch to explicitly provisional** — see §1/§6 update.
**Recommendation (unchanged from the reviewer's ask, now with a concrete counter-signal):**
SM-H2/H3 must measure 3-leg weighted-vs-RRF (and per-leg-calibrated weighted fusion) against
the real SM-G9 graph retriever before ratifying either fusion method for the 3-leg case.

### 3.6 PR-review reconciliation — recency placement vs SM-R5

**Issue (PR #16 review):** §1.3 sets recency weight = 0 on the retrieval hot path ("recency
monotonically hurts," measured on LongMemEval only), placing time-aware scoring as an
optional pre-filter + write-time importance (coupled to **SM-E7**). But sibling decision
**SM-R5** (`benchmarks/memory_decay/DECISION.md`, also unmerged/Proposed) makes
importance-weighted recency decay **ranking-primary, always on** for the memory tier:
`score = alpha*relevance + beta*recency`, `recency = 0.5^(age_since_access/half_life)`. The
reviewer: this apparent contradiction ("recency in ranking: SM-R5 yes, SM-R8 no") should be
stated explicitly so SM-E7/SM-H3 don't inherit contradictory guidance.

**Read of SM-R5's actual numbers (not assumed — read from its DECISION.md §2.1/§2.3/§2.5):**
SM-R5's own measurement **agrees with SM-R8's**, it just draws a different conclusion from
the same fact. Its `no_decay` baseline (pure relevance, no recency term) has the **highest**
nDCG/recall of every strategy tested (0.893→0.903 nDCG synthetic→real-embedding, recall
1.000). SM-R5's chosen `importance_weighted` strategy scores **lower** on those same metrics
(0.866→0.703 nDCG) — SM-R5 §2.3 says so directly: *"no_decay significantly beats it on raw
recall/nDCG ... the expected cost of decay, paid to buy the staleness and growth wins."*
So both decisions independently found: **adding recency to ranking costs retrieval quality.**
They diverge only in what that trade buys, because they optimize different things:

- **SM-R8** measures pure single-query retrieval quality (nDCG@10) on a static corpus with no
  notion of memory growth or pruning. Nothing in that benchmark offsets the quality cost, so
  the call is "don't pay it" — recency stays off the hot path.
- **SM-R5** measures a memory-*tier* objective across a 90-day horizon: bounded growth
  (pruning needs a decay signal, feeds SM-E9) and reduced stale-hit rate. It accepts the same
  quality cost SM-R8 measured, deliberately, because it buys those tier-level properties that
  SM-R8's static-corpus benchmark doesn't model at all.

**Reconciliation:** these are not the same mechanism at the same layer, even though both
technically "add recency." SM-R5's importance-weighted decay is a **slow, memory-tier signal**
(computed over the retained pool, governs retention/pruning + a coarse ranking bias) — this
*is* the **write-time importance** input SM-R8 already designated as its SM-E7 hook (§1.3/§6:
"importance as a write-time signal, consumed as a light prior"). SM-R8's rejected "composite
recency" is a different thing: a **hot-path, per-query, additive rescore** of already-fused
retrieval results from a raw timestamp gap, computed fresh at query time, with no
importance-weighting or pruning purpose. **Guidance for SM-E7/SM-H3: consume SM-R5's
decayed-relevance/importance output as the write-time prior SM-R8 already deferred to SM-E7 —
do not additionally re-enable a per-query additive recency rescore on top of retrieval fusion,
which SM-R8 measured to hurt independently of SM-R5's mechanism.**

**Two loose ends, flagged not resolved (need a human/architecture call, not a benchmark):**
- **Epic-reference mismatch:** SM-R8 couples time-aware scoring to **SM-E7**; SM-R5 couples
  its downstream work to **SM-E4** (recency scoring) and **SM-E9** (forgetting) and never
  mentions SM-E7. Whoever owns epic planning should confirm whether SM-E7 and SM-E4/SM-E9 are
  the same implementation task under different names or genuinely separate tickets before
  SM-H3 starts — as written, the two decisions hand off to different-looking downstream work.
- **Recency-semantics mismatch (reviewer's point):** LongMemEval's timestamp-recency (session
  freshness — is a newer session more likely the answer) is a different kind of signal from
  SM-R5's supersede-driven episodic recency (a fact was explicitly updated/contradicted). Both
  decisions' recency conclusions rest on benchmarks whose recency semantics don't fully match
  the production supersede-driven case — a real gap, not something this correction can measure
  away.

---

## 5. Seed-context verification & disposition

Paths verified against the current checkout (namespace still `egpt`; no rebrand applied here).

| Seed fact (from #83) | Verified | Disposition |
| --- | --- | --- |
| `vectordb/search.py:4` `SearchType = vector\|keyword\|hybrid` | ✓ (no `rrf`) | **Extend** — add an `rrf` fusion strategy for when the graph leg joins (SM-H2/H3). |
| `vectordb/pgvector/pgvector.py:62,146` `vector_score_weight=0.5`; `hybrid_search` (l.1067) fuses `vector_score*w + text_rank*(1-w)`, `text_rank = ts_rank_cd/(ts_rank_cd+0.1)` | ✓ — this **is** weighted/relative-score fusion, not RRF | **Reuse** — this is the chosen fusion for the 2-leg case now. Tune `vector_weight`. |
| `knowledge/reranker/` = `cohere`, `sentence_transformer` (`bge-reranker-v2-m3`), `infinity`, `aws_bedrock`, `base` | ✓ (base `rerank` sync-only; only `infinity` has `arerank`) | **Reuse** — `sentence_transformer`/`infinity` = Mode A, `cohere` = Mode B. Extend base with `arerank` (finding). |
| "No RRF implementation" | ✓ | **New** — RRF built here (`fusion.RRFFusion`); hand off to SM-H2/H3. |
| "No graph retriever to fuse yet (SM-G9)" | ✓ | **Out of scope** — graph leg arrives in SM-G9. Both fusion strategies are wired and ready (`fusion.py`); **which one to use for the 3-leg case is not decided here** — §3.5 found a synthetic 3-leg test favoring weighted, not RRF, so this is a gate for SM-H2/H3, not a settled switch. |

**Reuse/extend/replace summary:** reuse pgvector weighted hybrid + egpt rerankers +
egpt embedder; extend `SearchType` with `rrf` and the `Reranker` base with `arerank`;
new RRF fusion function. No `libs/agno/egpt/**` change in this task (see §10).

---

## 6. Couplings & handoff

- **SM-R3 (store hybrid support):** the chosen fusion is pgvector's existing weighted
  `hybrid_search` — **reuse as-is now**; the implementation task should add an **RRF fusion
  mode** (rank-based over dense+BM25[+graph]) behind `SearchType`/a fusion strategy so
  *either* method is available once the graph leg (SM-G9) joins — **which one wins 3-leg is
  not decided by this task** (§3.5: a synthetic 3-leg test favored weighted, contra the
  original theory). Concrete artifact: `fusion.RRFFusion(k=60)` +
  `fusion.WeightedFusion(vector_weight)`, both already N-leg-capable (not hardcoded to 2).
- **SM-R4 (embeddings):** the fusion benefit is **embedder-strength- and domain-dependent**
  — confirmed in §3.4 on SM-R4's *actual* ratified embedder (not just the Azure side-check),
  and it splits by domain: on general-IR BEIR (scifact/nfcorpus) the benefit narrows to a tie
  with a strong embedder; on **longmemeval (memory-shaped queries) it stays significant**,
  just smaller. SM-R4's production embedder must **re-validate `vector_weight` and whether
  hybrid still clears noise** — §3.4 shows this is a live, per-domain gap, not a hypothetical
  or blanket one. Dimension/latency of the embedder are SM-R4's to set; this task assumes a
  dense vector + a BM25 sparse index per tenant.
- **SM-E7 (time-aware episodic scoring):** placement decided — **write-time importance** (a
  per-memory salience in [0,1]) consumed as a light prior, and **recency as an optional
  pre-filter** for time-scoped queries; **not** an additive query-time re-score (measured
  loss). Concrete artifact: `scoring.CompositeScorer` with `w_recency=0` default and an
  `importance` hook fed by SM-E7. **§3.6 (PR #16 review): this importance hook should be fed
  by SM-R5's decay/importance output** — not layered with an additional per-query recency
  rescore, which SM-R8 measured to hurt independently. Also flags an unresolved epic-naming
  mismatch: SM-R5 hands off to SM-E4/SM-E9, not SM-E7 — confirm these are the same task
  before SM-H3.
- **SM-H2/H3 (implementation):** ship `RetrievalPipeline` (retrieve→fuse→[rerank]→[filter])
  with fusion + reranker selected by config; both sync + async; reranker off by default.

---

## 7. Latency budget (provisional, for SM-R1)

| stage | measured (this Intel-CPU host, indicative) | budget |
| --- | --- | --- |
| vector-only | <1 ms | — |
| + BM25 + fusion (RRF/weighted) | 6–24 ms p50 | keep on hot path |
| + cross-encoder rerank (MiniLM, CPU) | **1953–2459 ms p50** | **exceeds** → GPU/served only |
| + cross-encoder rerank (bge-reranker-v2-m3, CPU) | **~40–50 s/query** | **grossly exceeds** → GPU/served only |
| composite recency rescoring | +1–20 ms | negligible (but off by default) |

**Budget:** hot path (embed+BM25+fusion) target **p95 < ~50 ms** in-VPC (provisional).
Rerank, if enabled, must meet **≤300 ms added** — achievable only GPU-served; on CPU it is
~2 s and therefore off the default path. Query-embedding latency is provider-dependent (SM-R4)
and excluded here as option-invariant.

---

## 8. Out of scope & boundaries

- **Graph retriever / PPR** — SM-G9 (this decision is graph-ready — both fusion strategies
  are wired — but §3.5's synthetic 3-leg check means the RRF-on-graph-join switch is a gate
  for SM-H2/H3 to measure, not something to inherit from this doc as settled).
- **Embedder model/dimension** — SM-R4 (this task now defaults to SM-R4's ratified choice,
  §3.4; a full 4-dataset/5-option re-run on it is follow-up work, not this correction).
- **Decay/importance model** — SM-E7 (this task only *places* time-aware scoring, doesn't
  define the decay curve or importance scorer).
- **Store engine choice / tenancy** — SM-R3.
- **Absolute p95 SLOs / scale targets** — SM-R1 ratifies; numbers here are provisional and
  measured on a single Intel-CPU laptop host (indicative).

---

## 9. Consistency with dependencies & siblings

- **Depends on SM-R1 (#76, ADR)** — not yet ratified; scale/latency/quality targets here are
  flagged provisional for it.
- Consistent with the research doc's §3 (RRF for multi-scale fusion; cheap LLM-free read path;
  cross-encoder as an *optional* two-stage step) — with the empirical refinement that for the
  **2-leg** case weighted beats RRF, and CPU rerank blows the latency budget. **The research's
  3-leg RRF recommendation is not yet confirmed** — §3.5's synthetic check points the other way.
- Graph-ready for **SM-G9** (both fusions wired, 3-leg choice gated — §3.5); hands the
  fusion/reranker seams to **SM-H2/H3**; hands time-aware placement to **SM-E7**
  (reconciled with SM-R5's decay model, §3.6); flags embedder re-validation for **SM-R4**.

---

## 10. Grounding & reproducibility

- **Grounded in** `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` §3.
- **Datasets:** BEIR (scifact/nfcorpus/fiqa) + LongMemEval-S — real, independently labeled;
  see README + §3 for stats and honest limits (fiqa CPU-embedding cost; local vs Azure
  embedder; Intel-CPU latency).
- **Reproduce:** `python -m benchmarks.memory_retrieval.run --datasets ... --options a b c d e`
  and `... .sweep --datasets ...`; committed results in `results/`. Data-quality + method in
  README.
- **Core untouched:** `git diff main -- libs/agno/egpt` shows no change from this task; adapter
  seams found are saved under `findings/` (ST-version reranker seam; async-batch/rate-limit
  embedding seam), deferred to the implementation task.
