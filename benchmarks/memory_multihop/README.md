# SM-R9 — Multi-hop need + HippoRAG PPR spike

A **runnable prototype** that answers the SM-R9 question: does multi-hop associative
retrieval (HippoRAG-style Personalized PageRank over a knowledge graph) add
measurable value over vector-only RAG for our memory use cases, and is it feasible
on the egpt stack?

Issue: [Saturam-Inc/aicoe-tracker#84](https://github.com/Saturam-Inc/aicoe-tracker/issues/84).
Grounded in `docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md` §2 (HippoRAG/HippoRAG2).

The spike ingests a real multi-hop corpus, builds a KG via LLM OpenIE, runs PPR
seeded from query entities, retrieves passages, answers, and compares against a
vector-only baseline through the **same** `MultiHopRetriever` seam. Findings and the
go/no-go land in [`SPIKE.md`](SPIKE.md).

## Layout

```
harness/
  llm.py         Azure/OpenAI Responses backend + record/replay cache + cost accounting
  embeddings.py  egpt embedder behind a TextEmbedder seam + on-disk vector cache
  dataset.py     seeded 2WikiMultiHopQA eval-set builder (pooled corpus, gold labels)
  metrics.py     recall@k, all-hops-hit, EM/token-F1, bootstrap CI
  retrievers.py  MultiHopRetriever seam: VectorRetriever + HippoRetriever (PPR)   [Phase 2-3]
  openie.py      LLM OpenIE extraction -> triples                                  [Phase 3]
  kg.py          KG build + Personalized PageRank                                  [Phase 3]
run.py           end-to-end CLI: build -> index -> retrieve -> answer -> score     [Phase 2-3]
data/eval_set.json   the committed, seeded eval corpus
results/         metrics JSON/MD + llm_cache/ + embed_cache/
findings/        local adapter fixes (patch + note), kept out of core
```

## Eval corpus (the evidence base)

Source: **`voidful/2WikiMultihopQA`** (validation split) — an established,
third-party multi-hop QA benchmark (Ho et al., COLING 2020). Non-circular: we
author neither the questions, the passages, nor the gold labels.

- **30 questions**, stratified: 12 compositional, 6 inference, 6 bridge_comparison
  (the genuinely *chaining* multi-hop types), 6 comparison (parallel-fact contrast).
- **285 passages** pooled into one shared corpus (deduped by title) — the faithful
  HippoRAG setting: each query must find its bridge passages among *all* passages.
- Gold **supporting-fact titles** per question drive retrieval recall; gold short
  **answers** drive QA EM/F1; gold **evidence triples** give the reasoning chain.

Rebuild deterministically:

```bash
PYTHONPATH=benchmarks/memory_multihop .venvs/multihop/bin/python -m harness.dataset --seed 7
```

## Metrics

| Metric | Meaning |
| --- | --- |
| `recall@k` | fraction of a question's gold passages in the top-k |
| `all_hops_hit@k` | 1 iff *every* gold passage is in top-k (the multi-hop signal) |
| `EM` / `token_F1` | SQuAD-style answer correctness vs the gold short answer |
| `cover_match` | gold answer string appears in the prediction (lenient) |
| indexing cost | OpenIE LLM calls + tokens (PPR only) |
| query latency | retrieval wall-clock per query |

95% bootstrap CIs are reported so close options are called a tie honestly.

## Setup

```bash
python3.11 -m venv .venvs/multihop
.venvs/multihop/bin/python -m pip install -e libs/agno datasets networkx numpy scipy openai python-dotenv
cp benchmarks/memory_multihop/.env.example benchmarks/memory_multihop/.env   # fill in Azure creds
```

LLM and embedding calls are cached under `results/`, so a warmed run is free,
deterministic, and offline (`HARNESS_LLM_MODE=replay`, `HARNESS_EMBED_MODE=replay`).
