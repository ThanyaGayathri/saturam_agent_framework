# memory_graph_engine — SM-R2 temporal-graph engine spike

Decision deliverable for **SM-R2** ([#77](https://github.com/Saturam-Inc/aicoe-tracker/issues/77)):
which engine backs the Symbiotic Memory temporal knowledge graph. The issue names
Graphiti-over-Neo4j as the *preferred* option; this spike measures it and **does not adopt it**.

**Read [SPIKE.md](SPIKE.md) first** — it is the recommendation. This README is how to run the
comparison.

**Recommendation: raw Neo4j (Community) + SM-R10's custom bi-temporal layer. Route A.**

| Candidate | Bi-temporal (SM-R10's 30 probes) | Personalized PPR |
|---|---|---|
| **Raw Neo4j + custom (SM-R10, shipped)** | **30/30 = 100%** | ✅ `gds.pageRank(sourceNodes)` |
| Graphiti library over Neo4j | **19/30 = 63.3%** | ✅ (same engine) |
| Cognee | 8/30 **inexpressible** | ❌ |
| `lightrag` | no temporal surface at all | ❌ |
| *single-time baseline (SM-R10)* | 17/30 = 56.7% | — |

## What this folder does and does not build

This is a **decision task**, so it deliberately builds as little as possible:

- **Graphiti and Cognee** are the two genuinely untested candidates, so they get fresh,
  comparable prototypes here.
- **Raw Neo4j + custom** is SM-R10's shipped work. It is **cited, not rebuilt** — its 30/30 is
  SM-R10's measurement, and re-deriving it would only re-measure the same rule.
- **Ground truth is imported**, not authored: `scenarios.py` comes from
  [`../memory_bitemporal`](../memory_bitemporal). Both engines are scored on one ruler.

## Layout

| Path | What |
|---|---|
| [SPIKE.md](SPIKE.md) | The recommendation (mirrors #77 question-by-question). |
| [harness/graphiti_store.py](harness/graphiti_store.py) | Adapter driving the **real `graphiti-core`** temporal engine over SM-R10's op/probe protocol. |
| [run_graphiti.py](run_graphiti.py) | Scores graphiti-core on SM-R10's 30 probes. |
| [forensics.py](forensics.py) | Row-level dump showing *why* Graphiti loses the past (3 rows stored where SCD-2 stores 5). |
| [run_cognee.py](run_cognee.py) | Structural probe of cognee's temporal model + read path. Re-checks every claim at runtime. |
| [run_ppr_route.py](run_ppr_route.py) | Route A test: real `gds.pageRank(sourceNodes)` on Neo4j **Community**, incl. over an SM-R10 5a-filtered projection. |
| [results/](results) | Committed run output (JSON, per-probe detail). |

## Run

```bash
python3 -m venv .venvs/graphengine
.venvs/graphengine/bin/python -m pip install graphiti-core cognee kuzu neo4j

# Neo4j 5 (reuses SM-R10's container, so the substrate matches its measurements)
cd benchmarks/memory_bitemporal/docker && docker compose -p smr10 -f neo4j-compose.yml up -d

# Neo4j + the GDS plugin, for the Route A / PPR test
docker run -d --name smr2-gds -p 7690:7687 -e NEO4J_AUTH=neo4j/gdstestpassword \
  -e NEO4J_PLUGINS='["graph-data-science"]' neo4j:5

# FalkorDB, for the PPR-capability comparison
docker run -d --name smr2-falkor -p 6390:6379 falkordb/falkordb:latest

PYTHONPATH=. .venvs/graphengine/bin/python benchmarks/memory_graph_engine/run_graphiti.py
PYTHONPATH=. .venvs/graphengine/bin/python benchmarks/memory_graph_engine/forensics.py
PYTHONPATH=. .venvs/graphengine/bin/python benchmarks/memory_graph_engine/run_cognee.py
PYTHONPATH=. .venvs/graphengine/bin/python benchmarks/memory_graph_engine/run_ppr_route.py
```

Teardown: `docker rm -f smr2-gds smr2-falkor` (SM-R10's container is shared — leave it, or use
its own compose teardown).

**No credentials needed.** `run_graphiti.py` bypasses Graphiti's LLM deliberately (valid-times are
supplied from the timeline), which isolates the temporal model *and* is favourable to Graphiti.
`run_cognee.py` is a structural probe and needs no LLM.

## What the numbers do and do not establish

Stated up front, because a results table that lists only successes is how unvalidated work gets
mistaken for validated work.

| Claim | Status |
|---|---|
| graphiti-core scores 19/30 on SM-R10's probes | **measured** — real library, real Neo4j 5.26, per-probe detail committed |
| Graphiti's failures are write-model failures, not query artifacts | **measured** — the read path used is the *most favourable* correct 5a/5b form; `forensics.py` shows the missing rows |
| Cognee has no transaction axis; 8 probes inexpressible | **measured structurally** — re-asserted at runtime against cognee 1.4.2 |
| Cognee end-to-end `cognify` behaviour | **NOT measured** — needs LLM credentials. The finding rests on the data model + its own docstring, which no pipeline run can change |
| `lightrag` cannot carry a time predicate | **measured** — source inspection; `async_search` discards filters |
| Neo4j Community + GDS gives genuine personalized PPR | **measured** — incl. a check that different seeds give different rankings |
| SM-R10's 5a predicate composes with PPR at projection time | **measured** — mechanically, on temporal data (5 rels → 4, ranking changes) |
| PPR *ranking quality* over a bi-temporal projection | **NOT measured** — SM-H4's ([#134](https://github.com/Saturam-Inc/aicoe-tracker/issues/134)). Filtering a projection is not the same as ranking well over it |
| FalkorDB / Kuzu lack personalized PPR | **measured** — full procedure list / rejected parameters |
| Latency / throughput at scale | **NOT measured** — the PPR graphs here are 3–5 nodes; they establish capability, not performance |
| GDS license terms | **NOT resolved** — the shipped jar's LICENSE.txt and NOTICE.txt contradict each other; flagged for legal |

## Core untouched

`git diff origin/main -- libs/` is empty. This task is decision-only.
