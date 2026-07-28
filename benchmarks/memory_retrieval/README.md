# SM-R8 — Memory retrieval fusion + reranker benchmark

Reproducible benchmark behind [`DECISION.md`](DECISION.md): it drives the memory
retrieval pipeline through **five fusion/rerank configurations** and measures
**retrieval quality (nDCG@10 / recall@k / MRR)** plus **added latency**, on real
labeled data, to decide the fusion method and reranker policy for hybrid
neuro-symbolic memory retrieval (issue
[SM-R8 #83](https://github.com/Saturam-Inc/aicoe-tracker/issues/83)).

Everything is built as a **plug-and-play pipeline** over the real `egpt` abstractions
(the `Embedder`, the `Reranker` base) and never modifies `libs/agno/egpt/**`.

## Options evaluated

| id | configuration |
| --- | --- |
| a | vector-only baseline (dense) |
| b | hybrid dense + BM25, **RRF** fusion (k=60) |
| c | hybrid dense + BM25, **weighted/relative-score** fusion (pgvector-style, `vector_weight=0.5`) |
| d | option b + **cross-encoder rerank** (self-hosted, depth 50 → 10) |
| e | option b + **composite time-aware** rescoring (relevance + recency) |

Each option is just a configuration of one `RetrievalPipeline` — swapping fusion /
reranker / scorer changes no call sites (`pipeline.py`).

## Datasets (real, independently labeled — see the data-quality section of DECISION.md)

| dataset | queries | docs | labels | timestamps |
| --- | --- | --- | --- | --- |
| beir/scifact | 300 | 5,183 | binary | — |
| beir/nfcorpus | 323 | 3,633 | graded | — |
| beir/fiqa | 648 | 57,638 | binary | — |
| longmemeval/longmemeval_s | 470 | 23,566 | binary | yes (recency) |

BEIR = general-IR quality; LongMemEval-S = memory-shaped queries with per-session
timestamps (the only set where composite time-aware scoring is a real signal).

## Metrics

nDCG@10 / recall@{10,50,100} / MRR via **pytrec_eval** (the BEIR-standard lib), plus
**bootstrap CIs** and a **paired-bootstrap significance test** — options are only
*ranked* when the per-query gap clears noise, otherwise reported as a tie
(`metrics.py`).

## Setup

```bash
# dedicated venv (Intel-Mac note: torch caps at 2.2.2 -> sentence-transformers 3.4.1)
/usr/local/bin/python3.12 -m venv .venvs/smr8
.venvs/smr8/bin/python -m pip install -e libs/agno
.venvs/smr8/bin/python -m pip install numpy==1.26.4 torch==2.2.2 transformers==4.44.2 \
    "sentence-transformers==3.4.1" rank_bm25 pytrec_eval openai python-dotenv datasets requests

# Dense embedder is SM-R4's ratified choice (benchmarks/memory_embedding/DECISION.md
# §1), not a placeholder: default `--embedder local` downloads BAAI/bge-base-en-v1.5
# (Mode A, 768-dim, offline). `--embedder azure` uses text-embedding-3-large truncated
# to 1024-dim (Mode B) via the repo .env (AZURE_OPENAI_*); usable for the smaller BEIR
# sets, the deployment's TPM limit makes long-doc LongMemEval impractical.
export MEMBENCH_DATA_DIR=/path/to/cache        # dataset + embedding cache
export OMP_NUM_THREADS=8                        # CPU cross-encoder needs the cores
export PYTHONPATH=$PWD
```

## Running

```bash
python -m benchmarks.memory_retrieval.run \
  --datasets beir/scifact beir/nfcorpus longmemeval/longmemeval_s beir/fiqa \
  --options a b c d e --reranker st-minilm --seed 7 --tag matrixA
```

Parameter sweeps (for the switch thresholds) use the same CLI with `--rrf-k`,
`--vector-weight`, `--rerank-depth`, `--recency-weight`, and a distinct `--tag`.
Results (raw JSON + a markdown summary with the paired-significance table) land in
[`results/`](results/).

## Files

| File | Role |
| --- | --- |
| `data.py` | BEIR + LongMemEval loaders unified behind `RetrievalTask` |
| `embcache.py` | disk-cached embeddings through the egpt `AzureOpenAIEmbedder` |
| `retrievers.py` | `Retriever` ABC + dense (cosine) + BM25 adapters |
| `fusion.py` | `Fusion` ABC + `RRFFusion` + `WeightedFusion` |
| `rerank.py` | `RerankStage` wrapping the egpt `Reranker` base (offline + provider) |
| `scoring.py` | `CompositeScorer` — Generative-Agents relevance+recency+importance |
| `pipeline.py` | `RetrievalPipeline` composing the stages (sync + async) |
| `harness.py` | one (dataset × option) case → metrics + latency |
| `run.py` | CLI matrix + sweeps; writes JSON + summary |
| `metrics.py` | pytrec_eval metrics + bootstrap CI + paired significance |
| `findings/` | defects/seams found (kept out of core, deferred to implementation) |
| `DECISION.md` | the SM-R8 decision |
| `TEST_LOG.md` | run log |

## Known limitations (measurement honesty)

- Latencies are **Intel-Mac CPU, indicative** — the relative rerank cost and the
  fusion comparison are robust; absolute p95 would drop on a GPU / in-VPC serving
  host (see DECISION.md latency-budget section).
- The dense retriever is in-process (store-agnostic) so the fusion comparison is
  clean; pgvector's weighted fusion is reproduced faithfully in `WeightedFusion`.
- Composite time-aware scoring is measured on LongMemEval only (BEIR has no
  timestamps); importance is a write-time signal (SM-E7), not computed at query time.
