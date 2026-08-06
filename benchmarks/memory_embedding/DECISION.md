# SM-R4 — Decision: embedding model for memory

**Status:** Proposed (revised after QC review; pending SM-R1 ADR ratification of the
provisional targets)
**Issue:** [aicoe-tracker#79](https://github.com/Saturam-Inc/aicoe-tracker/issues/79)
**Depends on:** SM-R1 (#76 ADR), SM-R3 (#78 vector store) · **Feeds:** SM-V2
**Decisions covered:** D7 (embedder choice) + D8 (re-embedding / versioning)

Every option was **implemented and measured** through its egpt embedder abstraction
on an independent, significance-tested eval. This revision addresses the QC review
of the prior version — the eval was strengthened to test episodic/temporal/
contradiction retrieval, multiplicity correction was added, and **the corrected
evidence changed the choice** (see §3, §13).

> **Reproduce:** see [README.md](README.md). Harness in `harness/`, eval data in
> `data/`, measured results in `results/`.

---

## 1. Decision

| | Mode A (self-hostable / air-gapped VPC) | Mode B (provider allowed) |
|---|---|---|
| **Default embedder** | **`BAAI/bge-base-en-v1.5`** (768) via `SentenceTransformerEmbedder` (or `FastEmbedEmbedder`/ONNX) | **Azure `text-embedding-3-large`** truncated to **1024** via `AzureOpenAIEmbedder` |
| **Dimension** | **768** → `vector(768)` | **1024** → `vector(1024)` |
| **License / offline** | MIT · fully offline | Azure (proprietary) · not offline |
| **Alternative (statistically equal on memory)** | `bge-large` (1024) or `e5-base` (768) — tie bge-base; `gte-base` if out-of-domain retrieval dominates (it leads on SciFact) | — |

**One logical embedder, two configured backends.** The memory subsystem depends only
on the `Embedder` abstraction; Mode is a deployment-time config choice.

**Why bge-base@768 (this is a change from the pre-review draft):** on the corrected,
significance-tested memory eval, bge-base **significantly** beats bge-small@384
(Holm p=0.002) and gte-base (Holm p=0.016), and ties the heavier bge-large@1024 and
e5-base@768 (§3). It is the top-scoring model on the memory-representative set, MIT,
and ONNX-capable. gte-base — the pre-review pick — is now *significantly behind*
bge-base on the memory set, though it leads on out-of-domain SciFact, so it is kept
as the alternative for retrieval over non-memory-style text.

**Why 768 not 384:** the 768 tier significantly beats 384 (bge-small) on the memory
set *and* on human-labeled SciFact (§3). **Why not 1024:** bge-large gives no
significant gain over bge-base and is ~3× slower.

**Mode B is now OPTIONAL, not a quality upgrade.** On the corrected eval, open
bge-base (0.911) **statistically ties** the best Azure model (0.899, Holm n.s.) — the
provider's apparent advantage in the prior version was an artifact of lenient
labeling. Use Mode B only where self-hosting is undesirable; expect no quality gain
over Mode A. Within Mode B, 3072 and 1024 are a statistical tie (p=0.82), so 1024 is
chosen to fit the pgvector `vector` 2000-dim limit (§4).

---

## 2. Provisional targets (assumptions for SM-R1 to ratify)

| Target | Provisional value | Basis / status |
|---|---|---|
| Scale | **10M memory vectors** | user-stated |
| Language | **English-only** | user-stated; multilingual is a documented switch (§6) |
| Quality floor | **nDCG@10 ≥ 0.88** on the memory set | met by all 768-dim + Mode B |
| Query latency | **p95 ≤ 50 ms** (Mode A, in-VPC CPU) | **NOT met by 768-dim on CPU** — see §3 / #4. Open gate for R1: relax target, add GPU/vLLM or query-embed cache, or accept 384. |
| Storage budget | **≤ ~80 GB** vector tier at 10M | 768d = 80 GB float32 / 40 GB halfvec (§4) |
| Offline capability | **required** for Mode A | air-gapped deployment |

---

## 3. Retrieval quality (measured, significance-tested)

**Primary eval — independent, episodic memory set (v2, re-labeled after review):**
225 memories / 199 labeled queries / 15 diverse personas. Content authored by an LLM
(Azure gpt-5.4) in separate passes; **mode-aware BLIND graded labeling** by a separate
judge pass, then a **provenance audit** that forces hard-negative traps and superseded
facts to grade 0, keeps strict single-answer modes (hard_negative/temporal/
contradiction) to the correct answer only, and drops queries the judge won't back
(11 dropped, 3 corrections; audit verified: 0 trap/superseded leaks). Modes now
include **contradiction/belief-revision (23)** and time-specific **temporal (41)**.
Built by `data/generate_synthetic_v2.py` + `data/label_relevance.py`.

Ranking by **graded nDCG@10** with 95% bootstrap CIs (`harness/stats.py`). Pairwise
gaps use paired-bootstrap tests; the decision's family is **Holm-corrected**
(`holm_adjust`) so "significant" controls the family-wise error rate.

| Model | Mode | Dim | nDCG@10 [95% CI] | CPU p50 | CPU p95 |
|---|---|---:|---|---:|---:|
| **bge-base** | A | **768** | **0.911** [0.888, 0.934] | 198 ms | 482 ms |
| bge-large | A | 1024 | 0.905 [0.881, 0.928] | 623 ms | 1313 ms |
| e5-base | A | 768 | 0.901 [0.876, 0.924] | 205 ms | 465 ms |
| text-embedding-3-large | B | 1024 | 0.900 [0.876, 0.923] | net | net |
| text-embedding-3-large | B | 3072 | 0.899 [0.875, 0.923] | net | net |
| text-embedding-3-small | B | 1536 | 0.898 | net | net |
| nomic-embed-v1.5 `[patched]` | A | 768 | 0.898 [0.873, 0.921] | 255 ms | 609 ms |
| text-embedding-3-small | B | 512 | 0.898 | net | net |
| gte-base | A | 768 | 0.891 [0.866, 0.915] | 195 ms | 447 ms |
| bge-small (ST / ONNX) | A | 384 | 0.877 [0.850, 0.903] | 93 / 59 ms | 196 / 192 ms |
| minilm-l6 | A | 384 | 0.861 [0.833, 0.888] | 43 ms | 101 ms |

**Holm-corrected significance (decision family):**
- **bge-base > bge-small (768>384): Holm p=0.002 — significant** (now on the memory set alone, resolving the multiplicity concern).
- **bge-base > gte-base: Holm p=0.016 — significant** (bge-base is the pick, not a coin-flip).
- bge-base vs e5-base (p=0.29), vs bge-large (p=0.43), **vs azure-3-large (p=0.21)** — all ties.
- azure-3-large 3072 vs 1024: p=0.82 — tie.

**External cross-check — full uncapped BEIR/SciFact** (human-labeled, all judged
queries): confirms **768>384** — bge-base 0.740 > bge-small 0.713. gte-base leads on
SciFact (0.762), i.e. gte is stronger on out-of-domain scientific text while bge-base
leads on the memory-representative set — the basis for keeping gte as the alternative.
(nomic on full SciFact still running; folds in when done.)

**Latency (honest — this is the #4 gap).** Numbers are per-item CPU embedding. **No
768-dim model meets the p95 ≤ 50 ms target**, even on an idle host (idle gte p95 ≈
276–316 ms; bge-base similar); ONNX does **not** rescue it — measured gte-ONNX is
*slower* per-item than PyTorch (p50 152 vs 87 ms), quality-identical (parity 0.0000).
Only **384-dim bge-small via ONNX meets the target idle (p50 13 ms, p95 19 ms)**.
So Mode A carries a real **quality-vs-latency tension**: bge-base@768 for quality, or
bge-small@384 for the latency budget (significantly lower quality). R1 must ratify the
target or a mitigation (GPU/vLLM for query embed, query-embed cache, or a relaxed
budget). Latency here is measured on a shared dev CPU and is not an in-VPC number.

---

## 4. Dimension → storage/index cost (coupling to SM-R3)

At the provisional **10M** scale (`results/storage_cost_10M.json`; HNSW ≈1.8× raw):

| Dim | float32 `vector` | float16 `halfvec` | Fits `vector` (≤2000)? |
|---:|---:|---:|:---:|
| 384 (latency-safe fallback) | 40 GB | 20 GB | yes |
| **768** (Mode A pick) | **80 GB** | 40 GB | yes |
| **1024** (Mode B pick) | **107 GB** | 53 GB | yes |
| 3072 (3-large full — rejected) | — | 160 GB | **no — exceeds 2000** |

3-large @ 3072 does not fit the `vector` type; truncating Mode B to 1024 (a quality
tie with 3072) keeps it on the same column family as Mode A.

---

## 5. Seed-context verification & disposition

| Seed fact (issue #79) | Verified in current code | Disposition |
|---|---|---|
| `knowledge/embedder/` ships **18 embedders** | `libs/agno/egpt/knowledge/embedder/` — 18 present (namespace rebranded `agno`→**`egpt`**) | reuse |
| Self-hostable set: `vllm`/`ollama`/`huggingface`/`sentence_transformer`/`fastembed`/`openai_like` | all 6 present | Mode A uses `sentence_transformer`/`fastembed`; `vllm`/`ollama` = in-VPC server options |
| pgvector limit **2000** dims, **halfvec 4000** | pgvector-extension constraints (not egpt code), as issue states | honored (768/1024 fit `vector`) |

No orphan seed facts. Adapter findings (not committed to core) in `findings/`:
`SentenceTransformerEmbedder` lacks `trust_remote_code`/`device` passthrough (patch
saved; used for nomic and for CPU pinning); `HuggingfaceCustomEmbedder` is remote (HF
Inference API), **not** air-gapped despite being listed self-hostable.

---

## 6. Switch thresholds (when to revisit)

- **Latency budget is firm (p95 ≤ 50 ms, no mitigation):** drop to **bge-small@384**
  (ONNX; idle p95 ≈ 19 ms), accepting the significant quality loss — or add GPU/vLLM
  query embedding / a query-embed cache and keep bge-base@768.
- **Quality shortfall** (memory nDCG@10 < 0.88): the tied `bge-large`(1024)/`e5-base`(768)
  are drop-in equals before anything larger.
- **Out-of-domain / non-memory text dominates:** prefer `gte-base` (leads on SciFact).
- **Multilingual requirement:** switch to `bge-m3`/`multilingual-e5-base`; re-benchmark.
- **Scale/storage pressure** (≫10M): move to `halfvec` (40 GB) and validate recall.
- **Mode B:** only adopt if self-hosting is undesirable — it is a quality *tie* with
  Mode A bge-base, not an upgrade.

---

## 7. Couplings & handoff

- **→ SM-R3 (#78):** dimension drives store cost — Mode A `vector(768)` (80/40 GB @10M),
  Mode B `vector(1024)` (107/53 GB). Both fit `vector`; `halfvec` optional. **The p95
  latency gap (§3) must be reconciled with SM-R3's retrieval hot-path budget** — query
  embedding is on that path.
- **→ SM-V2:** ships the embedder config (model id + dim + cosine normalization).
- **Re-embedding / versioning (D8):** every vector carries an `embedding_version` tag,
  e.g. `bge-base-en-v1.5/d768/cos/v1` (model·dim·metric·rev), read from one config so
  write and query use the same version. Model/dim change → register new version, new
  column/index at the new dim, dual-write, backfill by re-embedding retained raw memory
  text, cut over at a coverage threshold, drop the old index after parity. Recoverable
  from source text — keep raw memory text durable.

---

## 8. Out of scope & boundaries

- Does **not** choose the vector store or its scale/latency switch thresholds — that is
  **SM-R3 (#78)**; this task only sizes the dimension it must hold.
- Does **not** decide multi-tenancy, temporal-graph, decay/segmentation (SM-R5/6/10).
  No production wiring — implementation is a later SM-C* task.

---

## 9. Consistency with dependencies & siblings

- **Depends on SM-R1 (#76 ADR)** — not yet ratified, so §2 targets (esp. the p95 gap)
  are flagged for it. Consistent with D8 (vector tier first-class).
- **Couples to SM-R3 (#78)** — §4 dimension/halfvec stated for the store decision; no
  conflict (both dims fit pgvector `vector`).
- Reuses the seed embedder abstraction (no fork).

---

## 10. Grounding & reproducibility

- Grounded in `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` (per the issue): the
  five episodic markers (raw events, recency, segmentation, temporal-order) motivated
  the re-labeled eval's temporal + contradiction modes.
- **Reproducible:** eval built by `data/generate_synthetic_v2.py` + `data/label_relevance.py`
  (committed JSON is the artifact of record; prompts/scripts committed). Runs via
  `harness/run.py`; significance via `harness/stats.py` (`holm_adjust`) / `analyze.py`.
- Full BEIR reproduces with `--dataset beir:<name>` (drop `--beir-capped`).

---

## 11. Acceptance criteria (issue #79)

- [x] **Embedder chosen for Mode A + Mode B** — bge-base-en-v1.5 (768) Mode A; Azure
  text-embedding-3-large (1024) Mode B (optional). §1, §3.
- [x] **Dimension fits the pgvector store (halfvec if needed)** — 768 & 1024 fit
  `vector`; halfvec optional; 3072 rejected for exceeding the limit. §4.
- [x] **Re-embedding / embedding-version strategy defined** — §7.
- [x] **Consistent embedder for write and query paths** — one version from one config;
  sync/async paths validated to match. §7.

## 12. Questions answered (issue #79)

| Question | Answer |
|---|---|
| Which self-hostable embedder for Mode A? Quality vs footprint? | **bge-base-en-v1.5** (768) via `sentence_transformer`/`fastembed`; significantly best on the memory set, MIT. `vllm` for a heavier in-VPC GPU server; `huggingface` adapter is remote (not air-gapped). |
| One shared model across modes, or a provider for Mode B? | Mode B provider is **optional** — it only ties open bge-base on quality. A single shared bge-base across both modes is the reasonable default unless a managed provider is specifically wanted. |
| Dimension vs pgvector limits; halfvec? | 768 (A) and 1024 (B) fit `vector` (≤2000); halfvec optional. Full 3072 rejected. §4. |
| Re-embedding cost/strategy? | `embedding_version` tag + dual-write + text-based backfill. §7. |

## 13. Review response (QC review of the prior version)

| # | Comment | Resolution |
|---|---|---|
| 1 | Eval didn't test episodic/temporal/contradiction; traps mislabeled | Re-labeled: mode-aware strict rubric + contradiction mode + provenance audit (traps/superseded→0, inversions dropped). Verified 0 leaks. **Changed the result** (§3). |
| 2 | No multiplicity correction (768>384 fails Holm) | Added `holm_adjust`; on the corrected eval 768>384 now survives Holm (p=0.002) on the memory set. |
| 3 | "1024 tied with 3072" inverted | On the corrected data it **is** a tie (p=0.82); 1024 chosen for the pgvector fit. §1. |
| 4 | p95 asserted, not met; ONNX unmeasured | Measured: 768 misses p95≤50 on CPU even idle; gte-ONNX is slower, not faster (quality-identical). Stated as an open R1 gap. §2/§3. |
| 5 | gte-vs-bge coin-flip; e5 omitted | On corrected data bge-base **significantly** beats gte (Holm 0.016). e5 is included (ties bge-base). (Reviewer's "e5 ties gte" did not reproduce — e5 is behind gte on the old set; moot now.) |
| 6 | nomic absent from v2 | nomic run on v2: 0.898, mid-pack, ties Mode B, below bge-base. §3. |
| 7 | SciFact was a capped smoke slice | Ran full uncapped SciFact; 768>384 confirmed (bge-base 0.740 > bge-small 0.713). |
| 8 | "99% agreement" overstated de-circularization | Removed; de-circularization is now the provenance audit (drops/corrections), not a self-consistency stat. §3. |
| 9 | Add Holm/BH to stats | Added `holm_adjust` in `harness/stats.py`. |
