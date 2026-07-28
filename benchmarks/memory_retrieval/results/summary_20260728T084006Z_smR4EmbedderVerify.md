# SM-R8 results — 20260728T084006Z_smR4EmbedderVerify

- embedding model: `local:bge-base-en-v1.5` | reranker: `st-minilm` | RRF k=60 | vector_weight=0.5 | rerank_depth=50
- host: macOS-26.5.2-arm64-arm-64bit (arm64); latencies are Intel-Mac CPU, indicative; query-embed latency excluded (option-invariant)

## beir/scifact

| option | nDCG@10 | R@10 | R@50 | R@100 | MRR | p50 ms | p95 ms | n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a-vector | 0.7376 | 0.8659 | 0.9377 | 0.9700 | 0.7043 | 0.1 | 0.1 | 300 |
| b-rrf | 0.7217 | 0.8364 | 0.9549 | 0.9693 | 0.6971 | 6.4 | 12.6 | 300 |
| c-weighted | 0.7445 | 0.8779 | 0.9527 | 0.9660 | 0.7103 | 6.4 | 12.4 | 300 |

Paired nDCG@10 (bootstrap 95% CI on the per-query delta; 'ranked' = CI excludes 0):

| comparison | Δ nDCG@10 | 95% CI | p | ranked? |
| --- | --- | --- | --- | --- |
| b-rrf vs a-vector | -0.0160 | [-0.0444, +0.0122] | 0.279 | tie |
| c-weighted vs a-vector | +0.0069 | [-0.0170, +0.0307] | 0.574 | tie |
| c-weighted vs b-rrf | +0.0229 | [+0.0073, +0.0386] | 0.003 | yes |

## beir/nfcorpus

| option | nDCG@10 | R@10 | R@50 | R@100 | MRR | p50 ms | p95 ms | n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a-vector | 0.3693 | 0.1756 | 0.2746 | 0.3320 | 0.5753 | 0.1 | 0.1 | 323 |
| b-rrf | 0.3565 | 0.1698 | 0.2646 | 0.3266 | 0.5820 | 1.0 | 3.0 | 323 |
| c-weighted | 0.3671 | 0.1759 | 0.2688 | 0.3237 | 0.5803 | 1.0 | 3.0 | 323 |

Paired nDCG@10 (bootstrap 95% CI on the per-query delta; 'ranked' = CI excludes 0):

| comparison | Δ nDCG@10 | 95% CI | p | ranked? |
| --- | --- | --- | --- | --- |
| b-rrf vs a-vector | -0.0128 | [-0.0260, +0.0003] | 0.057 | tie |
| c-weighted vs a-vector | -0.0022 | [-0.0136, +0.0090] | 0.701 | tie |
| c-weighted vs b-rrf | +0.0106 | [+0.0032, +0.0179] | 0.006 | yes |
