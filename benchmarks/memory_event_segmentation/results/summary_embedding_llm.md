# Event Segmentation Benchmark Results

dataset: synthetic  |  config: default  |  n: 5  |  tolerance: +/-1 events  |  relevance: embedding

| approach | F1 (mean±sd) | precision | recall | Pk (mean±sd) | WinDiff | segs | latency(ms) | cost($) |
|---|---|---|---|---|---|---|---|---|
| single_session | 0.000±0.000 | 0.000 | 0.000 | 0.465±0.024 | 0.465 | 1.0 | 0.002 | 0.00000 |
| per_turn | 0.161±0.019 | 0.088 | 1.000 | 0.535±0.024 | 1.000 | 112.0 | 0.007 | 0.00000 |
| fixed_window | 0.271±0.100 | 0.256 | 0.290 | 0.423±0.094 | 0.423 | 11.6 | 0.011 | 0.00000 |
| time_gap | 0.691±0.198 | 1.000 | 0.568 | 0.201±0.125 | 0.201 | 6.2 | 0.064 | 0.00000 |
| embedding_drift | 0.682±0.088 | 0.550 | 0.917 | 0.306±0.083 | 0.353 | 17.4 | 10.828 | 0.00000 |
| surprise_em_llm | 0.724±0.019 | 0.646 | 0.834 | 0.194±0.038 | 0.267 | 13.6 | 44.765 | 0.00000 |
| hybrid_gap_emllm | 0.799±0.054 | 0.699 | 0.939 | 0.130±0.013 | 0.193 | 14.0 | 44.979 | 0.00000 |
| hybrid_gap_drift | 0.711±0.092 | 0.571 | 0.961 | 0.279±0.087 | 0.331 | 17.6 | 12.421 | 0.00000 |
| llm_boundary | 0.877±0.069 | 0.816 | 0.956 | 0.078±0.040 | 0.100 | 12.4 | 2375.572 | 0.00907 |

Higher F1/precision/recall is better; **lower Pk / WindowDiff is better**. sd = standard deviation over the averaged units. `segs` is the mean predicted episodes per stream.
