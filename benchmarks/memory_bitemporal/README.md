# memory_bitemporal — SM-R10 bi-temporal model spec + prototype

Design/spec deliverable for **SM-R10** ([#85](https://github.com/Saturam-Inc/aicoe-tracker/issues/85)):
the bi-temporal edge model (valid time + transaction time, invalidate-not-delete) for the
Symbiotic Memory graph tier. The spec is testable — a runnable prototype demonstrates
point-in-time "true at T" reconstruction and non-destructive correction on three backends.

**Read [SPEC.md](SPEC.md) first** — it is the implementable artifact (fields, rules, query
predicates, migration). This README is how to run the prototype.

## Layout

| Path | What |
|---|---|
| [SPEC.md](SPEC.md) | The spec (mirrors #85 field-by-field). |
| [models.py](models.py) | `BiTemporalEdge` (4-timestamp, epoch-int) + `EdgeOutcome`. |
| [store.py](store.py) | `BiTemporalEdgeStore` abstraction: invalidation rule + query semantics (sync + async). |
| [adapters/](adapters) | `reference` (in-memory), `single_time` (Option B baseline), `postgres`, `neo4j`, and the **native** `sql_native` / `cypher_native` equivalence adapters. |
| [vector_tier.py](vector_tier.py) | 5c filter-only valid-time for the vector/episodic tier (SPEC §9). |
| [schema.sql](schema.sql) | Postgres DDL: `memory_edge`, `bt_upsert_edge`/`bt_retract_edge`, the append-only trigger, 5a/5b, vector-tier migration. |
| [graph_schema.cypher](graph_schema.cypher) | Neo4j constraints, R1/R2, 5a/5b. |
| [extraction.py](extraction.py) | `ValidTimeExtractor` (rule-based default + optional LLM). |
| [scenarios.py](scenarios.py) | 11 edge scenarios / 30 probes + 4 vector-tier scenarios / 10 filter probes (timeline-derived ground truth). |
| [harness.py](harness.py) / [run.py](run.py) | Scoring, cross-backend equivalence, multi-backend runner. |
| [results/](results) | Committed run output (JSON + summary). |
| [findings/](findings) | Phase-1 foundation notes + the model-correction trail. |

## Run

The reference backend and the extractor need **no services** (only `PYTHONPATH=libs/agno` so the
prototype can reuse `egpt.utils.dttm`). Postgres + Neo4j runs need the two containers.

```bash
# 1. Dedicated venv (drivers). Reference-only runs don't need this.
python3 -m venv .venvs/bitemporal
.venvs/bitemporal/bin/pip install "psycopg[binary]" neo4j

# 2. Start the stores (unique names/ports: pg 5533, neo4j 7688/7475)
cd benchmarks/memory_bitemporal/docker
docker compose -p smr10 -f pg-compose.yml    up -d
docker compose -p smr10 -f neo4j-compose.yml up -d
cd -

# 3. Run the benchmark (skips pg/neo4j automatically if unreachable)
PYTHONPATH=libs/agno .venvs/bitemporal/bin/python -m benchmarks.memory_bitemporal.run

# teardown
cd benchmarks/memory_bitemporal/docker && docker compose -p smr10 -f pg-compose.yml down -v && docker compose -p smr10 -f neo4j-compose.yml down -v
```

Env overrides: `BT_PG_DSN`, `BT_NEO4J_URI` / `BT_NEO4J_USER` / `BT_NEO4J_PASSWORD`.

## Quality metric

**Point-in-time reconstruction accuracy** = fraction of `(subject, predicate, T[, TT])` probes
whose returned fact equals the timeline-derived ground truth. Target **1.00** for the bi-temporal
implementation. Latest run: reference **100% (30/30)**, single-time baseline **56.7% (17/30)**,
5c vector-tier filter **10/10**, rule-based extraction **6/6**.

## What the numbers do and do not establish

The invalidation rule and query semantics live once, in [store.py](store.py); the reference,
Postgres and Neo4j adapters implement storage primitives only. Those backends agreeing is
**one shared rule over three storage layers**, not three independent derivations — it establishes
that the four-timestamp model round-trips losslessly through `timestamptz` and Neo4j `datetime`.

The SQL-native `bt_upsert_edge()` and the 5a/5b SQL/Cypher predicates — what SM-G4/G5/X11 actually
ship — are driven end-to-end by `sql_native` / `cypher_native` and compared **per probe** against
the reference (`Equivalence vs reference` in the run output). Those two paths are **written and
wired but not yet measured**: no container runtime was available. `run.py` prints
`[not measured]` and records `native_paths_measured` in the results JSON so a run that skipped
them cannot be mistaken for one that passed them. See [TEST_LOG.md](TEST_LOG.md).

## Scope

Decision/spec artifact only. Core (`libs/agno/egpt/**`) is untouched
(`git diff origin/main -- libs/` empty). Provisional targets await SM-R1 (#76).
