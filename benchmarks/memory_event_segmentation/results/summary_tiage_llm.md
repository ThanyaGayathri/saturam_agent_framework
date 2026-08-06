# Event Segmentation Benchmark Results

dataset: tiage  |  config: default  |  n: 100  |  tolerance: +/-1 events  |  relevance: embedding

| approach | F1 (mean±sd) | precision | recall | Pk (mean±sd) | WinDiff | segs | latency(ms) | cost($) |
|---|---|---|---|---|---|---|---|---|
| single_session | 0.000±0.000 | 0.000 | 0.000 | 0.459±0.164 | 0.459 | 1.0 | 0.002 | 0.00000 |
| per_turn | 0.343±0.142 | 0.216 | 0.970 | 0.541±0.164 | 0.983 | 15.6 | 0.004 | 0.00000 |
| fixed_window | 0.252±0.247 | 0.560 | 0.170 | 0.507±0.161 | 0.511 | 2.0 | 0.005 | 0.00000 |
| embedding_drift | 0.537±0.258 | 0.646 | 0.523 | 0.451±0.192 | 0.468 | 3.5 | 1.596 | 0.00000 |
| surprise_em_llm | 0.368±0.299 | 0.604 | 0.298 | 0.444±0.190 | 0.453 | 2.2 | 1.412 | 0.00000 |
| llm_boundary | 0.622±0.281 | 0.686 | 0.664 | 0.332±0.224 | 0.374 | 4.3 | 1611.435 | 0.00203 |

Higher F1/precision/recall is better; **lower Pk / WindowDiff is better**. sd = standard deviation over the averaged units. `segs` is the mean predicted episodes per stream.

_time_gap and the hybrids are omitted: TIAGE has no per-message timestamps._
