# Episodic Decay Benchmark Results

Seeds: [0, 1, 2, 3, 4]  |  k = 5  |  relevance = synthetic  |  candidates = 5

## Retrieval quality (ranking only, no pruning)

| strategy | nDCG@k | precision@k | recall@k | MRR | stale-hit-rate |
|---|---|---|---|---|---|
| no_decay | 0.893 | 0.201 | 1.000 | 0.856 | 0.151 |
| exp_recency | 0.614 | 0.145 | 0.718 | 0.579 | 0.039 |
| ttl_halflife | 0.586 | 0.134 | 0.664 | 0.560 | 0.049 |
| importance_weighted | 0.866 | 0.189 | 0.937 | 0.842 | 0.110 |
| ebbinghaus | 0.485 | 0.111 | 0.548 | 0.465 | 0.031 |

Higher nDCG/precision/recall/MRR is better; lower stale-hit-rate is better.

## Recall by fact-age bucket (ranking only)

| strategy | 0-1d | 1-7d | 7-30d | 30d+ |
|---|---|---|---|---|
| no_decay | 1.000 | 1.000 | 1.000 | 1.000 |
| exp_recency | 1.000 | 1.000 | 0.721 | 0.442 |
| ttl_halflife | 1.000 | 1.000 | 0.788 | 0.161 |
| importance_weighted | 1.000 | 1.000 | 0.996 | 0.787 |
| ebbinghaus | 0.983 | 0.870 | 0.410 | 0.411 |

How well each strategy still finds the current fact as it ages.

## Pruning behaviour (prune enabled)

| strategy | prune-precision | prune-recall | false-forget | retained-final |
|---|---|---|---|---|
| no_decay | 0.000 | 0.000 | 0.0 | 439 |
| exp_recency | 0.371 | 0.671 | 21.0 | 168 |
| ttl_halflife | 0.392 | 0.615 | 21.0 | 193 |
| importance_weighted | 0.277 | 0.236 | 0.0 | 292 |
| ebbinghaus | 0.319 | 0.710 | 28.0 | 140 |

prune-precision: of pruned, fraction already dead. prune-recall: of dead, fraction pruned. false-forget: pruned while still needed (lower is better).
