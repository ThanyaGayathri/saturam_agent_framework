# Episodic Decay Benchmark Results

Seeds: [0, 1, 2, 3, 4]  |  k = 5  |  relevance = synthetic  |  candidates = 5

Values are mean +/- per-seed std. See DECISION.md for paired-bootstrap significance.

## Retrieval quality (ranking only, no pruning)

| strategy | nDCG@k | recall@k | MRR | stale-hit | stale-hit (versioned) |
|---|---|---|---|---|---|
| no_decay | 0.893±0.024 | 1.000±0.000 | 0.856 | 0.151 | 0.347 |
| exp_recency | 0.614±0.010 | 0.718±0.016 | 0.579 | 0.039 | 0.091 |
| ttl_halflife | 0.586±0.036 | 0.664±0.040 | 0.560 | 0.049 | 0.112 |
| importance_weighted | 0.866±0.017 | 0.937±0.015 | 0.842 | 0.110 | 0.254 |
| ebbinghaus | 0.485±0.038 | 0.548±0.029 | 0.465 | 0.031 | 0.073 |

Higher nDCG/recall/MRR is better; lower stale-hit is better. 'stale-hit (versioned)' restricts to queries that can actually produce a same-topic stale hit.

## Recall by idle-age bucket (ranking only) -- decay resistance

| strategy | 0-1d | 1-7d | 7-30d | 30d+ |
|---|---|---|---|---|
| no_decay | 1.000 | 1.000 | 1.000 | 1.000 |
| exp_recency | 1.000 | 1.000 | 0.574 | 0.130 |
| ttl_halflife | 1.000 | 1.000 | 0.620 | 0.007 |
| importance_weighted | 1.000 | 1.000 | 0.990 | 0.362 |
| ebbinghaus | 0.997 | 0.904 | 0.244 | 0.019 |

Bucketed by time since the fact was last surfaced (the clock decay acts on). This measures resistance to decay, not re-access frequency.

## Recall by fact calendar-age bucket (ranking only) -- transparency

| strategy | 0-1d | 1-7d | 7-30d | 30d+ |
|---|---|---|---|---|
| no_decay | 1.000 | 1.000 | 1.000 | 1.000 |
| exp_recency | 1.000 | 1.000 | 0.721 | 0.442 |
| ttl_halflife | 1.000 | 1.000 | 0.788 | 0.161 |
| importance_weighted | 1.000 | 1.000 | 0.996 | 0.787 |
| ebbinghaus | 0.983 | 0.870 | 0.410 | 0.411 |

Bucketed by fact calendar-age; conflates re-access, so not the decay metric.

## Pruning behaviour (prune enabled)

| strategy | prune-precision | prune-recall | false-forget | post-prune recall | retained-final |
|---|---|---|---|---|---|
| no_decay | 0.000 | 0.000 | 0.0 | 1.000 | 439 |
| exp_recency | 0.922 | 0.663 | 21.0 | 0.686 | 168 |
| ttl_halflife | 0.915 | 0.593 | 21.0 | 0.661 | 193 |
| importance_weighted | 1.000 | 0.394 | 0.0 | 0.937 | 292 |
| ebbinghaus | 0.906 | 0.740 | 28.0 | 0.538 | 140 |

prune-precision: of pruned, fraction never needed again (noise counts as correct). prune-recall: of the disposable-by-horizon set (incl. noise), fraction pruned. false-forget: pruned while still needed later (lower is better). post-prune recall: recall measured in this same prune-enabled pass.
