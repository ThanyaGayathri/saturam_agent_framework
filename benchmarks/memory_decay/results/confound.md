# Importance-confound sensitivity

importance_weighted vs the best pure-recency peer (exp_recency/ebbinghaus) as importance is decorrelated from kind. margin > 0 means salience-weighting still helps.

| importance_noise | IW recall | peer recall | recall margin | IW idle-30d+ | peer idle-30d+ | idle-30d+ margin |
|---|---|---|---|---|---|---|
| 0.00 | 0.937 | 0.718 | +0.219 | 0.362 | 0.130 | +0.232 |
| 0.25 | 0.921 | 0.700 | +0.221 | 0.288 | 0.103 | +0.185 |
| 0.50 | 0.897 | 0.644 | +0.252 | 0.215 | 0.047 | +0.168 |
| 0.75 | 0.858 | 0.554 | +0.304 | 0.141 | 0.010 | +0.131 |
| 1.00 | 0.806 | 0.499 | +0.307 | 0.118 | 0.039 | +0.080 |
