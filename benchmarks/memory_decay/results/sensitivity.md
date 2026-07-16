# Decay Parameter Sensitivity

## exp_recency.base

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 0.99 | 0.535 | 0.034 |
| 0.993 | 0.559 | 0.030 |
| 0.995 | 0.612 | 0.031 |
| 0.997 | 0.714 | 0.045 |
| 0.999 | 0.916 | 0.108 |

nDCG spread across sweep: 0.381

## exp_recency.gamma

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 0.0 | 0.532 | 0.034 |
| 0.25 | 0.616 | 0.039 |
| 0.5 | 0.612 | 0.031 |
| 0.75 | 0.548 | 0.026 |
| 1.0 | 0.500 | 0.021 |

nDCG spread across sweep: 0.116

## ttl_halflife.half_life_days

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 1.0 | 0.539 | 0.090 |
| 3.0 | 0.440 | 0.031 |
| 7.0 | 0.563 | 0.043 |
| 14.0 | 0.756 | 0.077 |
| 30.0 | 0.905 | 0.124 |

nDCG spread across sweep: 0.465

## importance_weighted.imp_factor

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 0.0 | 0.508 | 0.031 |
| 2.0 | 0.754 | 0.066 |
| 4.0 | 0.864 | 0.088 |
| 8.0 | 0.927 | 0.122 |
| 16.0 | 0.938 | 0.141 |

nDCG spread across sweep: 0.430

## ebbinghaus.s0_days

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 1.0 | 0.269 | 0.016 |
| 3.0 | 0.406 | 0.025 |
| 5.0 | 0.501 | 0.035 |
| 10.0 | 0.661 | 0.051 |
| 20.0 | 0.794 | 0.073 |

nDCG spread across sweep: 0.524

## ebbinghaus.reinforce_gain_days

| value | nDCG@k | stale-hit-rate |
|---|---|---|
| 0.0 | 0.546 | 0.031 |
| 2.0 | 0.490 | 0.023 |
| 5.0 | 0.501 | 0.035 |
| 10.0 | 0.530 | 0.038 |
| 20.0 | 0.530 | 0.048 |

nDCG spread across sweep: 0.056

