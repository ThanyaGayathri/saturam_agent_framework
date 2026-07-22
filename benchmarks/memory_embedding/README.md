# SM-R4 — Embedding model benchmark for the memory vector tier

Reproducible benchmark backing [`DECISION.md`](DECISION.md) for
[aicoe-tracker#79](https://github.com/Saturam-Inc/aicoe-tracker/issues/79)
("Decision: embedding model for memory").

This is a **decision task**: it produces benchmark code + a decision document
only. It does **not** modify framework/core code (`libs/agno/egpt/**`); the
embedder implementation happens later in the SM-C* tasks. Every candidate is
exercised through an *existing* egpt embedder abstraction so the numbers reflect
what the memory subsystem would actually get.

> This is the **v2** eval (rigorous re-run). The v1 set that shipped in the first
> attempt was too small/single-authored to separate close models; it is retained
> under `data/synthetic_v1/` for provenance and is **superseded** — do not decide
> on it. See `DECISION.md` §3 for what changed.

## What is measured

For each candidate model, per eval set:

| Dimension | How |
|-----------|-----|
| Retrieval quality | Recall@{1,5,10}, **graded** nDCG@10, MRR@10 |
| Statistical significance | 95% bootstrap CIs + **paired-bootstrap** tests; close models flagged as ties, never ranked within noise |
| Latency | single-query embedding p50/p95 (ms), **CPU**, per-item |
| Throughput | documents/second (batched corpus indexing) |
| sync/async parity | max abs vector diff between `get_embedding` and `async_get_embedding` |
| Storage/index cost | pgvector footprint at 10M vectors for that dim (couples to SM-R3) |

Quality/latency are always **measured**, never cited. CPU is pinned (the Mode A
deployment is CPU/air-gapped; also avoids a Mac-MPS OOM/artifact).

## Eval sets

1. **`synthetic_v2`** (primary) — an **independent, episodic** memory-tier set: 225
   memories / 199 labeled queries across 15 diverse personas, authored by an LLM
   (Azure gpt-5.4) in separate passes. Labeling is **mode-aware + blind + audited**:
   a separate judge pass grades each query's candidates 0–2 with a per-mode rubric
   (hard-negative traps, wrong-time events, and superseded facts → 0), then a
   **provenance audit** forces traps/superseded to 0, keeps the single-answer modes
   (hard_negative / temporal / contradiction) to the correct answer only, and drops
   queries the judge won't back. Modes include **contradiction/belief-revision** and
   time-specific **temporal**. Built by `data/generate_synthetic_v2.py` +
   `data/label_relevance.py` (de-circularized — the harness author writes neither the
   content nor the labels).
2. **`beir:<name>`** (external cross-check) — established, human-labeled BEIR IR
   benchmarks (SciFact / NFCorpus / ArguAna). Full/uncapped by default; a capped
   slice (`--beir-capped`) is used when the host is CPU-constrained.

## Models under test

English-only scope. Mode A = self-hostable / air-gapped, Mode B = provider.

- **Mode A** (`SentenceTransformerEmbedder` / `FastEmbedEmbedder`): all-MiniLM-L6-v2
  (384), bge-small-en-v1.5 (384, ST + ONNX), bge-base-en-v1.5 (768), bge-large-en-v1.5
  (1024), e5-base-v2 (768), **gte-base (768)**, nomic-embed-text-v1.5 (768, `[patched]`).
- **Mode B** (`AzureOpenAIEmbedder`): text-embedding-3-large (3072 / 1024),
  text-embedding-3-small (1536 / 512).

## Setup

```bash
python3.12 -m venv .venvs/mememb
.venvs/mememb/bin/python -m pip install -r benchmarks/memory_embedding/requirements.txt
```

> macOS x86 caps `torch` at 2.2.2 → `numpy<2`, `transformers<4.45` (pinned).

## Running

```bash
cd benchmarks/memory_embedding
export PYTHONPATH=../../libs/agno:.

# (Re)build the independent synthetic set (needs Azure chat creds in env)
python data/generate_synthetic_v2.py --personas 15 --mem-per 15 --q-per 12 --out data/synthetic_v2
python data/label_relevance.py --dir data/synthetic_v2

# Mode A on the memory set (CPU, with latency)
python -m harness.run --dataset synthetic_v2 --mode A --out results_v2_synthetic_v2.json

# Mode B (Azure) — needs AZURE_EMBEDDER_OPENAI_* env
python -m harness.run --dataset synthetic_v2 --mode B --out results_v2_synthetic_v2_modeB.json

# External cross-check (full; add --beir-capped on a loaded host)
python -m harness.run --dataset beir:scifact --mode A --latency-reps 0 --out results_v2_beir_scifact.json

# Significance + CIs + cross-set rank agreement
python -m harness.analyze --metric ndcg@10 results/results_v2_synthetic_v2*.json
```

Credentials come from env only (never committed): `AZURE_OPENAI_*` for the
data-build LLM, `AZURE_EMBEDDER_OPENAI_*` + `AZURE_DEPLOYMENT_{SMALL,LARGE}` for
Mode B. Results (vectors/metrics only, no secrets) are under `results/`.

## Layout

```
benchmarks/memory_embedding/
├── README.md · DECISION.md · TEST_LOG.md · requirements.txt
├── data/
│   ├── generate_synthetic_v2.py   # LLM authors corpus+queries (separate passes)
│   ├── label_relevance.py         # separate BLIND graded judge -> qrels
│   ├── synthetic_v2/              # the primary eval set of record
│   └── synthetic_v1/              # retired PR-#5 set (provenance only, superseded)
├── harness/
│   ├── embedders.py   # registry over egpt abstractions; batched + CPU-pinned
│   ├── metrics.py     # graded recall/nDCG/MRR, per-query
│   ├── stats.py       # bootstrap CIs + paired significance + tie-aware ranking
│   ├── datasets.py    # synthetic_v2 + (full/capped) BEIR loaders
│   ├── llm.py         # Azure chat helper (data build only; not model scoring)
│   ├── run.py         # orchestrator
│   └── analyze.py     # CIs / significance / cross-set rank agreement
├── results/           # measured JSON
└── findings/          # egpt adapter issues found while benchmarking (for SM-C*)
```

## Coupling to SM-R3

The chosen dimension drives the vector-store storage/index cost SM-R3 (#78) sizes.
`harness/storage.py` quantifies it at 10M vectors and enforces the pgvector limits
(`vector` ≤ 2000, `halfvec` ≤ 4000). See `DECISION.md` §4.
