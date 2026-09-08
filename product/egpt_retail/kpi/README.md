# Retail metric catalogue — a K11 input

`retail_metrics.json` holds **295 retail metric definitions**, harvested from the
*Key Metrics and Benchmarks* sections of the curated retail corpus
(`Saturam-Inc/retailGPT`, `research_docs`). 30 of the 31 documents contributed; every entry
carries a formula, an industry benchmark, and the document it came from.

## What this is not

**Not a KPI registry, and not executable.** A harvested formula reads
`COGS / Average Inventory at Cost` — it names quantities, not columns. Turning one into a
`KpiDefinition` needs `source_tables` and SQL bound to a specific customer's schema, which is
K11's job with the semantic layer. **Feasibility cannot be computed from this file**, because
feasibility is a set difference over source tables and this file has none.

So treat an entry as a *definition a customer's data may or may not be able to support*, which
is the honest state of a metric before a harvest has run.

## Why it exists

It replaces invention. Without it, K11's registry and K12's suggestion agent propose metrics
from nothing, and a model that can invent a metric name will — plausibly. With it, a proposal
can cite a curated definition and an industry benchmark, and a reviewer can check it against
the source document.

The benchmark column is the part that is hard to get any other way: `Inventory Turns` of
14–20x for grocery and 4–6x for apparel is the kind of number that makes a KPI actionable
rather than merely computed.

## Caveat carried from the corpus

The corpus's own sourcing note says some figures are *approximate benchmarks* — directional
heuristics, not independently verified unless a report, publisher and year is cited alongside.
Treat benchmarks as non-authoritative defaults. Replace them with primary sources before any
of them reaches a customer-facing threshold or alert.
