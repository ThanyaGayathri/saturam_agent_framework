<!--
SM-V1 (#95) discharging the measurement ADR 0003 section A.3b makes the ratified recall target
conditional on. Numbers produced by ef_sweep.py against the shipped substrate; raw JSON in
results/ef_sweep_smv1_100k.json.
-->
# SM-V1 — `ef_search` sweep at the ratified operating point

- **Tracking issue:** [Memory] SM-V1 — Saturam-Inc/aicoe-tracker#95
- **Discharges:** ADR 0003 section A.3b, which ratifies `Recall@10 >= 0.95` **conditional** on
  SM-V1 running this sweep and reporting recall *and* p95 together.
- **Supersedes nothing.** SM-R3's decision (pgvector, partitioned by tenant) is unchanged; this
  measures the one operating point it left open.

## Why it was needed

SM-R3 measured a constant `ef_search = 64` and found **recall 0.881 at 100k/tenant** — inside the
ratified 100k–1M band and below the 0.95 target. Its text says only that a higher `ef` *"narrows"*
the gap *"at some latency cost"*, and its `results/` contains no sweep. The ADR refused to assume
the gap closes and made the target conditional:

> If the sweep cannot reach 0.95 within the 50 ms budget, the ratified position becomes: pgvector's
> floor is its measured 0.881 at that operating point, and 0.95 becomes the SM-R3 escalation trigger
> to Qdrant. It is **not** grounds to quietly lower the floor and stay on pgvector.

## Method

Driven through `PgVectorMemorySubstrate` — the layout this ticket ships (one partition and one HNSW
index per tenant, the scope predicate on every read) — not through a benchmark's own table.

- **Real embeddings:** GloVe-100-angular, the source SM-R3 v2 used. Recall on i.i.d. Gaussian
  vectors is pinned at 1.0 and measures nothing.
- **100k/tenant × 3 tenants** (300k vectors), `k = 10`, 200 queries, seeded.
- Queries are in-tenant record vectors with small Gaussian noise — a realistic "find similar
  memories" probe whose true neighbours sit in a dense region.
- Ground truth is **exact cosine top-k within the query's tenant**, computed in numpy, so a
  shortfall is the index's and not the scope predicate's.
- Reproduce: `python -m benchmarks.memory_vectordb.ef_sweep --per-tenant 100000 --tenants 3`

## Result

| `ef_search` | recall@10 | p50 ms | p95 ms |
| --- | --- | --- | --- |
| 16 | 0.763 | 3.18 | 29.55 |
| 32 | 0.839 | 2.31 | 3.89 |
| 64 | 0.889 | 2.15 | 2.77 |
| 128 | 0.930 | 2.84 | 3.43 |
| **256** | **0.967** | 4.46 | **7.07** |
| 512 | 0.986 | 6.60 | 10.39 |

**The gate resolves in pgvector's favour.** `ef_search = 256` reaches **0.967** — above the ratified
0.95 — at a **p95 of 7.07 ms**, seven times inside the 50 ms budget. The escalation to Qdrant
per-tenant collections is **not** triggered at this operating point.

`ef = 64` reproduces SM-R3 independently: 0.889 here against its 0.881, on a different harness and a
different machine. That agreement is the reason to trust the rest of the column.

**So the shipped default is `ef_search = 256`**, and it stays a per-deployment parameter — the trade
moves with corpus size, dimension and hardware, which is exactly why the ADR refused to fix it at
the benchmark's constant.

## What these numbers are not

- **Not comparable to SM-R3's latencies.** Its p95 at `ef = 64` was 15.2 ms against 2.77 ms here:
  different hardware, container settings and client. What transfers is the *shape* — recall rises
  with `ef`, latency follows — and the recall column, which is a property of the index and the
  data.
- **Three tenants, not SM-R3's ten or fifty.** Under per-tenant partitioning each query traverses
  only its own partition's index, so tenant count changes total data volume rather than the index a
  query walks. That is the reasoning for the reduction, not a measurement of it; SM-R3 already
  measured tenant-count scaling on this layout and found it *improves* (1.000 at 50 tenants).
- **200 queries, single repeat, no concurrency**, against SM-R3's 1000 × 3. Enough to separate
  0.889 from 0.967; not enough to rank two adjacent `ef` values whose recall differs in the third
  decimal.
- **The p95 at `ef = 16` (29.55 ms) is an artefact** of the first queries in the run paying cold
  cache, not a property of a narrow search — its p50 is 3.18 ms and every wider setting is faster.
  A warmup pass would remove it; it is left in rather than tuned away, since the row it appears on
  is not the one the decision rests on.

## Still open, and not this ticket's

- **The memory→vector fan-out ratio** (ADR section A.1, "HELD OPEN"): vectors-per-memory on real
  tenant data, with graph memory on and off. It needs a real tenant corpus; synthesising one would
  produce a number that looks measured and is not. Capacity stays planned **in vectors**.
- **The 1M/tenant end of the band.** This sweep covers 100k/tenant, where SM-R3's gap was. At 1M
  the same sweep should be re-run before the envelope is committed to.
