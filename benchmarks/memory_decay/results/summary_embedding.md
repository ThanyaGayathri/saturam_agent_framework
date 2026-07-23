# Episodic Decay Benchmark Results

Seeds: [0, 1, 2, 3, 4]  |  k = 5  |  relevance = embedding  |  candidates = 5

Values are mean +/- per-seed std. See DECISION.md for paired-bootstrap significance.

## Retrieval quality (ranking only, no pruning)

| strategy | nDCG@k | recall@k | MRR | stale-hit | stale-hit (versioned) |
|---|---|---|---|---|---|
| no_decay | 0.903±0.011 | 1.000±0.000 | 0.870 | 0.151 | 0.347 |
| exp_recency | 0.415±0.025 | 0.489±0.028 | 0.391 | 0.015 | 0.034 |
| ttl_halflife | 0.385±0.035 | 0.442±0.042 | 0.365 | 0.023 | 0.052 |
| importance_weighted | 0.703±0.019 | 0.782±0.021 | 0.677 | 0.059 | 0.136 |
| ebbinghaus | 0.373±0.040 | 0.429±0.039 | 0.355 | 0.017 | 0.039 |

Higher nDCG/recall/MRR is better; lower stale-hit is better. 'stale-hit (versioned)' restricts to queries that can actually produce a same-topic stale hit.

## Recall by idle-age bucket (ranking only) -- decay resistance

| strategy | 0-1d | 1-7d | 7-30d | 30d+ |
|---|---|---|---|---|
| no_decay | 1.000 | 1.000 | 1.000 | 1.000 |
| exp_recency | 1.000 | 0.973 | 0.152 | 0.000 |
| ttl_halflife | 1.000 | 0.968 | 0.143 | 0.000 |
| importance_weighted | 1.000 | 0.997 | 0.678 | 0.029 |
| ebbinghaus | 0.984 | 0.741 | 0.063 | 0.005 |

Bucketed by time since the fact was last surfaced (the clock decay acts on). This measures resistance to decay, not re-access frequency.

## Recall by fact calendar-age bucket (ranking only) -- transparency

| strategy | 0-1d | 1-7d | 7-30d | 30d+ |
|---|---|---|---|---|
| no_decay | 1.000 | 1.000 | 1.000 | 1.000 |
| exp_recency | 1.000 | 0.991 | 0.401 | 0.127 |
| ttl_halflife | 1.000 | 0.996 | 0.370 | 0.014 |
| importance_weighted | 1.000 | 0.996 | 0.858 | 0.459 |
| ebbinghaus | 0.937 | 0.672 | 0.262 | 0.380 |

Bucketed by fact calendar-age; conflates re-access, so not the decay metric.

## Pruning behaviour (prune enabled)

| strategy | prune-precision | prune-recall | false-forget | post-prune recall | retained-final |
|---|---|---|---|---|---|
| no_decay | 0.000 | 0.000 | 0.0 | 1.000 | 439 |
| exp_recency | 0.906 | 0.675 | 26.2 | 0.489 | 160 |
| ttl_halflife | 0.902 | 0.594 | 24.6 | 0.442 | 188 |
| importance_weighted | 0.996 | 0.394 | 0.6 | 0.782 | 292 |
| ebbinghaus | 0.908 | 0.761 | 28.4 | 0.429 | 131 |

prune-precision: of pruned, fraction never needed again (noise counts as correct). prune-recall: of the disposable-by-horizon set (incl. noise), fraction pruned. false-forget: pruned while still needed later (lower is better). post-prune recall: recall measured in this same prune-enabled pass.
