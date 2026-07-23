# Event Segmentation Benchmark Results

dataset: synthetic  |  config: hard  |  n: 5  |  tolerance: +/-1 events  |  relevance: embedding

| approach | F1 (mean±sd) | precision | recall | Pk (mean±sd) | WinDiff | segs | latency(ms) | cost($) |
|---|---|---|---|---|---|---|---|---|
| single_session | 0.000±0.000 | 0.000 | 0.000 | 0.465±0.024 | 0.465 | 1.0 | 0.002 | 0.00000 |
| per_turn | 0.161±0.019 | 0.088 | 1.000 | 0.535±0.024 | 1.000 | 112.0 | 0.005 | 0.00000 |
| fixed_window | 0.271±0.100 | 0.256 | 0.290 | 0.423±0.094 | 0.423 | 11.6 | 0.008 | 0.00000 |
| time_gap | 0.425±0.064 | 0.308 | 0.712 | 0.409±0.049 | 0.593 | 22.8 | 0.055 | 0.00000 |
| embedding_drift | 0.682±0.088 | 0.550 | 0.917 | 0.306±0.083 | 0.353 | 17.4 | 10.168 | 0.00000 |
| surprise_em_llm | 0.724±0.019 | 0.646 | 0.834 | 0.194±0.038 | 0.267 | 13.6 | 42.650 | 0.00000 |
| hybrid_gap_emllm | 0.503±0.034 | 0.348 | 0.921 | 0.377±0.040 | 0.606 | 26.6 | 42.397 | 0.00000 |
| hybrid_gap_drift | 0.470±0.057 | 0.323 | 0.882 | 0.419±0.055 | 0.630 | 27.6 | 10.949 | 0.00000 |
| llm_boundary | 0.876±0.051 | 0.812 | 0.956 | 0.082±0.035 | 0.102 | 12.4 | 1779.788 | 0.00907 |

Higher F1/precision/recall is better; **lower Pk / WindowDiff is better**. sd = standard deviation over the averaged units. `segs` is the mean predicted episodes per stream.
