# SM-R5 Phase-2 ablations

Seeds: [0, 1, 2, 3, 4]  |  k = 5

## Prune-gate ablation (recency-only vs DECISION §4 three-condition)

| strategy | gate | precision | recall | false-forget | post-prune recall | retained |
|---|---|---|---|---|---|---|
| exp_recency | recency | 0.922 | 0.663 | 21.0 | 0.686 | 168 |
| exp_recency | three_condition | 1.000 | 0.176 | 0.0 | 0.718 | 373 |
| ttl_halflife | recency | 0.915 | 0.593 | 21.0 | 0.661 | 193 |
| ttl_halflife | three_condition | 0.915 | 0.593 | 21.0 | 0.661 | 193 |
| importance_weighted | recency | 1.000 | 0.394 | 0.0 | 0.937 | 292 |
| importance_weighted | three_condition | 1.000 | 0.101 | 0.0 | 0.937 | 402 |
| ebbinghaus | recency | 0.906 | 0.740 | 28.0 | 0.538 | 140 |
| ebbinghaus | three_condition | 1.000 | 0.192 | 0.0 | 0.548 | 367 |

## Reinforcement ablation -- synthetic (all-top-k vs valid-only)

| strategy | policy | recall | stale-hit | stale-hit (versioned) | idle-30d+ |
|---|---|---|---|---|---|
| no_decay | all_topk | 1.000 | 0.151 | 0.347 | 1.000 |
| no_decay | valid_only | 1.000 | 0.151 | 0.347 | 1.000 |
| exp_recency | all_topk | 0.718 | 0.039 | 0.091 | 0.130 |
| exp_recency | valid_only | 0.892 | 0.055 | 0.127 | 0.598 |
| ttl_halflife | all_topk | 0.664 | 0.049 | 0.112 | 0.007 |
| ttl_halflife | valid_only | 0.741 | 0.049 | 0.114 | 0.055 |
| importance_weighted | all_topk | 0.937 | 0.110 | 0.254 | 0.362 |
| importance_weighted | valid_only | 0.962 | 0.092 | 0.211 | 0.642 |
| ebbinghaus | all_topk | 0.548 | 0.031 | 0.073 | 0.019 |
| ebbinghaus | valid_only | 0.808 | 0.048 | 0.111 | 0.496 |

## Reinforcement ablation -- embedding (all-top-k vs valid-only)

| strategy | policy | recall | stale-hit | stale-hit (versioned) | idle-30d+ |
|---|---|---|---|---|---|
| no_decay | all_topk | 1.000 | 0.151 | 0.347 | 1.000 |
| no_decay | valid_only | 1.000 | 0.151 | 0.347 | 1.000 |
| exp_recency | all_topk | 0.489 | 0.015 | 0.034 | 0.000 |
| exp_recency | valid_only | 0.570 | 0.021 | 0.048 | 0.062 |
| ttl_halflife | all_topk | 0.442 | 0.023 | 0.052 | 0.000 |
| ttl_halflife | valid_only | 0.491 | 0.020 | 0.046 | 0.000 |
| importance_weighted | all_topk | 0.782 | 0.059 | 0.136 | 0.029 |
| importance_weighted | valid_only | 0.802 | 0.050 | 0.116 | 0.055 |
| ebbinghaus | all_topk | 0.429 | 0.017 | 0.039 | 0.005 |
| ebbinghaus | valid_only | 0.504 | 0.017 | 0.039 | 0.076 |

