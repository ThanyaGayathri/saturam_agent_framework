# SM-R3 benchmark results (20260716T131502Z)

dim=768, k=10, queries=200, tenants=50

| backend | records | tenancy | tenants | recall@k | p50 ms | p95 ms | p99 ms | insert q/s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| qdrant | 1000000 | filter | 50 | 0.2945 | 302.123 | 547.594 | 2085.917 | 442.4 |
| qdrant | 1000000 | collection | 50 | 1.0 | 54.305 | 86.153 | 160.085 | 917.1 |
