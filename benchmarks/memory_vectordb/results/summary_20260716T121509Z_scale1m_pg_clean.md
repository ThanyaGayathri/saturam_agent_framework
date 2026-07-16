# SM-R3 benchmark results (20260716T121509Z)

dim=768, k=10, queries=200, tenants=50

| backend | records | tenancy | tenants | recall@k | p50 ms | p95 ms | p99 ms | insert q/s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pgvector | 1000000 | filter | 50 | 1.0 | 375.871 | 771.195 | 1505.111 | 354.2 |
| pgvector | 1000000 | collection | 50 | 1.0 | 190.924 | 235.747 | 341.273 | 883.7 |
