# Event Segmentation Benchmark Results

Seeds: [0, 1, 2]  |  boundary tolerance: +/-1 events  |  relevance: synthetic

| approach | F1 | precision | recall | Pk | WinDiff | segs | latency(ms) | cost($) |
|---|---|---|---|---|---|---|---|---|
| single_session | 0.000 | 0.000 | 0.000 | 0.462 | 0.462 | 1.0 | 0.001 | 0.00000 |
| per_turn | 0.158 | 0.086 | 1.000 | 0.538 | 1.000 | 119.3 | 0.003 | 0.00000 |
| fixed_window | 0.344 | 0.322 | 0.373 | 0.367 | 0.367 | 12.3 | 0.005 | 0.00000 |
| time_gap | 0.781 | 1.000 | 0.687 | 0.143 | 0.143 | 7.3 | 0.036 | 0.00000 |
| embedding_drift | 0.987 | 0.974 | 1.000 | 0.017 | 0.017 | 11.3 | 6.990 | 0.00000 |
| surprise_em_llm | 0.840 | 0.726 | 1.000 | 0.109 | 0.165 | 15.0 | 32.000 | 0.00000 |
| hybrid_gap_emllm | 0.840 | 0.726 | 1.000 | 0.109 | 0.165 | 15.0 | 34.654 | 0.00000 |
| hybrid_gap_drift | 0.987 | 0.974 | 1.000 | 0.013 | 0.013 | 11.3 | 8.945 | 0.00000 |
| slm_boundary | 0.319 | 0.225 | 0.705 | 0.482 | 0.723 | 32.7 | 337968.311 | 0.00000 |

Higher F1/precision/recall is better; **lower Pk / WindowDiff is better**. `segs` is the mean number of predicted episodes per stream.
