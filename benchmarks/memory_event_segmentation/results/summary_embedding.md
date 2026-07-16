# Event Segmentation Benchmark Results

Seeds: [0, 1, 2, 3, 4]  |  boundary tolerance: +/-1 events  |  relevance: embedding

| approach | F1 | precision | recall | Pk | WinDiff | segs | latency(ms) |
|---|---|---|---|---|---|---|---|
| single_session | 0.000 | 0.000 | 0.000 | 0.465 | 0.465 | 1.0 | 0.002 |
| per_turn | 0.161 | 0.088 | 1.000 | 0.535 | 1.000 | 112.0 | 0.003 |
| fixed_window | 0.271 | 0.256 | 0.290 | 0.423 | 0.423 | 11.6 | 0.005 |
| time_gap | 0.691 | 1.000 | 0.568 | 0.201 | 0.201 | 6.2 | 0.028 |
| embedding_drift | 0.682 | 0.550 | 0.917 | 0.306 | 0.353 | 17.4 | 6.147 |
| surprise_em_llm | 0.724 | 0.646 | 0.834 | 0.194 | 0.267 | 13.6 | 23.129 |
| hybrid_gap_emllm | 0.799 | 0.699 | 0.939 | 0.130 | 0.193 | 14.0 | 23.136 |
| hybrid_gap_drift | 0.711 | 0.571 | 0.961 | 0.279 | 0.331 | 17.6 | 6.073 |

Higher F1/precision/recall is better; **lower Pk / WindowDiff is better**. `segs` is the mean number of predicted episodes per stream.
