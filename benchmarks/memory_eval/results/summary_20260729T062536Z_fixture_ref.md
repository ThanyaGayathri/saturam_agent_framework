# Memory eval — run summary

- Generated (UTC): 20260729T062536Z
- Mode: fixture
- Judge: stub

> NOTE: fixture mode runs the tiny committed smoke set. Numbers prove the harness computes end-to-end and that metrics discriminate providers; they are NOT for ranking real systems. Use `--mode full` on the real corpora for that.

| benchmark | provider | k | QA | temporal | recall@k | nDCG@k | bitemporal | as_of_leak | decay(supp/outr) | mem(items/tok) | ingest p95 | query p95 | cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| custom_temporal | recency_decay | 5 | 100.0% | 100.0% | 100.0% | 1.000 | 100.0% | 0.00 | 0.00/1.00 | 3/24 | 0.00ms | 0.02ms | $0.0000 |
| dmr | recency_decay | 5 | 100.0% | - | 100.0% | 1.000 | - | - | - | 3/23 | 0.00ms | 0.01ms | $0.0000 |
| locomo[0] | recency_decay | 5 | 66.7% | 100.0% | 100.0% | 0.833 | - | - | - | 4/26 | 0.00ms | 0.01ms | $0.0000 |
| longmemeval | recency_decay | 5 | 66.7% | 100.0% | 100.0% | 1.000 | 100.0% | 0.00 | - | 7/52 | 0.00ms | 0.01ms | $0.0000 |

Columns: QA = LLM-judged answer correctness; temporal = QA over {temporal, knowledge_update, as_of_t}; recall@k / nDCG@k over probes with gold evidence; bitemporal = QA over {as_of_t, knowledge_update}; as_of_leak = fraction of as-of probes whose top-1 retrieval is NOT valid at T (two-sided: future OR stale-superseded; SM-R10 t_valid<=T<t_invalid; lower better); decay supp/outr = stale-target suppression@k / gold-outranks-stale (higher better); mem = labelless footprint (stored items / token proxy — lower = more consolidated).
