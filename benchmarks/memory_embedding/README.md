# SM-R4 — Embedding model benchmark for the memory vector tier

Reproducible benchmark backing the decision in [`DECISION.md`](DECISION.md) for
[aicoe-tracker#79](https://github.com/Saturam-Inc/aicoe-tracker/issues/79)
("Decision: embedding model for memory").

This is a **decision task**. It produces benchmark code + a decision document
only. It does **not** modify framework/core code (`libs/agno/egpt/**`); the
embedder implementation happens later in the SM-C* tasks. Every candidate is
exercised through an *existing* egpt embedder abstraction so the numbers reflect
what the memory subsystem would actually get.

## What is measured

For each candidate model (per query set):

| Dimension | How |
|-----------|-----|
| Retrieval quality | recall@{1,5,10}, nDCG@10, MRR@10 on a labeled eval set |
| Latency | single-query embedding p50 / p95 (ms), sync path, CPU |
| Throughput | documents/second, sync sequential **and** async concurrent |
| sync/async parity | max abs vector diff between `get_embedding` and `async_get_embedding` |
| Vector dimension | measured from the produced vectors |
| Storage/index cost | pgvector footprint at 10M vectors for that dim (couples to SM-R3) |
| Footprint / license / offline | from the registry (static model facts) |

Quality, latency and throughput are always **measured**, never cited. Only the
static columns (params, disk, license, offline-capable) are declared.

## Models under test

English-only scope (see decision Q&A). Mode A = self-hostable / air-gapped,
Mode B = provider.

- **Mode A** (via `SentenceTransformerEmbedder` / `FastEmbedEmbedder`):
  `all-MiniLM-L6-v2` (384), `bge-small-en-v1.5` (384, ST + ONNX),
  `bge-base-en-v1.5` (768), `bge-large-en-v1.5` (1024), `e5-base-v2` (768),
  `gte-base` (768), `nomic-embed-text-v1.5` (768).
- **Mode B** (via `OpenAIEmbedder`): `text-embedding-3-small` (1536 and
  truncated 512), `text-embedding-3-large` (3072 and truncated 1024).

## Eval sets

1. **`synthetic`** (primary) — a hand-authored, seeded memory-tier corpus of 54
   short memories (preferences / episodic events / entity facts / tasks) with 32
   labeled queries exercising paraphrase, temporal, coreference and
   hard-negative retrieval. Built by [`data/build_dataset.py`](data/build_dataset.py);
   re-running it reproduces byte-identical JSON. Generic IR benchmarks are not
   representative of the short, personal, episodic text of the memory tier,
   which is why this set exists.
2. **`beir:scifact`** (cross-check) — a capped slice of a public BEIR IR
   benchmark, for an external sanity check against published MTEB-style rankings.

## Setup

```bash
# Dedicated venv (Python 3.12; not the project .venv / .venvs/demo).
python3.12 -m venv .venvs/mememb
.venvs/mememb/bin/python -m pip install -r benchmarks/memory_embedding/requirements.txt
```

> macOS x86 caps `torch` at 2.2.2, which requires `numpy<2` and
> `transformers<4.45`; the pins in `requirements.txt` reflect that. On
> linux/arm64 newer versions work too.

## Running

```bash
cd benchmarks/memory_embedding
export PYTHONPATH=../../libs/agno:.        # egpt + harness on the path

# Build (or rebuild) the eval set
../../.venvs/mememb/bin/python data/build_dataset.py

# Mode A (self-hostable models) on the synthetic memory set
../../.venvs/mememb/bin/python -m harness.run --dataset synthetic --mode A

# Mode B (OpenAI) — requires OPENAI_API_KEY in the environment
OPENAI_API_KEY=sk-... ../../.venvs/mememb/bin/python -m harness.run \
    --dataset synthetic --mode B

# External cross-check
../../.venvs/mememb/bin/python -m harness.run --dataset beir:scifact --mode A
```

Results are written to [`results/`](results/) as JSON. A rendered summary and
the interpretation live in [`DECISION.md`](DECISION.md).

## Layout

```
benchmarks/memory_embedding/
├── README.md              # this file
├── DECISION.md            # the decision + provisional targets + migration plan
├── TEST_LOG.md            # run log (PASS/FAIL per step)
├── requirements.txt       # pinned benchmark deps
├── data/
│   ├── build_dataset.py   # authored corpus/queries/qrels -> JSON (reproducible)
│   ├── corpus.json
│   ├── queries.json
│   └── qrels.json
├── harness/
│   ├── embedders.py       # registry over existing egpt embedder abstractions
│   ├── datasets.py        # synthetic + BEIR loaders
│   ├── metrics.py         # recall@k, nDCG@10, MRR@10
│   ├── storage.py         # pgvector storage/index cost model (SM-R3 coupling)
│   └── run.py             # orchestrator
├── results/               # generated JSON + logs
└── findings/              # adapter issues found while benchmarking (for SM-C*)
```

## Coupling to SM-R3

The chosen dimension drives the vector-store storage and index cost that SM-R3
(#78) sizes. `harness/storage.py` quantifies this at the provisional 10M-memory
scale and enforces the pgvector limits (`vector` ≤ 2000 dims, `halfvec` ≤ 4000).
See the storage table in `DECISION.md`.
