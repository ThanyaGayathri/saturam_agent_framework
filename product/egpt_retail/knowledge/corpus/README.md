# Curated retail corpus

31 documents plus a table of contents, vendored from `Saturam-Inc/retailGPT` at commit
`2c0a176`. 483,339 words. This is **layer 1** of `docs/plan/domain-knowledge-layers.md` —
our own curated retail writing, shipped with every deployment.

Vendored rather than fetched at build time so a deployment has no network dependency on
another repository, and so the corpus a given release ingested is recoverable from that
release's tag. Re-sync by copying `research_docs/*.md` over this directory and updating the
commit above.

## Two things it is used for

**K2 ingests it as prose** — split on headings, tagged `origin=shipped`, into pgvector.

**K3 cites it.** The ontology's concepts each carry a `sources` list naming documents from
here, so a practitioner reviewing a concept can check it against the writing rather than
against the author's judgment.

The `Key Metrics and Benchmarks` sections were also harvested into
`../../kpi/retail_metrics.json` as a K11 input — 295 metrics with formulas and benchmarks.

## Excluded from ingestion

`00-table-of-contents.md` is navigation, not prose. Its chunks would retrieve as a wall of
anchors competing with the sections they point at. `read_corpus()` excludes it by default.

## Sourcing caveat, from the corpus itself

The corpus's own note says some figures are **approximate benchmarks** — directional
heuristics, not independently verified unless a report, publisher and year is cited alongside.
Treat them as non-authoritative defaults, and replace them with primary sources before any
number here becomes a customer-facing threshold.
