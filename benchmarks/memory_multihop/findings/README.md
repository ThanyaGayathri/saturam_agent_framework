# Findings — SM-R9 multi-hop / HippoRAG spike

Implementation notes and local fixes discovered while building the prototype. None
of these touch core (`libs/agno/egpt/**`); they are handoff notes for the SM-H4
implementation task. Core is verified untouched (`git diff <base> -- libs/` empty).

## F1 — OpenIE output is not reliably valid JSON (robustness required)

**What:** With `gpt-5.4` (Azure Responses) in JSON mode, a fraction of OpenIE
passes return *malformed* JSON — typically an unescaped `"` inside an entity
surface form (e.g. a nickname or a quoted work title), which breaks `json.loads`
mid-array. A naive `json.loads` on the extraction output crashes the whole index
on the first bad passage.

**Fix (in-spike, `harness/openie.py`):** parse defensively — try strict
`json.loads`, and on failure fall back to a regex salvage of well-formed
`["s","r","o"]` triples, counting any total miss in `EXTRACTION_FAILURES` and
surfacing the count in the run summary (`openie_failures`). One malformed passage
now costs at most that passage's triples, not the run.

**Handoff to SM-H4:** the production KG-write path must treat extraction as
best-effort and idempotent per passage: validate/repair JSON, quarantine failures
for retry, and record an extraction-failure metric. Do **not** assume the LLM
returns parseable JSON. Prefer a structured-output/tool-schema call if the chosen
model supports guaranteed schemas, with the regex salvage as a backstop.

## F2 — Entity embedding must be batched, not per-call

**What:** The HippoRAG index embeds every unique KG entity. Embedding them
one-at-a-time through `Embedder.get_embedding` made indexing dominated by network
round-trips (~1.4 s/entity → ~90 min at full corpus scale).

**Fix (in-spike, `harness/embeddings.py`):** `CachedEmbedder` collects all
uncached texts and calls `async_get_embeddings_batch_and_usage` in batches
(default 256), collapsing thousands of calls into a handful. Indexing entity
embedding dropped from the dominant cost to negligible.

**Handoff to SM-H4:** the write-side consolidation loop should batch entity/edge
embeddings (and reuse a cache keyed by surface form) — this is the difference
between a minutes-scale and an hour-scale index build.

## F3 — Seed context path check (rebrand)

The issue cites `vectordb/lightrag/lightrag.py` as the existing graph-RAG path.
Confirmed present at `libs/agno/egpt/vectordb/lightrag/lightrag.py` (namespace is
`egpt`, not `agno`). It is a thin HTTP client to an external LightRAG server with
**no** personalized-PageRank retrieval (modes: local/global/hybrid only), so it is
an *integration route*, not a reuse-as-is source for PPR. See SPIKE.md §Seed
context for the full disposition.
