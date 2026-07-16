# Event Segmentation Benchmark Results

Seeds: [0, 1, 2, 3, 4]  |  boundary tolerance: +/-1 events  |  relevance: synthetic

| approach | F1 | precision | recall | Pk | WinDiff | segs | latency(ms) |
|---|---|---|---|---|---|---|---|
| single_session | 0.000 | 0.000 | 0.000 | 0.465 | 0.465 | 1.0 | 0.001 |
| per_turn | 0.161 | 0.088 | 1.000 | 0.535 | 1.000 | 112.0 | 0.002 |
| fixed_window | 0.271 | 0.256 | 0.290 | 0.423 | 0.423 | 11.6 | 0.003 |
| time_gap | 0.691 | 1.000 | 0.568 | 0.201 | 0.201 | 6.2 | 0.023 |
| embedding_drift | 0.970 | 0.962 | 0.978 | 0.023 | 0.023 | 10.8 | 4.893 |
| surprise_em_llm | 0.846 | 0.752 | 0.978 | 0.096 | 0.147 | 13.8 | 19.146 |
| hybrid_gap_emllm | 0.846 | 0.752 | 0.978 | 0.096 | 0.147 | 13.8 | 18.807 |
| hybrid_gap_drift | 0.970 | 0.962 | 0.978 | 0.021 | 0.021 | 10.8 | 4.992 |

Higher F1/precision/recall is better; **lower Pk / WindowDiff is better**. `segs` is the mean number of predicted episodes per stream.
