# SM-R4 — Decision: embedding model for memory

**Status:** Proposed (pending SM-R1 ratification of the provisional targets)
**Issue:** [aicoe-tracker#79](https://github.com/Saturam-Inc/aicoe-tracker/issues/79)
**Depends on:** SM-R3 (#78, vector store) · **Feeds:** SM-V2
**Decisions covered:** D7 (embedder choice) + D8 (re-embedding/versioning)

This decision is backed by a reproducible benchmark in this folder — every model
was actually exercised through its egpt embedder abstraction and measured; no
choice is made from cited numbers. See [`README.md`](README.md) to reproduce and
[`TEST_LOG.md`](TEST_LOG.md) for the run log.

---

## 1. Decision

| | Mode A (self-hostable / air-gapped VPC) | Mode B (provider allowed) |
|---|---|---|
| **Default embedder** | `FastEmbedEmbedder` running **`BAAI/bge-small-en-v1.5`** (ONNX) | `AzureOpenAIEmbedder` running **`text-embedding-3-large`** |
| **Dimension** | **384** (`vector`, float32) | **1024** (Matryoshka-truncated) |
| **egpt class** | `egpt.knowledge.embedder.fastembed.FastEmbedEmbedder` | `egpt.knowledge.embedder.azure_openai.AzureOpenAIEmbedder` |
| **pgvector column** | `vector(384)` | `vector(1024)` (still ≤ 2000 limit) |
| **License / offline** | MIT · fully offline | Azure (proprietary) · not offline |

**One logical embedder, two configured backends.** The memory subsystem depends on
the `Embedder` abstraction only; Mode is a deployment-time configuration choice,
not a code fork. Mode A is the default and the only one usable in an air-gapped
VPC. Mode B is offered where a provider call is permitted and higher headroom is
wanted.

**Why 384 for Mode A, 1024 for Mode B?** bge-small at 384 dims matched the quality
of 768–1024d local models on both the memory set and the BEIR cross-check while
costing 2–4× less storage at 10M scale (§4). For Mode B the network already
dominates latency, so we spend dimension on quality but truncate 3072→1024 to
stay well under the pgvector `vector` 2000-dim limit and roughly halve storage
versus full-width, at a ≈0.4 nDCG-point cost (§3).

**A single memory store uses one Mode at a time.** Vectors written by a bge-small
(384) Mode A store are **not** comparable to text-embedding-3 (1024) Mode B
vectors — switching Mode is a re-embedding migration (§5), not a runtime toggle
per query.

---

## 2. Provisional targets (assumptions for SM-R1 to ratify)

SM-R1 (architecture ADR) is done last, so these are explicit assumptions this
decision is sized against. If R1 sets different numbers, revisit via the switch
thresholds in §6.

| Target | Provisional value | Basis |
|---|---|---|
| Scale | **10M memory vectors** | user-stated for this decision |
| Language | **English-only** | user-stated; multilingual is a documented switch (§6) |
| Retrieval quality floor | **nDCG@10 ≥ 0.90, Recall@5 ≥ 0.95** on the memory set | all shortlisted 384–1024d models clear it |
| Query embedding latency | **p95 ≤ 50 ms** (Mode A, in-VPC, CPU) | met by bge-small ONNX (§3 caveat on measurement) |
| Storage budget | **≤ ~50 GB** for the vector tier at 10M | 384d = 40 GB float32 / 20 GB halfvec (§4) |
| Offline capability | **required** for Mode A | air-gapped deployment |

---

## 3. Retrieval quality + latency (measured)

Synthetic memory set (54 memories, 32 labeled queries; paraphrase / temporal /
coreference / hard-negative). Full JSON in [`results/`](results/).

| Mode | Model | Dim | Recall@5 | nDCG@10 | p50 latency | Offline | License |
|---|---|---:|---:|---:|---:|:---:|---|
| B | text-embedding-3-large | 3072 | 1.000 | **0.959** | 1749 ms · net | no | Azure |
| B | **text-embedding-3-large** | **1024** | **1.000** | **0.947** | **1435 ms · net** | no | Azure |
| B | text-embedding-3-small | 1536 | 1.000 | 0.954 | 1759 ms · net | no | Azure |
| B | text-embedding-3-small | 512 | 0.969 | 0.931 | 1337 ms · net | no | Azure |
| A | bge-large-en-v1.5 | 1024 | 0.984 | 0.942 | 253 ms | yes | MIT |
| A | **bge-small-en-v1.5 (ONNX)** | **384** | **0.969** | **0.927** | **12.6 ms** | yes | MIT |
| A | bge-small-en-v1.5 (ST) | 384 | 0.969 | 0.927 | 88 ms | yes | MIT |
| A | bge-base-en-v1.5 | 768 | 1.000 | 0.923 | 94 ms | yes | MIT |
| A | nomic-embed-text-v1.5 `[patched]` | 768 | 0.938 | 0.910 | 257 ms | yes | Apache-2.0 |
| A | e5-base-v2 | 768 | 0.938 | 0.902 | 105 ms | yes | MIT |
| A | gte-base | 768 | 1.000 | 0.895 | 86 ms | yes | MIT |
| A | all-MiniLM-L6-v2 | 384 | 0.953 | 0.874 | 35 ms | yes | Apache-2.0 |

**BEIR/SciFact cross-check** (external validity, 859 docs / 50 queries): ranking
order tracked published MTEB (bge-base 0.863 ≈ gte-base 0.858 > e5-base 0.836 ≈
bge-small 0.832 > MiniLM 0.798 nDCG@10). bge-small stayed within ~3 points of the
768d models — the same pattern as the memory set.

**Latency caveats (measured honestly):**
- Mode A latencies are single-query CPU embedding on a dev laptop (macOS x86).
  The **ONNX runtime (`FastEmbedEmbedder`) is ~7× faster than the PyTorch/ST
  runtime for the same bge-small weights** (12.6 ms vs 88 ms p50) at identical
  quality — this is why the ONNX route is the Mode A default.
- Mode B p50 (~1.3–1.8 s) is **wide-area network round-trip** from the dev box to
  Azure, *not* model compute. In a co-located VPC/Foundry it is far lower; treat
  Mode B latency as network-bound and re-measure in-environment.

**sync vs async parity:** local models produced **byte-identical** vectors on the
`get_embedding` and `async_get_embedding` paths (max abs diff 0.0). The Azure
adapter showed tiny non-zero diffs (≤ 4e-3) because the provider's embeddings are
not bitwise-deterministic across calls — negligible for ranking, but noted.

---

## 4. Dimension → storage/index cost (coupling to SM-R3)

The chosen dimension is what SM-R3's vector store must physically hold. At the
provisional **10M-vector** scale (`results/storage_cost_10M.json`; HNSW index
approximated at ~1.8× raw — SM-R3 measures the real index):

| Dim | float32 `vector` total | float16 `halfvec` total | Fits `vector` (≤2000)? |
|---:|---:|---:|:---:|
| **384** (Mode A pick) | **40 GB** | 20 GB | yes |
| 512 | 53 GB | 27 GB | yes |
| 768 | 80 GB | 40 GB | yes |
| **1024** (Mode B pick) | **107 GB** | 53 GB | yes |
| 1536 | 160 GB | 80 GB | yes |
| 3072 (3-large full) | — | 160 GB | **no — exceeds 2000** |

**Consequences for SM-R3:**
- Mode A `vector(384)` fits comfortably; **`halfvec` is not required** but halves
  storage (20 GB) if SM-R3 wants it, at a small recall cost to validate there.
- **`text-embedding-3-large` at full 3072 dims does not fit pgvector's `vector`
  type (2000-dim limit)** and would force `halfvec` (4000-dim limit). Truncating
  to **1024** avoids that entirely and keeps Mode B on the same `vector` column
  family as Mode A. This is the concrete reason the Mode B default is truncated.

---

## 5. Re-embedding / embedding-version strategy (D8)

Embeddings are only comparable within one (model, dimension, normalization,
instruction-prefix) tuple. Any change to that tuple invalidates existing vectors.

**Version tag.** Every memory vector row carries an `embedding_version` string,
e.g. `bge-small-en-v1.5/d384/cos/v1` (model · dim · similarity · schema rev). The
active writer version lives in config. Query and write paths **must** use the
same version (acceptance criterion) — enforce by reading it from one place.

**Migration when the model/dimension changes** (new model, Mode A↔B switch, dim
change):
1. Register the new `embedding_version` and stand up a new column/index sized to
   the new dimension (never mix dims in one index).
2. **Dual-write** new memories under both versions during the transition.
3. **Backfill** existing memories by re-embedding raw memory text in batches
   (the source text is retained precisely so re-embedding is always possible).
   Backfill cost at 10M: bge-small ONNX ≈ hours on a few CPU cores; a provider
   backfill is an API-cost line item — budget it.
4. Cut queries over to the new version once backfill reaches a coverage
   threshold; keep the old index until parity is confirmed, then drop it.

**Cost note.** Re-embedding requires only the stored source text, never the old
vectors — so a model change is always recoverable. Keep raw memory text durable.

---

## 6. Switch thresholds (when to revisit this default)

- **Quality shortfall:** if measured nDCG@10 on the memory set drops below the
  0.90 floor for Mode A → step up 384→768 with **`bge-base-en-v1.5`** (best
  measured 768d, Recall@5 1.0) before considering anything larger.
- **Multilingual requirement appears:** English-only is assumed. If memories
  become multilingual → switch Mode A to **`bge-m3`** or **`multilingual-e5-base`**
  (768d) and re-benchmark; expect a storage step from 40→80 GB.
- **Storage/scale pressure** (≫10M, budget breached): move the 384d store to
  **`halfvec`** (20 GB) and validate recall; only then consider PQ/quantization
  in SM-R3.
- **Mode B latency/cost** unacceptable in-VPC: fall back to the Mode A model for
  Mode B too (single shared `bge-small`), accepting the ~0.03 nDCG headroom loss.
- **A stronger small model** clears the eval set by a clear margin on a re-run:
  swap it in — the abstraction makes this a config change, not a code change.

---

## 7. Acceptance criteria (issue #79)

- [x] **Embedder chosen for Mode A (self-hostable) and Mode B** — bge-small-en-v1.5
  (ONNX, 384) for Mode A; text-embedding-3-large (1024, Azure) for Mode B. §1.
- [x] **Dimension fits the pgvector store (with halfvec if needed)** — 384 and 1024
  both fit `vector` (≤2000); halfvec optional for storage savings; full 3072
  explicitly rejected because it exceeds the limit. §4.
- [x] **Re-embedding / embedding-version strategy defined** — `embedding_version`
  tag + dual-write + text-based backfill. §5.
- [x] **Consistent embedder for write and query paths** — single `Embedder`
  version read from one config location; both paths validated to produce
  matching vectors (parity 0.0 local). §3, §5.

---

## 8. Findings for the implementation phase (SM-C*)

Not committed to core here (decision task). See [`findings/`](findings/):
1. `SentenceTransformerEmbedder` cannot load custom-code models
   (`trust_remote_code`) — blocked `nomic`; patch saved (`st_trust_remote_code.patch`).
   **Not needed for the chosen models** (bge via fastembed/ST works as-is).
2. `HuggingfaceCustomEmbedder` is remote (HF Inference API), **not** air-gapped
   despite being listed under self-hostable — do not use it for Mode A; use
   `fastembed` / `sentence_transformer` / in-VPC `vllm` instead.
