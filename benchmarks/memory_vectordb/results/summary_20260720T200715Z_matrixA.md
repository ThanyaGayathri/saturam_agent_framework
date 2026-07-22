# SM-R3 benchmark results v2 (20260720T200715Z)

dataset=GloVe-100-angular, k=10, ef_search=64, queries=1000, repeats=3, seed=7

| backend | total | per-tenant | tenants | tenancy | recall@k | p95 ms | p95/rep | leaks | insert q/s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pgvector | 1000000 | 100000 | 10 | filter | 0.5637 | 25.005 | [25.591, 24.206, 25.011] | 0 | 65.9 |
| pgvector | 1000000 | 100000 | 10 | collection | 0.8812 | 20.671 | [21.61, 19.935, 20.351] | 0 | 389.0 |
| pgvector | 1000000 | 100000 | 10 | partition | 0.8806 | 15.181 | [15.379, 14.91, 14.867] | 0 | 438.8 |
| qdrant | 1000000 | 100000 | 10 | filter | 0.7809 | 766.703 | [775.026, 778.679, 739.912] | 0 | 3111.8 |
| qdrant | 1000000 | 100000 | 10 | collection | 1.0 | 13.388 | [13.61, 13.386, 13.067] | 0 | 5503.0 |
| milvus |  | 100000 | 10 | filter | ERROR | ERROR | ERROR | ERROR | ERROR |
| milvus |  | 100000 | 10 | collection | ERROR | ERROR | ERROR | ERROR | ERROR |
