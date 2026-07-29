# Finding 02 — Embedder coverage gap (known, flagged not fixed)

## The gap

Both baseline providers in this PR (`LexicalProvider`, `RecencyDecayProvider`,
`harness/baselines.py`) rank by **lexical token overlap only**. **No embedder / no
vector similarity is exercised anywhere in this harness.**

## Why it is acceptable for this *plan* deliverable

- SM-R13 is a **plan + executable skeleton**: its job is to prove the metrics
  compute, discriminate providers, and drive a `MemoryProvider` uniformly — not to
  benchmark embedding retrieval quality. Lexical baselines do that with zero heavy
  deps, fully offline and deterministic.
- The harness is **embedder-agnostic by construction**: retrieval quality is scored
  from a provider's returned `source_ids` (recall@k / nDCG@k), independent of *how*
  the provider ranked. A real embedding-backed provider (SM-R8 retrieval / SM-R9
  PPR / the SM-R3 vector store) drops in behind the `MemoryProvider` protocol with
  no metric or runner change.
- **Embedder choice is explicitly out of scope** here — the embedding-model decision
  is owned by **SM-R4** (#79, ratified: `bge-base-en-v1.5`@768 Mode A /
  `text-embedding-3-large`→1024 Mode B) and consumed via the **SM-R11** seam. Baking a
  specific embedder into this plan would pre-empt that decision.

## The upside (deliberate)

Using no embedder also **sidesteps the wrong-embedder / dimension-mismatch class of
bug** that hit SM-R9 and earlier SM-R8/SM-R11 work — this PR cannot silently inherit
a bad embedding config.

## What is therefore NOT exercised (the honest cost)

- Semantic / paraphrase retrieval (lexical overlap misses "car" vs "vehicle").
- Embedding latency and cost at scale (the `query` p95 here reflects in-memory
  lexical scan, microseconds — a real vector search is the number SM-R3/SM-R8 gate).

## Recommendation for SM-X7 (the implementer)

Add at least one **embedding-backed provider adapter** (behind the same
`MemoryProvider` seam, using the **SM-R4** embedder (`bge-base`/`text-embedding-3-large`)
+ SM-R3 store) as a third baseline, and re-run the same metrics. No harness changes are
needed — only a new adapter in the registry. Until then, treat the retrieval numbers here as a **lexical floor**,
not a semantic-retrieval measurement.
