# Episodic Decay Benchmark Results

Seeds: [0, 1, 2, 3, 4]  |  k = 5  |  candidates = 5

## Retrieval quality (ranking only, no pruning)

| strategy | nDCG@k | precision@k | recall@k | MRR | stale-hit-rate |
|---|---|---|---|---|---|
| no_decay | 0.891 | 0.200 | 1.000 | 0.853 | 0.147 |
| exp_recency | 0.612 | 0.142 | 0.709 | 0.580 | 0.031 |
| ttl_halflife | 0.563 | 0.129 | 0.641 | 0.537 | 0.043 |
| importance_weighted | 0.864 | 0.188 | 0.939 | 0.839 | 0.088 |
| ebbinghaus | 0.501 | 0.112 | 0.560 | 0.482 | 0.035 |

Higher nDCG/precision/recall/MRR is better; lower stale-hit-rate is better.

## Recall by fact-age bucket (ranking only)

| strategy | 0-1d | 1-7d | 7-30d | 30d+ |
|---|---|---|---|---|
| no_decay | 1.000 | 1.000 | 1.000 | 1.000 |
| exp_recency | 1.000 | 1.000 | 0.731 | 0.427 |
| ttl_halflife | 1.000 | 1.000 | 0.762 | 0.176 |
| importance_weighted | 1.000 | 1.000 | 0.982 | 0.828 |
| ebbinghaus | 1.000 | 0.884 | 0.459 | 0.402 |

How well each strategy still finds the current fact as it ages.

## Pruning behaviour (prune enabled)

| strategy | prune-precision | prune-recall | false-forget | retained-final |
|---|---|---|---|---|
| no_decay | 0.000 | 0.000 | 0.0 | 438 |
| exp_recency | 0.365 | 0.703 | 22.8 | 164 |
| ttl_halflife | 0.382 | 0.622 | 21.6 | 192 |
| importance_weighted | 0.280 | 0.235 | 0.0 | 295 |
| ebbinghaus | 0.325 | 0.720 | 29.0 | 136 |

prune-precision: of pruned, fraction already dead. prune-recall: of dead, fraction pruned. false-forget: pruned while still needed (lower is better).
