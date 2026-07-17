# SM-R4 — Decision: embedding model for memory

**Status:** Proposed (pending SM-R1 ADR ratification of the provisional targets)
**Issue:** [aicoe-tracker#79](https://github.com/Saturam-Inc/aicoe-tracker/issues/79)
**Depends on:** SM-R1 (#76 ADR), SM-R3 (#78 vector store) · **Feeds:** SM-V2
**Decisions covered:** D7 (embedder choice) + D8 (re-embedding / versioning)

Every option was **implemented and measured** through its egpt embedder
abstraction on good-quality, independent data — no choice is made from cited
numbers. This supersedes the first attempt (PR #5), whose eval set was too small
and single-authored to separate close models; see §3.

> **Reproduce:** see [README.md](README.md). Harness in `harness/`, eval data in
> `data/`, measured results in `results/`.

---

## 1. Decision

| | Mode A (self-hostable / air-gapped VPC) | Mode B (provider allowed) |
|---|---|---|
| **Default embedder** | **`thenlper/gte-base`** (768) via `FastEmbedEmbedder` (ONNX) or `SentenceTransformerEmbedder` | **Azure `text-embedding-3-large`** truncated to **1024** via `AzureOpenAIEmbedder` |
| **Dimension** | **768** → `vector(768)` | **1024** → `vector(1024)` |
| **License / offline** | MIT · fully offline | Azure (proprietary) · not offline |
| **Alternative (statistically equal)** | `BAAI/bge-base-en-v1.5` (768) — tied on the memory set, MIT, equal ONNX support | text-embedding-3-large @ 3072 (no significant gain, exceeds pgvector `vector` limit) |

**One logical embedder, two configured backends.** The memory subsystem depends
only on the `Embedder` abstraction; Mode is a deployment-time configuration, not a
code fork. Mode A is the default and the only air-gapped-capable option.

**Why 768 (not 384, which PR #5 chose):** on a properly powered, independent eval,
768-dim models **significantly** beat the 384-dim `bge-small` on the memory set
(p=0.037) *and* on external SciFact (p=0.0096). PR #5 ranked those models within
its noise and picked 384; that was not supported (§3). Going beyond 768 to 1024
(`bge-large`) gives **no statistically significant** quality gain while tripling
latency (187 ms vs 60 ms) — so 768 is the sweet spot.

**Why gte-base (with bge-base as the equal alternative):** on the memory set the
two are a statistical tie (gte 0.850 vs bge 0.842, paired p=0.23), but gte-base is
**significantly better on external SciFact** (paired p=0.045), **faster** on CPU
(49 ms vs 60 ms p50), and equal on license (MIT) and ONNX/`fastembed` support — so
it dominates bge-base on the measured evidence with no downside. bge-base is a
fully acceptable equal alternative (identical on the memory set); pick it if
ecosystem familiarity is weighted over the external-set edge.

**Why 1024 for Mode B:** text-embedding-3-large @ 1024 is statistically tied with
its full 3072 (0.877 vs 0.882) and beats every open model, while the full 3072
**exceeds pgvector's 2000-dim `vector` limit** (§5). 1024 keeps Mode B on the same
column family as Mode A.

---

## 2. Provisional targets (assumptions for SM-R1 to ratify)

SM-R1 is authored last, so these are explicit assumptions this decision is sized
against; if R1 sets different numbers, revisit via the switch thresholds (§6).

| Target | Provisional value | Basis |
|---|---|---|
| Scale | **10M memory vectors** | user-stated |
| Language | **English-only** | user-stated; multilingual is a documented switch (§6) |
| Quality floor | **nDCG@10 ≥ 0.84** on the memory set | met by 768-dim + all Mode B |
| Query latency | **p95 ≤ 50 ms** (Mode A, in-VPC CPU) | gte-base ST p50 = 49 ms on a *loaded* dev CPU; the ONNX runtime and a quiet host give headroom — flagged for R1 (§3 caveat) |
| Storage budget | **≤ ~80 GB** for the vector tier at 10M | 768d = 80 GB float32 / 40 GB halfvec (§4) |
| Offline capability | **required** for Mode A | air-gapped deployment |

---

## 3. Retrieval quality (measured, with significance)

**Primary eval — independent synthetic memory set:** 225 memories / **180
queries** across 15 diverse personas, authored by an LLM (Azure gpt-5.4) in
separate passes and **blind-labeled with graded relevance by a separate judge
pass** (not the harness author). Generator/judge agreement on intended memories:
99% (174/176). Built by `data/generate_synthetic_v2.py` + `data/label_relevance.py`.

Ranking by **graded nDCG@10** with **95% bootstrap CIs** and **paired-bootstrap
significance** (`harness/stats.py`); "~tie" = the gap to the next model is not
significant at α=0.05.

| Model | Mode | Dim | nDCG@10 [95% CI] | CPU p50 | vs next |
|---|---|---:|---|---:|---|
| text-embedding-3-large | B | 3072 | 0.882 [0.858, 0.904] | ~1.7 s* | > (p=0.03) |
| text-embedding-3-large | B | **1024** | 0.877 [0.853, 0.900] | ~1.5 s* | ~tie |
| text-embedding-3-small | B | 1536 | 0.869 [0.842, 0.894] | ~1.6 s* | ~tie |
| bge-large | A | 1024 | 0.856 [0.830, 0.881] | 187 ms | ~tie |
| gte-base | A | 768 | 0.850 [0.818, 0.879] | 49 ms | ~tie |
| text-embedding-3-small | B | 512 | 0.849 [0.819, 0.877] | ~1.3 s* | ~tie |
| **bge-base** | A | **768** | 0.842 [0.812, 0.869] | 60 ms | **> bge-small (p=0.037)** |
| bge-small (ONNX/ST) | A | 384 | 0.825 [0.793, 0.855] | 11 / 28 ms | ~tie |
| e5-base | A | 768 | 0.823 [0.790, 0.854] | 52 ms | ~tie |
| minilm-l6 | A | 384 | 0.804 [0.769, 0.837] | 10 ms | — |

\* Mode B latency is **WAN round-trip** from a dev box to Azure, **not** model
compute; in-VPC/Foundry it is far lower. Re-measure in-environment.

**External cross-check — BEIR/SciFact** (capped 1007-doc slice, human-labeled):
independently confirms the 768>384 finding — **bge-base 0.846 > bge-small 0.818,
p=0.0096**. Rank agreement with the memory set (open models, Spearman) = 0.54;
both sets place the 384-dim models near the bottom and minilm last.

**Latency caveats (honest):** measurements are CPU (device pinned via the harness
— the deployment is CPU/air-gapped, and this avoids a Mac-GPU/MPS artifact) on a
**heavily loaded shared host** (load avg ~15–80 during runs), so absolute
latencies are inflated; treat them as *relative*. The ONNX (`fastembed`) runtime
was ~2.5× faster than PyTorch at identical quality (bge-small: 11 vs 28 ms), so
gte-base via ONNX is the recommended Mode A runtime and the path most likely to
meet the 50 ms p95 target on a quiet in-VPC host.

**What changed from PR #5:** the old set (54 docs / 32 queries, single-authored,
binary labels) put bge-base/bge-small/gte-base within noise and picked bge-small@384.
The new set (180 independent queries, graded, blind-labeled) + significance testing
shows 768 **significantly** beats 384 on two datasets. The methodology fix flipped
the Mode A choice — the reason this redo was warranted.

---

## 4. Dimension → storage/index cost (coupling to SM-R3)

Dimension is what SM-R3's store must physically hold. At the provisional **10M**
scale (`results/storage_cost_10M.json`; HNSW index ≈1.8× raw — SM-R3 measures the
real index):

| Dim | float32 `vector` total | float16 `halfvec` total | Fits `vector` (≤2000)? |
|---:|---:|---:|:---:|
| 384 (PR #5 pick — rejected on quality) | 40 GB | 20 GB | yes |
| **768** (Mode A pick) | **80 GB** | 40 GB | yes |
| **1024** (Mode B pick) | **107 GB** | 53 GB | yes |
| 1536 | 160 GB | 80 GB | yes |
| 3072 (3-large full — rejected) | — | 160 GB | **no — exceeds 2000** |

Moving 384→768 for the significant quality gain **doubles** Mode A storage
(40→80 GB); still within budget and `halfvec` (40 GB) recovers it if SM-R3 wants,
at a small recall cost to validate there. **3-large @ 3072 does not fit the
`vector` type** and would force `halfvec`/truncation — the concrete reason Mode B
is truncated to 1024.

---

## 5. Seed-context verification & disposition

Issue #79 seed facts, verified against the current (post-rebrand) code:

| Seed fact (issue) | Verified in current code | Disposition |
|---|---|---|
| `knowledge/embedder/` ships **18 embedders** | `libs/agno/egpt/knowledge/embedder/` — 18 embedder modules present (namespace rebranded `agno`→**`egpt`**) | reuse |
| Self-hostable set: `vllm`, `ollama`, `huggingface`, `sentence_transformer`, `fastembed`, `openai_like` | all 6 present | Mode A uses `fastembed`/`sentence_transformer`; `vllm`/`ollama` = in-VPC server alternatives |
| `PgVector` fixed-dim; pgvector limit **2000** dims, **halfvec 4000** | pgvector-extension constraints (not egpt code), as issue states; drives §4 | honored (768/1024 fit `vector`) |

No orphan seed facts. Two adapter findings surfaced while benchmarking (not
committed to core) — see `findings/`: `SentenceTransformerEmbedder` lacks
`trust_remote_code`/`device` passthrough (patch saved; also used to pin CPU);
`HuggingfaceCustomEmbedder` is remote (HF Inference API), **not** air-gapped
despite being listed self-hostable — do not use it for Mode A.

---

## 6. Switch thresholds (when to revisit)

- **Quality shortfall:** if measured memory-set nDCG@10 drops below 0.84 for
  Mode A → the tied `bge-base` (768) is the drop-in equal, before considering
  `bge-large` (1024, no significant gain, 3× latency).
- **Latency miss:** if gte-base can't hit p95 ≤ 50 ms in-VPC → use the ONNX
  (`fastembed`) runtime first; only then step down to `bge-small`@384 (accepting
  the significant quality loss) or relax the target with R1.
- **Multilingual requirement:** English-only assumed. If memories become
  multilingual → switch Mode A to `bge-m3`/`multilingual-e5-base` and re-benchmark
  (expect a storage step).
- **Scale/storage pressure** (≫10M): move the 768d store to `halfvec` (40 GB) and
  validate recall; then PQ/quantization in SM-R3.
- **Mode B latency/cost** unacceptable in-VPC → fall back to the Mode A model for
  Mode B too (single shared embedder), accepting ~0.03 nDCG headroom.

---

## 7. Couplings & handoff

- **→ SM-R3 (#78):** chosen dimension drives store cost — Mode A `vector(768)`
  (80/40 GB @ 10M), Mode B `vector(1024)` (107/53 GB). Both fit the `vector` type;
  `halfvec` optional. SM-R3 sizes the real HNSW index.
- **→ SM-V2:** ships the concrete embedder config (model id + dim + normalization
  = cosine) both modes consume.
- **Re-embedding / versioning (D8):** every memory vector carries an
  `embedding_version` tag, e.g. `gte-base/d768/cos/v1` (model·dim·metric·rev),
  read from one config location so **write and query use the same version**
  (acceptance criterion). Model/dim change → register new version, stand up a new
  column/index at the new dim (never mix dims), **dual-write**, **backfill** by
  re-embedding retained raw memory text in batches, cut queries over at a coverage
  threshold, drop the old index after parity. Backfill needs only the source text,
  so a model change is always recoverable — keep raw memory text durable.

---

## 8. Out of scope & boundaries

- Does **not** choose the vector store or its scale/latency switch thresholds —
  that is **SM-R3 (#78)**. This task only sizes the *dimension* the store must hold.
- Does **not** decide multi-tenancy, the temporal-graph tier, or decay/segmentation
  (SM-R5/R6/R10). No production wiring — implementation is a later SM-C* task.

---

## 9. Consistency with dependencies & siblings

- **Depends on SM-R1 (#76 ADR)** — not yet ratified, so §2 targets are provisional
  assumptions flagged for it. Consistent with D8 (vector tier first-class).
- **Couples to SM-R3 (#78)** — dimension/`halfvec` choices in §4 are stated for the
  store decision; no conflict (both dims fit pgvector `vector`).
- Consistent with the seed's existing embedder abstraction (reuse, not replace).

---

## 10. Grounding & reproducibility

- Grounded in the memory research doc (`docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md`,
  per the issue) and the seed embedder inventory (§5).
- **Reproducible:** eval built by `data/generate_synthetic_v2.py` +
  `data/label_relevance.py` (committed JSON is the artifact of record; prompts +
  scripts committed for provenance). Runs via `harness/run.py`; analysis via
  `harness/analyze.py`/`stats.py`. All measured results committed under `results/`.
- **Full BEIR** (uncapped SciFact/NFCorpus/ArguAna) reproduces with
  `--dataset beir:<name>` (drop `--beir-capped`) on a non-loaded host; capped
  slices were used here because the shared host was CPU-saturated by other work.

---

## 11. Acceptance criteria (issue #79)

- [x] **Embedder chosen for Mode A (self-hostable) and Mode B** — gte-base
  (768; bge-base an equal alternative) for Mode A; Azure text-embedding-3-large
  (1024) for Mode B. §1, §3.
- [x] **Dimension fits the pgvector store (with halfvec if needed)** — 768 and 1024
  fit `vector` (≤2000); halfvec optional; full 3072 rejected for exceeding the limit. §4.
- [x] **Re-embedding / embedding-version strategy defined** — `embedding_version`
  tag + dual-write + text-based backfill. §7.
- [x] **Consistent embedder for write and query paths** — one version from a single
  config source; sync/async paths validated to produce matching vectors. §3, §7.

## 12. Questions answered (issue #79)

| Question | Answer |
|---|---|
| Which self-hostable embedder for Mode A (`vllm`/`sentence_transformer`/`huggingface`/`fastembed`)? Quality vs footprint? | `fastembed` (ONNX) or `sentence_transformer` running **gte-base** (768; bge-base-en-v1.5 an equal alternative). Measured best quality-per-footprint at the significant 768 tier; `vllm` is the option for a heavier in-VPC GPU server; `huggingface` adapter is remote (not air-gapped). |
| One shared model across modes, or a provider for Mode B? | Provider (Azure 3-large@1024) for Mode B where allowed — it measurably beats the open models. A single shared gte-base is the documented fallback if Mode B latency/cost is unacceptable in-VPC. |
| Dimension vs pgvector limits; halfvec needed? | 768 (A) and 1024 (B) fit `vector` (≤2000) — **halfvec not required**, but halves storage if SM-R3 wants it. Full 3072 would require halfvec/truncation → rejected. |
| Re-embedding cost/strategy when the model changes? | `embedding_version` tag + dual-write + text-based backfill; recoverable from retained source text. §7. |
