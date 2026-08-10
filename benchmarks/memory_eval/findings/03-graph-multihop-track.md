# Finding 03 — Graph / multi-hop retrieval track (D8's graph half)

Raised by the design review (Major): the retrieval metrics were flat
`recall`/`hit`/`nDCG` over turn-ids, with no multi-hop retrieval metric, no
graph-traversal / PPR path, and no graph dataset — yet **SM-R9** (multi-hop need +
HippoRAG PPR) and **SM-R10 SS12.1(c)** (bi-temporal PPR) both assign *"PPR ranking
quality over a time-filtered (bi-temporal) projection"* to a downstream eval — this
one (SM-R13 / SM-X7). D8 is vector **+ graph**; an eval that measures only the
vector/QA half cannot validate the system. This note gives that composition a home.

## Wired now (in this PR)

- **`all_hops_hit@k`** (`harness/metrics.py`) — the discriminative multi-hop
  RETRIEVAL metric: `1` iff **every** gold `evidence_id` (all bridge turns) is in
  top-k, else `0`; defined only for multi-hop probes (`|evidence| >= 2`).
  `hit@k`/`recall@k` reward partial retrieval; `all_hops_hit@k` does not. Reported as
  `retrieval.all_hops_hit_at_k` (+ `n_multihop`), in the results table, unit-tested.
- **Multi-hop probes exist in the fixtures** — e.g. the LOCOMO fixture probe
  "What did Alice start learning after moving?" has evidence `["D1:1","D2:1"]`
  (two hops across sessions), so the metric is exercised offline today.
- **Bi-temporal + multi-hop compose**: `all_hops_hit@k` and the two-sided top-k
  `as_of_leak_rate` are independent report fields, so a probe can require both "all
  hops" AND "valid at T" — the SM-R10-SS12.1(c) shape.

## Scoped out to SM-X7 (named owner) — with the seam it plugs into

The following need real substrates and are **out of scope for this plan**, assigned
to **SM-X7** (the harness implementer), consuming upstream decisions unchanged
behind the `MemoryProvider` seam:

| Deferred piece | Owner | Consumes |
|---|---|---|
| Graph-traversal / **PPR retrieval provider** (an adapter that ranks by personalized PageRank over the graph) | SM-X7 | **SM-R9** PPR decision; **SM-G1** graph store |
| **Bridge-passage graph dataset** (multi-hop questions whose hops are graph edges, e.g. a HotpotQA/MuSiQue-style set or LongMemEval multi-session) | SM-X7 | public multi-hop sets (fetch-on-demand, `findings/01` licensing rule) |
| **Bi-temporal-graph PPR probe** — PPR ranking quality over a **time-filtered** (as-of) graph projection | SM-X7 | **SM-R10** bi-temporal read (`t_valid<=T<t_invalid`) + SM-R9 PPR |

**Why scoped, not built here:** a PPR/graph provider needs a graph engine (SM-G1 /
SM-R2) and the SM-R9 PPR ranking — building a stub of either in this plan would
pre-empt those decisions and duplicate SM-R7/SM-G1 territory. The harness is
**graph-ready**: the metric (`all_hops_hit@k`) and the probe shape (multi-hop +
`as_of`) are defined and tested now; a graph/PPR provider drops in behind the
existing `MemoryProvider` seam and is scored by the same runner with **no harness
change** — exactly the swap the two baselines already prove.

## Acceptance for the reviewer's Major

The multi-hop metric + probe shape (the *measuring instrument* R13 owns) is wired
and tested; the graph/PPR *provider* + bridge dataset (the *runnable subject*) is
explicitly scoped to SM-X7 with the upstream owners named — rather than left with
"no home". Cross-referenced in `PLAN.md §2.6` and §6.
