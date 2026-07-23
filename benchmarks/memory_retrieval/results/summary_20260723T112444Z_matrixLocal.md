# SM-R8 results — 20260723T112444Z_matrixLocal

- embedding model: `local:all-MiniLM-L6-v2` | reranker: `st-minilm` | RRF k=60 | vector_weight=0.5 | rerank_depth=50
- host: macOS-15.7.3-x86_64-i386-64bit (x86_64); latencies are Intel-Mac CPU, indicative; query-embed latency excluded (option-invariant)

## beir/scifact

| option | nDCG@10 | R@10 | R@50 | R@100 | MRR | p50 ms | p95 ms | n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a-vector | 0.6451 | 0.7833 | 0.8920 | 0.9250 | 0.6110 | 0.5 | 0.9 | 300 |
| b-rrf | 0.6768 | 0.8059 | 0.9397 | 0.9577 | 0.6470 | 24.4 | 47.9 | 300 |
| c-weighted | 0.7113 | 0.8343 | 0.9380 | 0.9543 | 0.6820 | 23.7 | 48.6 | 300 |
| d-rerank[st-minilm] | 0.6956 | 0.8306 | 0.9397 | 0.9397 | 0.6678 | 1953.0 | 2405.9 | 300 |
| e-composite | 0.6768 | 0.8059 | 0.9397 | 0.9397 | 0.6468 | 43.6 | 85.1 | 300 |

Paired nDCG@10 (bootstrap 95% CI on the per-query delta; 'ranked' = CI excludes 0):

| comparison | Δ nDCG@10 | 95% CI | p | ranked? |
| --- | --- | --- | --- | --- |
| b-rrf vs a-vector | +0.0317 | [+0.0047, +0.0587] | 0.022 | yes |
| c-weighted vs a-vector | +0.0663 | [+0.0403, +0.0921] | 0.000 | yes |
| d-rerank[st-minilm] vs a-vector | +0.0506 | [+0.0172, +0.0835] | 0.004 | yes |
| e-composite vs a-vector | +0.0317 | [+0.0047, +0.0587] | 0.022 | yes |
| c-weighted vs b-rrf | +0.0345 | [+0.0186, +0.0510] | 0.000 | yes |
| d-rerank[st-minilm] vs b-rrf | +0.0188 | [-0.0108, +0.0478] | 0.212 | tie |
| e-composite vs b-rrf | +0.0000 | [+0.0000, +0.0000] | 1.000 | tie |

## beir/nfcorpus

| option | nDCG@10 | R@10 | R@50 | R@100 | MRR | p50 ms | p95 ms | n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a-vector | 0.3160 | 0.1550 | 0.2508 | 0.3115 | 0.5101 | 0.3 | 0.7 | 323 |
| b-rrf | 0.3354 | 0.1592 | 0.2536 | 0.3183 | 0.5519 | 6.1 | 17.3 | 323 |
| c-weighted | 0.3452 | 0.1681 | 0.2614 | 0.3094 | 0.5488 | 6.2 | 17.1 | 323 |
| d-rerank[st-minilm] | 0.3535 | 0.1643 | 0.2536 | 0.2536 | 0.5831 | 2458.6 | 2756.2 | 323 |
| e-composite | 0.3354 | 0.1592 | 0.2536 | 0.2536 | 0.5512 | 10.4 | 33.9 | 323 |

Paired nDCG@10 (bootstrap 95% CI on the per-query delta; 'ranked' = CI excludes 0):

| comparison | Δ nDCG@10 | 95% CI | p | ranked? |
| --- | --- | --- | --- | --- |
| b-rrf vs a-vector | +0.0194 | [+0.0041, +0.0354] | 0.012 | yes |
| c-weighted vs a-vector | +0.0291 | [+0.0146, +0.0445] | 0.000 | yes |
| d-rerank[st-minilm] vs a-vector | +0.0375 | [+0.0198, +0.0557] | 0.000 | yes |
| e-composite vs a-vector | +0.0194 | [+0.0041, +0.0354] | 0.012 | yes |
| c-weighted vs b-rrf | +0.0098 | [+0.0026, +0.0172] | 0.009 | yes |
| d-rerank[st-minilm] vs b-rrf | +0.0181 | [+0.0052, +0.0313] | 0.004 | yes |
| e-composite vs b-rrf | +0.0000 | [+0.0000, +0.0000] | 1.000 | tie |

## longmemeval/longmemeval_s

| option | nDCG@10 | R@10 | R@50 | R@100 | MRR | p50 ms | p95 ms | n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| a-vector | 0.8271 | 0.9211 | 1.0000 | 1.0000 | 0.8337 | 0.2 | 0.3 | 470 |
| b-rrf | 0.8934 | 0.9679 | 1.0000 | 1.0000 | 0.8893 | 1.0 | 2.0 | 470 |
| c-weighted | 0.9209 | 0.9790 | 1.0000 | 1.0000 | 0.9181 | 1.0 | 2.0 | 470 |
| d-rerank[st-minilm] | 0.8414 | 0.9095 | 1.0000 | 1.0000 | 0.8673 | 2281.7 | 2753.7 | 470 |
| e-composite | 0.8337 | 0.9627 | 1.0000 | 1.0000 | 0.8206 | 1.5 | 2.5 | 470 |

Paired nDCG@10 (bootstrap 95% CI on the per-query delta; 'ranked' = CI excludes 0):

| comparison | Δ nDCG@10 | 95% CI | p | ranked? |
| --- | --- | --- | --- | --- |
| b-rrf vs a-vector | +0.0663 | [+0.0493, +0.0828] | 0.000 | yes |
| c-weighted vs a-vector | +0.0939 | [+0.0763, +0.1116] | 0.000 | yes |
| d-rerank[st-minilm] vs a-vector | +0.0143 | [-0.0073, +0.0363] | 0.194 | tie |
| e-composite vs a-vector | +0.0066 | [-0.0146, +0.0277] | 0.537 | tie |
| c-weighted vs b-rrf | +0.0276 | [+0.0181, +0.0372] | 0.000 | yes |
| d-rerank[st-minilm] vs b-rrf | -0.0520 | [-0.0731, -0.0309] | 0.000 | yes |
| e-composite vs b-rrf | -0.0597 | [-0.0737, -0.0462] | 0.000 | yes |
