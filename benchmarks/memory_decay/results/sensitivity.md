# Decay Parameter Sensitivity

## exp_recency.base

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 0.99 | 0.549 | 0.036 |
| 0.993 | 0.571 | 0.035 |
| 0.995 | 0.614 | 0.039 |
| 0.997 | 0.732 | 0.057 |
| 0.999 | 0.912 | 0.123 |

nDCG spread across sweep: 0.363

## exp_recency.gamma

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 0.0 | 0.537 | 0.040 |
| 0.25 | 0.636 | 0.046 |
| 0.5 | 0.614 | 0.039 |
| 0.75 | 0.559 | 0.031 |
| 1.0 | 0.506 | 0.022 |

nDCG spread across sweep: 0.129

## ttl_halflife.half_life_days

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 1.0 | 0.553 | 0.089 |
| 3.0 | 0.445 | 0.033 |
| 7.0 | 0.586 | 0.049 |
| 14.0 | 0.781 | 0.090 |
| 30.0 | 0.901 | 0.138 |

nDCG spread across sweep: 0.456

## importance_weighted.imp_factor

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 0.0 | 0.506 | 0.035 |
| 2.0 | 0.785 | 0.080 |
| 4.0 | 0.866 | 0.110 |
| 8.0 | 0.928 | 0.133 |
| 16.0 | 0.933 | 0.146 |

nDCG spread across sweep: 0.426

## importance_weighted.base_half_life_days

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 2.0 | 0.672 | 0.050 |
| 3.0 | 0.780 | 0.076 |
| 5.0 | 0.866 | 0.110 |
| 7.0 | 0.910 | 0.128 |
| 14.0 | 0.935 | 0.146 |

nDCG spread across sweep: 0.263

## ebbinghaus.s0_days

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 1.0 | 0.237 | 0.016 |
| 3.0 | 0.384 | 0.023 |
| 5.0 | 0.485 | 0.031 |
| 10.0 | 0.656 | 0.049 |
| 20.0 | 0.811 | 0.083 |

nDCG spread across sweep: 0.574

## ebbinghaus.reinforce_gain_days

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 0.0 | 0.559 | 0.035 |
| 2.0 | 0.479 | 0.025 |
| 5.0 | 0.485 | 0.031 |
| 10.0 | 0.499 | 0.038 |
| 20.0 | 0.497 | 0.045 |

nDCG spread across sweep: 0.080

