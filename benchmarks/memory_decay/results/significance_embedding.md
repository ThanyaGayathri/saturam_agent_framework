# Paired-bootstrap significance -- embedding

Chosen = importance_weighted. Paired bootstrap over pooled per-query values; Holm-corrected over the family. delta = chosen - rival (positive favours chosen).

| comparison | delta | 95% CI | p (Holm) | significant |
|---|---|---|---|---|
| importance_weighted vs no_decay [recall] | -0.2168 | [-0.242, -0.192] | 0.0000 | yes |
| importance_weighted vs exp_recency [recall] | +0.2932 | [+0.265, +0.323] | 0.0000 | yes |
| importance_weighted vs ttl_halflife [recall] | +0.3410 | [+0.312, +0.370] | 0.0000 | yes |
| importance_weighted vs ebbinghaus [recall] | +0.3508 | [+0.316, +0.385] | 0.0000 | yes |
| importance_weighted vs no_decay [ndcg] | -0.2000 | [-0.226, -0.173] | 0.0000 | yes |
| importance_weighted vs exp_recency [ndcg] | +0.2876 | [+0.263, +0.313] | 0.0000 | yes |
| importance_weighted vs ttl_halflife [ndcg] | +0.3193 | [+0.293, +0.346] | 0.0000 | yes |
| importance_weighted vs ebbinghaus [ndcg] | +0.3269 | [+0.295, +0.358] | 0.0000 | yes |
