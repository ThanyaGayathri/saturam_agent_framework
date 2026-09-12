# AZ-B decisions: embedder dimension and hybrid-scoring reconciliation

Package **AZ-B** (`docs/plan/azure-migration-plan.md`), closing gap #3 in
`/Users/rasik/.claude/plans/shimmying-twirling-piglet.md`. Both decisions below are made once,
here, and **AZ-C inherits them verbatim** — the migration plan is explicit that re-deriving either
independently in Track C risks disagreeing with Track B.

Environment note: this repo has no Azure CLI, no Azure subscription, and no live Azure AI Search
service reachable in this session. Both decisions below are informed by (a) the
`azure-search-documents` SDK's own type definitions, installed and inspected directly
(`azure.search.documents.indexes.models.SearchField`, `VectorSearch*`, `VectorizedQuery`), pinned
at `azure-search-documents==11.6.0`, and (b) Microsoft Learn documentation fetched via web search
in this session (cited below) — not from memory and not assumed. Nothing here is verified against
a running Azure AI Search resource; see `TEST_LOG.md` for the boundary between what is and is not
proven.

## Decision 1 — keep the 1024-dimension truncation

**Decision: keep `MODE_DEFAULTS[ProviderMode.FRONTIER]` at `text-embedding-3-large` truncated to
1024 dimensions. Do not widen to the native 3072 (or the SDK default of 1536) now that pgvector's
2000-dim HNSW ceiling no longer constrains the vector store.**

### Why the constraint disappears but the number doesn't change

`libs/agno/egpt/integrations/memory/vector/pgvector/sql.py:87-90` (`MAX_HNSW_DIM = 2000`) is a
**pgvector-specific** ceiling: pgvector's HNSW index type tops out at 2000 dimensions, and a
`vector` column wider than that has no index any search can use, so every query becomes an exact
scan (SM-R3 Finding 3's failure mode). That is the sole reason `libs/agno/egpt/memory/embedding.py`
truncates Mode B's `text-embedding-3-large` (native width 3072) down to 1024 — see the module's
own comment: "truncated because the full 3072 exceeds pgvector's 2000-dim `vector` limit while
1024 ties it (p=0.82)."

Azure AI Search does not share that ceiling. Per Microsoft Learn's [Vector Index
Limits](https://learn.microsoft.com/en-us/azure/search/vector-search-index-size) page (fetched
this session): the maximum dimensions per vector field is 3072, and HNSW with half-precision
(`Edm.Half`) supports indexes up to 4,000 dimensions — both comfortably above 3072. The SDK's own
`SearchField.vector_search_dimensions: Optional[int]` (in
`azure.search.documents.indexes.models`, inspected directly from the installed package) carries no
narrower hard limit in its docstring. So the mechanical reason 1024 was chosen — "it's forced" —
is genuinely gone for this backend.

### Why the number stays 1024 anyway

The forcing reason disappearing does not imply the forced value was wrong; it only removes one
argument *for* it. The remaining evidence still points at 1024:

1. **The tie is statistical, not an artifact of truncation being cheap.** `embedding.py`'s own
   comment states Mode B at 1024 is "a statistical tie with Mode A [768] rather than an upgrade
   (Holm n.s.)" — this was measured against Mode A's 768, not against pgvector's ceiling. Nothing
   about *where the vectors are stored* changes what the eval measured. There is no equivalent
   measurement of 3072 (or 1536) beating 1024 on this repo's memory-representative eval — only the
   absence of a storage-side reason to avoid it. Absence of a blocker is not evidence of a
   benefit, and this decision doc treats it as such rather than hedging.
