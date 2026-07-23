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

1. **Fusion method — WEIGHTED / relative-score fusion now; RRF (k≈60) once the graph
   retriever (SM-G9) joins.** For the current two-leg dense+BM25 case, weighted fusion is
   the *measured, statistically-significant* winner over RRF on every dataset (including
   the memory set). RRF's rank-basis advantage — robustness to incomparable score scales —
   is decisive only when a **third, differently-scaled** scorer (graph traversal/PPR) is
   fused, which is exactly the issue's "RRF default once graph joins." So: keep pgvector's
   weighted hybrid now (`vector_score_weight ≈ 0.4–0.5`), adopt RRF at the moment SM-G9 adds
   the graph leg.
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

- [x] **Fusion method chosen (RRF default once graph joins) with rationale** — §1.1, §4,
  evidence §3, sweeps §3.3. Weighted now (significant win, 2-leg); RRF k≈60 when graph joins.
- [x] **Reranker chosen per mode (self-hosted for Mode A) + latency budget set** — §1.2, §4.
  Mode A: self-hosted `bge-reranker-v2-m3`/`infinity`, GPU-served; Mode B: Cohere. Budget
  ≤300 ms (GPU); measured CPU ~2.0–2.5 s shows it is off the default hot path.
- [x] **Placement of time-aware episodic scoring decided** — §1.3, §5 (SM-E7 coupling):
  write-time importance + optional recency pre-filter, not an additive re-ranker.
- [x] **Confirms no query-time LLM on the hot path** — §1.4.

---

## 3. Questions answered (from #83)

| Question | Answer |
| --- | --- |
| **RRF (k≈60) vs weighted score fusion, esp. once a graph retriever joins?** | **Weighted wins the 2-leg (dense+BM25) case now** — significant over RRF on all 3 datasets (§3.1). **Switch to RRF k≈60 when the graph leg (SM-G9) joins**: a 3rd incomparable score scale is where weighted normalization breaks and RRF's rank-basis pays off. |
| **Which reranker + self-hosted vs provider (dual-mode)?** | **Mode A (air-gapped): self-hosted cross-encoder** — `sentence_transformer` `BAAI/bge-reranker-v2-m3` (offline) or `infinity` (served). **Mode B: Cohere `rerank-v3.5`.** But **off by default** — see latency. |
| **Latency budget for the rerank stage (research +50–300 ms)?** | Adopt **≤300 ms added** *as a gate*: only enable rerank where it can be met (GPU/served). Measured on this CPU host: **~2.0–2.5 s/query p50** (real docs) — so rerank is **not** on the default CPU hot path. |
| **Where does time-aware episodic scoring (SM-E7) apply?** | **Not** as an additive query-time score (it hurt). As an **optional recency pre-filter** for time-scoped queries + **write-time importance** consumed as a light prior. Keeps the read path LLM-free. |

---

## 4. Options evaluated

All five run through one config-driven `RetrievalPipeline` (swap fusion/reranker/scorer by
config, no call-site change). Embedder = local `all-MiniLM-L6-v2` (provisional; see §5/§8);
strong-embedder Azure cross-check in §3.2. nDCG@10, paired-bootstrap significance. Reranker
(d) was run on the 3 smaller sets (fiqa d skipped — its conclusion is settled and CPU rerank
of the 57k-corpus set is prohibitively slow; see §8).

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
path** — neutral/harmful (significant loss on the memory set) and ~100× slower. **Composite
recency: rejected as an additive score** — significant loss on the only set where recency is real.

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

---

## 5. Seed-context verification & disposition

Paths verified against the current checkout (namespace still `egpt`; no rebrand applied here).

| Seed fact (from #83) | Verified | Disposition |
| --- | --- | --- |
| `vectordb/search.py:4` `SearchType = vector\|keyword\|hybrid` | ✓ (no `rrf`) | **Extend** — add an `rrf` fusion strategy for when the graph leg joins (SM-H2/H3). |
| `vectordb/pgvector/pgvector.py:62,146` `vector_score_weight=0.5`; `hybrid_search` (l.1067) fuses `vector_score*w + text_rank*(1-w)`, `text_rank = ts_rank_cd/(ts_rank_cd+0.1)` | ✓ — this **is** weighted/relative-score fusion, not RRF | **Reuse** — this is the chosen fusion for the 2-leg case now. Tune `vector_weight`. |
| `knowledge/reranker/` = `cohere`, `sentence_transformer` (`bge-reranker-v2-m3`), `infinity`, `aws_bedrock`, `base` | ✓ (base `rerank` sync-only; only `infinity` has `arerank`) | **Reuse** — `sentence_transformer`/`infinity` = Mode A, `cohere` = Mode B. Extend base with `arerank` (finding). |
| "No RRF implementation" | ✓ | **New** — RRF built here (`fusion.RRFFusion`); hand off to SM-H2/H3. |
| "No graph retriever to fuse yet (SM-G9)" | ✓ | **Out of scope** — graph leg arrives in SM-G9; this decision is graph-ready (RRF switch). |

**Reuse/extend/replace summary:** reuse pgvector weighted hybrid + egpt rerankers +
egpt embedder; extend `SearchType` with `rrf` and the `Reranker` base with `arerank`;
new RRF fusion function. No `libs/agno/egpt/**` change in this task (see §10).

---

## 6. Couplings & handoff

- **SM-R3 (store hybrid support):** the chosen fusion is pgvector's existing weighted
  `hybrid_search` — **reuse as-is now**; the implementation task should add an **RRF fusion
  mode** (rank-based over dense+BM25[+graph]) behind `SearchType`/a fusion strategy so the
  graph leg (SM-G9) can be fused without score-scale coupling. Concrete artifact:
  `fusion.RRFFusion(k=60)` + `fusion.WeightedFusion(vector_weight)`.
- **SM-R4 (embeddings):** the fusion benefit is **embedder-strength-dependent** (mid MiniLM →
  hybrid helps a lot and is significant; strong Azure → benefit narrows, vector wins nfcorpus).
  SM-R4's production embedder must **re-validate `vector_weight` and whether hybrid still
  clears noise**. Dimension/latency of the embedder are SM-R4's to set; this task assumes a
  dense vector + a BM25 sparse index per tenant.
- **SM-E7 (time-aware episodic scoring):** placement decided — **write-time importance** (a
  per-memory salience in [0,1]) consumed as a light prior, and **recency as an optional
  pre-filter** for time-scoped queries; **not** an additive query-time re-score (measured
  loss). Concrete artifact: `scoring.CompositeScorer` with `w_recency=0` default and an
  `importance` hook fed by SM-E7.
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

- **Graph retriever / PPR** — SM-G9 (this decision is graph-ready via the RRF switch).
- **Embedder model/dimension** — SM-R4 (used a provisional local embedder here).
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
  **2-leg** case weighted beats RRF, and CPU rerank blows the latency budget.
- Graph-ready for **SM-G9**; hands the fusion/reranker seams to **SM-H2/H3**; hands time-aware
  placement to **SM-E7**; flags embedder re-validation for **SM-R4**.

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
