# Event Segmentation Benchmark Results

dataset: synthetic  |  config: default  |  n: 5  |  tolerance: +/-1 events  |  relevance: synthetic

| approach | F1 (mean±sd) | precision | recall | Pk (mean±sd) | WinDiff | segs | latency(ms) |
|---|---|---|---|---|---|---|---|
| single_session | 0.000±0.000 | 0.000 | 0.000 | 0.465±0.024 | 0.465 | 1.0 | 0.001 |
| per_turn | 0.161±0.019 | 0.088 | 1.000 | 0.535±0.024 | 1.000 | 112.0 | 0.003 |
| fixed_window | 0.271±0.100 | 0.256 | 0.290 | 0.423±0.094 | 0.423 | 11.6 | 0.004 |
| time_gap | 0.691±0.198 | 1.000 | 0.568 | 0.201±0.125 | 0.201 | 6.2 | 0.027 |
| embedding_drift | 0.970±0.043 | 0.962 | 0.978 | 0.023±0.022 | 0.023 | 10.8 | 5.225 |
| surprise_em_llm | 0.846±0.033 | 0.752 | 0.978 | 0.096±0.034 | 0.147 | 13.8 | 18.285 |
| hybrid_gap_emllm | 0.846±0.033 | 0.752 | 0.978 | 0.096±0.034 | 0.147 | 13.8 | 18.145 |
| hybrid_gap_drift | 0.970±0.043 | 0.962 | 0.978 | 0.021±0.019 | 0.021 | 10.8 | 5.162 |

Higher F1/precision/recall is better; **lower Pk / WindowDiff is better**. sd = standard deviation over the averaged units. `segs` is the mean predicted episodes per stream.
