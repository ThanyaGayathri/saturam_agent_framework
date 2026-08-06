# Event Segmentation Benchmark Results

dataset: synthetic  |  config: hard  |  n: 5  |  tolerance: +/-1 events  |  relevance: synthetic

| approach | F1 (mean±sd) | precision | recall | Pk (mean±sd) | WinDiff | segs | latency(ms) |
|---|---|---|---|---|---|---|---|
| single_session | 0.000±0.000 | 0.000 | 0.000 | 0.465±0.024 | 0.465 | 1.0 | 0.001 |
| per_turn | 0.161±0.019 | 0.088 | 1.000 | 0.535±0.024 | 1.000 | 112.0 | 0.007 |
| fixed_window | 0.271±0.100 | 0.256 | 0.290 | 0.423±0.094 | 0.423 | 11.6 | 0.008 |
| time_gap | 0.425±0.064 | 0.308 | 0.712 | 0.409±0.049 | 0.593 | 22.8 | 0.066 |
| embedding_drift | 0.950±0.058 | 0.925 | 0.978 | 0.023±0.022 | 0.031 | 11.2 | 10.985 |
| surprise_em_llm | 0.799±0.063 | 0.738 | 0.875 | 0.118±0.025 | 0.155 | 12.4 | 43.246 |
| hybrid_gap_emllm | 0.487±0.035 | 0.340 | 0.876 | 0.379±0.033 | 0.603 | 25.8 | 44.566 |
| hybrid_gap_drift | 0.529±0.036 | 0.374 | 0.921 | 0.341±0.047 | 0.550 | 24.8 | 11.261 |

Higher F1/precision/recall is better; **lower Pk / WindowDiff is better**. sd = standard deviation over the averaged units. `segs` is the mean predicted episodes per stream.
