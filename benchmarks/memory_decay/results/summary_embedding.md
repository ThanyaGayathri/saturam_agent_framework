# Episodic Decay Benchmark Results

Seeds: [0, 1, 2, 3, 4]  |  k = 5  |  relevance = embedding  |  candidates = 5

## Retrieval quality (ranking only, no pruning)

| strategy | nDCG@k | precision@k | recall@k | MRR | stale-hit-rate |
|---|---|---|---|---|---|
| no_decay | 0.903 | 0.201 | 1.000 | 0.870 | 0.151 |
| exp_recency | 0.415 | 0.099 | 0.489 | 0.391 | 0.015 |
| ttl_halflife | 0.385 | 0.090 | 0.442 | 0.365 | 0.023 |
| importance_weighted | 0.703 | 0.158 | 0.782 | 0.677 | 0.059 |
| ebbinghaus | 0.373 | 0.087 | 0.429 | 0.355 | 0.017 |

Higher nDCG/precision/recall/MRR is better; lower stale-hit-rate is better.

## Recall by fact-age bucket (ranking only)

| strategy | 0-1d | 1-7d | 7-30d | 30d+ |
|---|---|---|---|---|
| no_decay | 1.000 | 1.000 | 1.000 | 1.000 |
| exp_recency | 1.000 | 0.991 | 0.401 | 0.127 |
| ttl_halflife | 1.000 | 0.996 | 0.370 | 0.014 |
| importance_weighted | 1.000 | 0.996 | 0.858 | 0.459 |
| ebbinghaus | 0.937 | 0.672 | 0.262 | 0.380 |

How well each strategy still finds the current fact as it ages.

## Pruning behaviour (prune enabled)

| strategy | prune-precision | prune-recall | false-forget | retained-final |
|---|---|---|---|---|
| no_decay | 0.000 | 0.000 | 0.0 | 439 |
| exp_recency | 0.367 | 0.700 | 26.2 | 160 |
| ttl_halflife | 0.386 | 0.631 | 24.6 | 188 |
| importance_weighted | 0.282 | 0.242 | 0.6 | 292 |
| ebbinghaus | 0.332 | 0.759 | 28.4 | 131 |

prune-precision: of pruned, fraction already dead. prune-recall: of dead, fraction pruned. false-forget: pruned while still needed (lower is better).