2. **Widening triples (or 1.5x's) storage and query cost for a tie, not a win.** Azure AI Search
   bills and scales by vector index size; HNSW query latency and index build time both grow with
   dimension. Moving to 3072 with no measured recall improvement over 1024 is pure cost.
3. **Cross-backend consistency matters more here than in the pgvector-only world.** The seam
   (`egpt.memory.substrates.VectorSubstrate`) is backend-swappable by registry key precisely so a
   deployment can move between pgvector and Azure AI Search without re-deriving application-level
   assumptions. `embedding.py`'s `_version_tag` bakes the dimension into the embedding-version tag
   that gates re-embedding migrations (SPEC §11.3); keeping the dimension identical across backends
   means a deployment cutting over from pgvector to Azure AI Search is a backend swap, not also a
   forced re-embedding of every stored memory. Widening now would make AZ-B a re-embedding
   migration wearing a backend-swap's clothes.
4. **A future widening is still available and cheap to gate.** If a real Azure AI Search recall
   benchmark (the thing this environment cannot run — see `TEST_LOG.md`) someday shows 3072 beats
   1024 by more than noise on real Azure infrastructure specifically, that is a new measurement
   that can change `MODE_DEFAULTS` for this backend. Nothing here forecloses it; it just refuses to
   spend 3x the storage on an untested hope today.

**Net:** `AzureAISearchMemorySubstrate` takes `dim` from the same `MemoryEmbedder` pipeline
(`egpt.memory.embedding.resolve_memory_embedder`) as `PgVectorMemorySubstrate` does, unchanged.
The vector field this backend declares on its Azure AI Search index is
`Collection(Edm.Single)` at exactly `pipeline.dimensions` — 768 for Mode A, 1024 for Mode B — with
no backend-specific override. `AZ-C` (K2, the plain `VectorDb` consumer) inherits this: whatever
dimension K1's pipeline resolves is what K2's Azure-backed `VectorDb` should also index at, for
the same re-embedding-avoidance argument.

## Decision 2 — hybrid legs stay separate; no native RRF in the memory substrate

**Decision: `AzureAISearchMemorySubstrate.search_candidates` issues two independent single-leg
Azure AI Search queries — a vector-only query and a text-only (BM25) query — and returns them
unfused, exactly as `HybridSearchSubstrate.search_candidates` requires. It does not use Azure AI
Search's native hybrid/RRF query shape (`search_text=... , vector_queries=[...]` in one call) for
this port.**

### This is not a stylistic choice — the two are structurally incompatible

`egpt.memory.substrates.HybridSearchSubstrate.search_candidates` returns a
`RetrievalCandidates(vector=[...], lexical=[...])` — **two separate ranked lists, deliberately
unfused** (SM-V3 #97; see `RetrievalCandidates`'s own docstring in
`libs/agno/egpt/memory/types.py`: "Fusing the lists is **not** part of this port"). This mirrors
exactly what `PgVectorMemorySubstrate.search_candidates`
(`libs/agno/egpt/integrations/memory/vector/pgvector/pgvector.py:1033-1075`) does: two statements
in one pinned-snapshot transaction, one ranked by cosine distance and one by `ts_rank_cd`, returned
as two lists a fusion stage (SM-H2 #132) consumes later.

Azure AI Search's native hybrid search — one `search()` call carrying both `search_text` and
`vector_queries` — does the opposite: per Microsoft Learn's [Vector Relevance and
Ranking](https://learn.microsoft.com/en-us/azure/search/vector-search-ranking) page (fetched this
session), "If you run two or more vector queries in parallel, or if you do a hybrid search that
combines vector and text queries in the same request, Reciprocal Rank Fusion (RRF) is used for
scoring the final search results" — i.e. it returns **one already-fused ranked list** with one
`@search.score` per document. There is no SDK option to get RRF's inputs back separately from one
hybrid call. So native RRF cannot satisfy this Protocol regardless of any scoring-semantic
preference: the port wants two lists, RRF-in-one-call produces one. Using it here would mean
either (a) faking a second list by re-running one of the legs anyway (defeating the point of using
RRF at all), or (b) violating the Protocol's explicit "unfused" contract. Neither is acceptable.

**This settles the question for the memory substrate by construction, not by preference between
RRF and a weighted blend.** The two single-leg queries this substrate issues are the same shape
`PgVector`'s memory substrate uses — a pure `vector_queries=[VectorizedQuery(...)]` call with no
`search_text`, and a pure `search_text=...` call with no `vector_queries` — run as two separate
requests (Azure AI Search's REST API has no cross-request transaction/snapshot isolation
comparable to Postgres's `REPEATABLE READ`; see `TEST_LOG.md` for what this means for the
snapshot-consistency property pgvector's implementation buys and this one cannot).

### Per-leg score normalization (the piece that *is* a real decision)

Each leg's `@search.score` has a different native shape, and the port requires a similarity in
`[0.0, 1.0]`, higher-is-better, monotonic in the underlying measure (matching
`VectorSubstrate.search`'s contract that `PgVectorMemorySubstrate` already satisfies via
`normalize_score`/`_lexical_similarity`):

- **Vector leg.** Per the same Microsoft Learn ranking page: for the `cosine` metric (this
  substrate's only supported metric, matching the pipeline's own cosine assumption —
  `embedding.py`'s `_METRIC = "cos"`), Azure AI Search computes
  `@search.score = 1 / (1 + cosine_distance)`, which is **already** in `(0.0, 1.0]`, monotonically
  decreasing in distance (so increasing in similarity), and already a similarity rather than a
  distance. This is passed through directly with no further transform — no `normalize_score` call
  needed, because Azure has already done the pgvector backend's `<=>`-to-`[0,1]` conversion for us
  natively, on the wire.
- **Lexical leg.** Azure AI Search's text-query `@search.score` is an unbounded BM25 relevance
  score with no documented upper bound — the same shape `ts_rank_cd` has in pgvector. This
  substrate applies the identical saturating transform pgvector's `_lexical_similarity` uses,
  `r / (r + k)` with the same `k = 0.1` constant (`_RANK_SATURATION` in
  `pgvector/pgvector.py`), reused verbatim rather than re-derived, because it is strictly
  increasing (cannot reorder the list) and gives the two backends' lexical legs comparable score
  magnitudes for whatever later reads them. This is a implementation-detail parity choice, not a
  measured BM25-vs-`ts_rank_cd` calibration — SM-H2 (#132), the fusion stage, is where recalibrating
  either matters, exactly as `pgvector.py`'s own docstring says about its lexical leg.

### What this means for AZ-C (the plain `VectorDb`, out of scope here but named per the plan)

`egpt.integrations.vectordb.pgvector.PgVector.hybrid_search` (referenced at
`libs/agno/egpt/integrations/vectordb/pgvector/pgvector.py:1067-1222`) is a **different port** —
`VectorDb`, not `VectorSubstrate`/`HybridSearchSubstrate` — and it returns **one** blended-score
list already (`vector_score_weight * vector_score + text_rank_weight * text_rank`), not two. That
port's shape is compatible with Azure AI Search's native RRF in a way this one is not: both produce
one final ranked list with one score per document. AZ-C's Azure-backed `VectorDb` implementation is
therefore free to use native RRF for `hybrid_search` — recommended, since re-deriving a client-side
weighted blend there would fight the platform rather than use it — but it must document plainly
that the resulting `score` field holds Azure's RRF-derived score, not the same
`vector_score_weight * vector_score + text_rank_weight * text_rank` magnitude pgvector produces.
Ordering-based callers are unaffected; a caller reading the absolute score value as a calibrated
number is not, and AZ-C must say so rather than imply parity. This is the one place this document
deliberately does *not* extend AZ-B's own choice (separate legs) to AZ-C, because the two ports
have different contracts and forcing one answer onto both would be the wrong kind of consistency.

## A third, smaller decision: pinning the SDK to the 11.x line

`azure-search-documents` publishes a `12.0.0` alongside the long-lived `11.x` line (`pip index
versions azure-search-documents` in this session lists `12.0.0, 11.6.0, 11.5.3, ...`). `12.0.0` was
downloaded and inspected directly: it is a **fully regenerated low-level client** --
`azure.search.documents.SearchClient` is now built from
`_SearchClientOperationsMixin`/`_operations.py` with none of the 11.x line's pythonic convenience
methods (`upload_documents`, `merge_or_upload_documents`, the `search()` keyword-argument surface
this module's code depends on), and `azure.search.documents.indexes.models`'s `SimpleField`/
`SearchableField` builder functions are gone in favour of raw generated model classes. This is a
different SDK shape, not a version bump of the same one. `11.6.0` was downloaded and inspected in
the same way and confirmed to carry the classic, documented shape -- `SearchClient.search(
search_text=..., vector_queries=..., filter=...)`, `SearchIndexClient.create_or_update_index`,
`azure.search.documents.indexes.models.SimpleField`/`SearchableField`/`SearchField` -- that this
module, virtually every published Azure AI Search example, and the Microsoft Learn pages cited
above all assume. `libs/agno/pyproject.toml` pins `azure-search-documents==11.6.0` under the new
`azure-ai-search` extra for this reason: not a conservative default, a deliberate avoidance of a
different and unfamiliar API surface with no corresponding documentation or examples to build
against confidently in an environment with no live service to validate against either way.

## What is deliberately not decided here

Matching `PgVectorMemorySubstrate`'s own "deliberately not here" list: rank fusion and its knobs
(SM-H2 #132) are not implemented by this substrate either — `search_candidates` returns the two
raw legs and stops. Per-tenant physical isolation at the index level (Azure AI Search's analogue of
pgvector's per-tenant partition + HNSW index, which SM-R3 measured as necessary for pgvector's
shared-table recall to not collapse with tenant count) is **not** attempted here: this substrate
uses one shared index with `tenant_id` (and the other scope slots) as an OData `$filter`, the same
shared-table shape SM-R3 measured pgvector's recall collapsing under at tenant count. Azure AI
Search's filtered-vector-search implementation is a different engine with different filter-push-
down behavior than pgvector's post-HNSW filter, so this is **not** assumed to have the same
degradation curve — but it is also not measured, because that measurement needs a real Azure AI
Search resource at a representative tenant count, which this environment cannot provide. Recorded
here rather than silently shipped as if it were known-good; see `TEST_LOG.md`.
