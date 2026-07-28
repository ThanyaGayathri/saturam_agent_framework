# Paired-bootstrap significance -- synthetic

Chosen = importance_weighted. Paired bootstrap over pooled per-query values; Holm-corrected over the family. delta = chosen - rival (positive favours chosen).

| comparison | delta | 95% CI | p (Holm) | significant |
|---|---|---|---|---|
| importance_weighted vs no_decay [recall] | -0.0621 | [-0.077, -0.048] | 0.0000 | yes |
| importance_weighted vs exp_recency [recall] | +0.2206 | [+0.194, +0.247] | 0.0000 | yes |
| importance_weighted vs ttl_halflife [recall] | +0.2722 | [+0.245, +0.300] | 0.0000 | yes |
| importance_weighted vs ebbinghaus [recall] | +0.3883 | [+0.356, +0.419] | 0.0000 | yes |
| importance_weighted vs no_decay [ndcg] | -0.0267 | [-0.046, -0.008] | 0.0056 | yes |
| importance_weighted vs exp_recency [ndcg] | +0.2531 | [+0.230, +0.277] | 0.0000 | yes |
| importance_weighted vs ttl_halflife [ndcg] | +0.2796 | [+0.253, +0.306] | 0.0000 | yes |
| importance_weighted vs ebbinghaus [ndcg] | +0.3799 | [+0.350, +0.408] | 0.0000 | yes |
