# SM-R3 benchmark results v2 (20260721T021033Z)

dataset=GloVe-100-angular, k=10, ef_search=64, queries=1000, repeats=3, seed=7

| backend | total | per-tenant | tenants | tenancy | recall@k | p95 ms | p95/rep | leaks | insert q/s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pgvector | 1000000 | 20000 | 50 | filter | 0.2173 | 23.042 | [23.799, 22.194, 22.738] | 0 | 61.9 |
| pgvector | 1000000 | 20000 | 50 | collection | 0.9502 | 16.9 | [17.17, 17.585, 15.407] | 0 | 640.9 |
| pgvector | 1000000 | 20000 | 50 | partition | 1.0 | 13.728 | [13.889, 13.575, 13.595] | 0 | 813.4 |
| qdrant | 1000000 | 20000 | 50 | filter | 0.9864 | 901.654 | [811.331, 938.146, 934.566] | 0 | 3594.2 |
| qdrant | 1000000 | 20000 | 50 | collection | 1.0 | 14.419 | [17.086, 12.63, 12.383] | 0 | 5045.9 |
