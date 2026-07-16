# SM-R3 benchmark results (20260716T103948Z)

dim=768, k=10, queries=200, tenants=50

| backend | records | tenancy | tenants | recall@k | p50 ms | p95 ms | p99 ms | insert q/s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| milvus | 100000 | filter | 50 | 0.93 | 31.967 | 65.028 | 112.41 | 62.4 |
