# TEST_LOG — SM-R4 memory-embedding benchmark

Environment: macOS x86_64, Python 3.12 (`.venvs/mememb`), CPU-only.
egpt imported from source (`PYTHONPATH=libs/agno`); no core code committed.
Deterministic eval set built by `data/build_dataset.py` (54 memories / 32 queries).

---

### data/build_dataset.py

**Status:** PASS

**Description:** Serializes the hand-authored synthetic memory corpus, queries and
qrels to JSON. Sanity check asserts every gold id exists in the corpus.

**Result:** 54 memories, 32 queries, 34 relevance judgements. Reproducible
(byte-identical on re-run).

---

### harness/run.py --dataset synthetic --mode A

**Status:** PASS

**Description:** Retrieval quality + latency + throughput + sync/async parity +
storage cost for 7 self-hostable models, each via its egpt embedder abstraction
(`SentenceTransformerEmbedder`, `FastEmbedEmbedder`).

**Result:** All models ran. Every model's sync (`get_embedding`) and async
(`async_get_embedding`) paths produced byte-identical vectors (max abs diff 0.0).
Highlights (synthetic memory set):

| Model | Dim | Recall@5 | nDCG@10 | p50 ms |
|-------|-----|----------|---------|--------|
| bge-base-en-v1.5 | 768 | 1.000 | 0.923 | 94 |
| gte-base | 768 | 1.000 | 0.895 | 86 |
| bge-large-en-v1.5 | 1024 | 0.984 | 0.942 | 253 |
| bge-small-en-v1.5 (ST) | 384 | 0.969 | 0.927 | 88 |
| bge-small ONNX (fastembed) | 384 | 0.969 | 0.927 | 12.6 |
| e5-base-v2 | 768 | 0.938 | 0.902 | 105 |
| all-MiniLM-L6-v2 | 384 | 0.953 | 0.874 | 35 |

`nomic-embed-text-v1.5` FAILED on stock core (needs `trust_remote_code`) — see
Finding 1; re-run with the local patch gave Recall@5 0.938 / nDCG@10 0.910 / 257 ms
(dim 768). Results: `results/results_synthetic.json`,
`results/results_synthetic_nomic.json`.

---

### harness/run.py --dataset beir:scifact (6 models, external cross-check)

**Status:** PASS

**Description:** Capped BEIR/SciFact slice (859 docs, 50 queries) to check the
synthetic ranking against a public IR benchmark.

**Result:** Ranking order tracks published MTEB standings (bge-base ≈ gte-base >
e5-base ≈ bge-small > MiniLM), validating the harness. bge-small (384d) stays
within ~3 nDCG points of the 768d models. ONNX and ST routes to the same BGE
weights gave identical quality. Results: `results/results_beir_scifact.json`.

---

### storage cost model (harness/storage.py) @ 10M vectors

**Status:** PASS

**Description:** pgvector footprint by dimension at the provisional 10M-memory
scale (SM-R3 coupling).

**Result:** 384d ≈ 40 GB (float32) / 20 GB (halfvec); 768d ≈ 80 / 40 GB;
1536d ≈ 160 / 80 GB; 3072d exceeds the pgvector `vector` 2000-dim limit and must
use `halfvec` (≈160 GB) or be truncated. Results: `results/storage_cost_10M.json`.

---

### harness/run.py --dataset synthetic --mode B (Azure AI Foundry)

**Status:** PASS

**Description:** `text-embedding-3-small` / `-large` (full + Matryoshka-truncated
dims) via `AzureOpenAIEmbedder` against Azure AI Foundry
(`aicoekey.cognitiveservices.azure.com`, api_version 2024-10-21). Key supplied via
gitignored session scratchpad, never committed.

**Result:** All 4 points ran. Quality (synthetic memory set):

| Model | Dim | Recall@5 | nDCG@10 | p50 (network) |
|-------|-----|----------|---------|---------------|
| text-embedding-3-large | 3072 | 1.000 | 0.959 | 1749 ms |
| text-embedding-3-large | 1024 | 1.000 | 0.947 | 1435 ms |
| text-embedding-3-small | 1536 | 1.000 | 0.954 | 1759 ms |
| text-embedding-3-small | 512 | 0.969 | 0.931 | 1337 ms |

Dimension truncation via the `dimensions` param confirmed working (1024/512
returned exactly). Latency is WAN round-trip from the dev box, not model compute;
sync/async parity ≤ 4e-3 (provider embeddings not bitwise-deterministic across
calls, unlike local models). Results: `results/results_synthetic_modeB.json`.

---

### Core untouched check

**Status:** PASS

**Description:** Decision task must not commit core changes. The `nomic` run
temporarily applied `findings/st_trust_remote_code.patch` to
`libs/agno/egpt/knowledge/embedder/sentence_transformer.py` and reverted it.

**Result:** `git status libs/` clean; only `benchmarks/` is added.

---

### ./scripts/format.sh and ./scripts/validate.sh

**Status:** PASS (in scope) — see note on out-of-scope core mypy.

**Description:** Repo format/validate gates. These target `libs/*` and `cookbook`,
not `benchmarks/`; the in-scope (`benchmarks/`) files were additionally checked
directly with the repo-pinned `ruff==0.15.20` (`ruff format --check`, `ruff check`)
and `mypy` — all clean.

**Result:**
- `format.sh`: PASS — 4126 files across agno/agnoctl/agno_infra/cookbook left
  unchanged; `git diff HEAD -- libs/` is empty (core byte-identical, untouched).
- `validate.sh`: `ruff check` PASS (agno/agnoctl/cookbook); `agnoctl` mypy PASS;
  `cookbook` pattern check PASS. `agno` mypy reports 3 pre-existing errors in
  `libs/agno/egpt/knowledge/reranker/sentence_transformer.py` (a core reranker
  file **not touched by this task** and unrelated to embedders). They are a
  `CrossEncoder` signature mismatch surfaced by the benchmark venv's
  `sentence-transformers==3.0.1` pin (required by the macOS-x86 torch-2.2.2
  constraint), not by any change here — `git diff HEAD -- libs/` is empty. Running
  `validate.sh` in the repo's own dev venv (`./scripts/dev_setup.sh`) checks core
  against its intended dependency versions; this benchmark venv is deliberately
  minimal. No core file was modified to silence these.
