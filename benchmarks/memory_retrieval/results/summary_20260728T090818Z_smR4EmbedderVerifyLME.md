# SM-R8 results — 20260728T090818Z_smR4EmbedderVerifyLME

- embedding model: `local:bge-base-en-v1.5` | reranker: `st-minilm` | RRF k=60 | vector_weight=0.5 | rerank_depth=50
- host: macOS-26.5.2-arm64-arm-64bit (arm64); latencies are Intel-Mac CPU, indicative; query-embed latency excluded (option-invariant)

## longmemeval/longmemeval_s

| option | nDCG@10 | R@10 | R@50 | R@100 | MRR | p50 ms | p95 ms | n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a-vector | 0.8717 | 0.9547 | 1.0000 | 1.0000 | 0.8683 | 0.0 | 0.0 | 470 |
| b-rrf | 0.9104 | 0.9757 | 1.0000 | 1.0000 | 0.9089 | 0.2 | 0.3 | 470 |
| c-weighted | 0.9297 | 0.9806 | 1.0000 | 1.0000 | 0.9246 | 0.2 | 0.3 | 470 |

Paired nDCG@10 (bootstrap 95% CI on the per-query delta; 'ranked' = CI excludes 0):

| comparison | Δ nDCG@10 | 95% CI | p | ranked? |
| --- | --- | --- | --- | --- |
| b-rrf vs a-vector | +0.0387 | [+0.0252, +0.0523] | 0.000 | yes |
| c-weighted vs a-vector | +0.0580 | [+0.0438, +0.0726] | 0.000 | yes |
| c-weighted vs b-rrf | +0.0193 | [+0.0112, +0.0277] | 0.000 | yes |
