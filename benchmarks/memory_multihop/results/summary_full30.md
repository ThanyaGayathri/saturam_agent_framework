# SM-R9 results — full run (n=30, 285 passages, k=5)

Source: `results_20260723T075917Z_full30.json`

| method | recall@5 | all-hops@5 | EM | F1 | chaining all-hops | chaining F1 | q-latency p95 (s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| vector | 0.750 [0.6667, 0.8333] | 0.467 [0.3, 0.6667] | 0.567 | 0.583 | 0.333 [0.1667, 0.5417] | 0.479 | 2.199 |
| hippo | 0.908 [0.8417, 0.9667] | 0.767 [0.6, 0.9] | 0.700 | 0.731 | 0.708 [0.5, 0.875] | 0.663 | 3.836 |
| hybrid | 0.892 [0.825, 0.95] | 0.700 [0.5333, 0.8667] | 0.633 | 0.656 | 0.625 [0.4583, 0.7917] | 0.569 | 0.065 |

## HippoRAG index cost (one-time)

- OpenIE: 285 LLM calls, 107637 tokens, $0.63
- KG: 3393 entities, 7199 edges
- Index wall-clock: 1058s (OpenIE ~889s, sequential)
- OpenIE extraction failures: 0/285

CIs are 95% bootstrap (2000 resamples, seed 7). n=30 (24 chaining + 6 comparison).
