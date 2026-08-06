# SM-R8 results — 20260723T122020Z_fiqaFusion

- embedding model: `local:all-MiniLM-L6-v2` | reranker: `st-minilm` | RRF k=60 | vector_weight=0.5 | rerank_depth=50
- host: macOS-15.7.3-x86_64-i386-64bit (x86_64); latencies are Intel-Mac CPU, indicative; query-embed latency excluded (option-invariant)

## beir/fiqa

| option | nDCG@10 | R@10 | R@50 | R@100 | MRR | p50 ms | p95 ms | n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a-vector | 0.3687 | 0.4413 | 0.6306 | 0.7061 | 0.4543 | 7.7 | 10.4 | 648 |
| b-rrf | 0.3385 | 0.4172 | 0.6330 | 0.6921 | 0.4199 | 289.5 | 514.1 | 648 |
| c-weighted | 0.3657 | 0.4432 | 0.6303 | 0.6906 | 0.4502 | 279.5 | 507.6 | 648 |
| e-composite | 0.3385 | 0.4172 | 0.6330 | 0.6330 | 0.4194 | 269.2 | 492.2 | 648 |

Paired nDCG@10 (bootstrap 95% CI on the per-query delta; 'ranked' = CI excludes 0):

| comparison | Δ nDCG@10 | 95% CI | p | ranked? |
| --- | --- | --- | --- | --- |
| b-rrf vs a-vector | -0.0301 | [-0.0500, -0.0105] | 0.003 | yes |
| c-weighted vs a-vector | -0.0029 | [-0.0193, +0.0130] | 0.709 | tie |
| e-composite vs a-vector | -0.0301 | [-0.0500, -0.0105] | 0.003 | yes |
| c-weighted vs b-rrf | +0.0272 | [+0.0177, +0.0368] | 0.000 | yes |
| e-composite vs b-rrf | +0.0000 | [+0.0000, +0.0000] | 1.000 | tie |
