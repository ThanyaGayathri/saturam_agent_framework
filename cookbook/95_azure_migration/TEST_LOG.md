# Azure migration -- test log

One folder, one log, per `docs/plan/azure-migration-plan.md`. Entries are appended, tagged by
package (AZ-A/AZ-B/AZ-C/AZ-D/AZ-P0). Do not overwrite another package's entry.

Note on merge conflicts: several AZ packages create this file fresh from the same base, so the
only conflict git raises across them is "added by both" on this shared header -- the resolution
is always "keep one header, concatenate the package sections," never picking a side. No entry is
ever lost to a merge; see any of the individual package PRs for the same note at the point it was
first hit.

### AZ_D_entra_scopes.py

**Package:** AZ-D
**Status:** PASS

**Description:** Ran against a real local HTTP server serving a real RSA-signed JWKS document
(no mocks) through the real `AuthMiddleware`. Three scenarios: (A) Entra's `scp` claim -- a
space-delimited string -- resolved via `scopes_claim="scp"`, `scopes_claim_format=
"space_delimited"`; (B) Entra's `roles` claim -- already a list -- resolved via
`scopes_claim="roles"` (format has no effect on a list); (C) the framework's own default
`scopes` claim, completely unconfigured, run over a JWKS *URL* instead of a JWKS file (gap
#2), proving the default behaviour is unaffected by the JWKS-URL code path.

**Result:**
```
Serving a real JWKS document over HTTP at http://127.0.0.1:60764/jwks.json

--- Scenario A: scp (space-delimited) ---
status=200 body={'user_id': 'alice', 'scopes': ['agents.read', 'agents.run']}
PASS: resolved scopes ['agents.read', 'agents.run'] == expected ['agents.read', 'agents.run']

--- Scenario B: roles (list, unaffected by format) ---
status=200 body={'user_id': 'bob', 'scopes': ['Retail.Admin', 'Retail.Viewer']}
PASS: resolved scopes ['Retail.Admin', 'Retail.Viewer'] == expected ['Retail.Admin', 'Retail.Viewer']

--- Scenario C: default scopes claim, unchanged behaviour, over a JWKS URL ---
status=200 body={'user_id': 'carol', 'scopes': ['agents:read']}
PASS: resolved scopes ['agents:read'] == expected ['agents:read']

All AZ-D scenarios passed: scopes_claim/scopes_claim_format expansion and the
JWKS URL path both resolve real RS256 tokens to the expected internal scopes,
through the real AuthMiddleware, with zero mocks.
```

All three hand-computed expected-scope sets matched exactly. Also verified separately (unit
tests, `libs/agno/tests/unit/os/test_scopes_claim.py` and `test_jwks_url.py`): the OLD
behaviour (default `scopes` claim, a bare string coercing to a one-element list) is unchanged
when the new fields are left at their defaults, a malformed claim shape (int, `None`, dict)
still resolves to `[]` rather than crashing, and a genuine local JWKS file path still goes
through the original `open()`+`json.load()` code path (no `PyJWKClient` involved). Full
`libs/agno/tests/unit/os/` suite: 2271 passed (3 pre-existing, unrelated failures needing
`OPENAI_API_KEY`, confirmed present identically on `origin/main` before this change).

---

### AZ_A_blob_roundtrip.py

**Package:** AZ-A
**Status:** PASS

**Description:** Ran against a real local Azurite instance (`mcr.microsoft.com/azure-storage/azurite:latest`,
started with `docker run -d --name egpt-azurite -p 10000:10000 -p 10001:10001 -p 10002:10002
mcr.microsoft.com/azure-storage/azurite:latest azurite --blobHost 0.0.0.0 --queueHost 0.0.0.0
--tableHost 0.0.0.0`). Uploaded C1's four committed landing fixtures
(`product/egpt_retail/contracts/_fixtures/landing/raw/...`) into a fresh `egpt-landing`
container via Azurite's well-known development connection string. Listed the container
through `AzureBlobObjectStore.list_objects`, read every object back through `get_bytes`, and
diffed both against `LocalDirectoryStore` reading the identical fixtures off disk. Checked the
async twins (`alist_objects`/`aget_bytes`) agree with their sync counterparts. Deliberately
tried a path that escapes the landing root (`raw/../../../etc/passwd`), and a half-configured
credential pair (`account_name` with no `account_key`).

**Result:** All four fixture files listed by `AzureBlobObjectStore` matched
`LocalDirectoryStore`'s listing exactly, and every object's bytes were identical between the
two backends. The async variants returned the same results as their sync twins. The escaping
path was refused with `ValueError: 'raw/../../../etc/passwd' resolves outside the landing
root egpt-landing/` -- the same shape `LocalDirectoryStore._resolve` raises. The
half-configured credential pair was refused at construction with `RuntimeError:
EGPT_RETAIL_STORAGE_ACCOUNT_KEY is not set while its partner is...`, before any client or
network call was made.

Full script output:

```
AZ-A -- Azure Blob storage backend, against Azurite
==============================================================================
-- 1. Upload C1's fixtures into a fresh Azurite container --
   uploaded 4 fixture files
-- 2. List and read through AzureBlobObjectStore --
   Azure Blob listed 4 objects
   local directory listed 4 objects
   all 4 objects byte-for-byte identical to LocalDirectoryStore
-- 3. Async variants agree with their sync twins --
   alist_objects/aget_bytes agree with list_objects/get_bytes
-- 4. A path escaping the landing root is refused --
   refused as expected: 'raw/../../../etc/passwd' resolves outside the landing root egpt-landing/
-- 5. A half-configured credential pair is refused at construction --
   refused as expected: EGPT_RETAIL_STORAGE_ACCOUNT_KEY is not set while its partner is. Set both to use an explicit shared key, or neither to use the ambient credential chain (managed identity on Container Apps). One of the two is a typo, not a configuration.

==============================================================================
PASS -- Azure Blob listing and get_bytes match LocalDirectoryStore's output
byte-for-byte on the same fixtures. The escaping path was refused with the same
ValueError shape LocalDirectoryStore uses. The half-configured credential pair
was refused at construction.
```

Also verified: `product/tests/test_r4_storage.py` extended with seven unit tests for
`AzureBlobObjectStore`'s constructor validation (half-configured shared-key pair, both/neither
accepted, connection-string-only construction, no-account-addressable refusal, path-escape
refusal) and `build_object_store`'s new explicit `elif` dispatch (unrecognized provider now
raises `ValueError` instead of silently falling through to S3), and 11 more from review round 1
for the shared `validate_object_path` allowlist -- nine refused path shapes parametrized across
all three backends (absolute, backslash-traversal, drive letter, `..`, `.`, `//`, NUL,
whitespace), an accepted-shapes test pinned against a real committed landing fixture path, and
a message-level test for the absolute-path branch. 32/32 passed, no regression
in the existing local/S3 tests. `MYPYPATH=libs/agno .venv/bin/mypy product/egpt_retail
--config-file product/pyproject.toml` found zero errors (including the new
`azure_blob.py`; no mypy override was needed for `azure.storage.blob` or
`azure.identity` -- both ship their own type stubs, unlike `boto3`). `ruff format` and
`ruff check` clean on every file touched.

---

### AZ_B_ai_search_substrate.py

**Package:** AZ-B (`docs/plan/azure-migration-plan.md`, gap #3 in
`/Users/rasik/.claude/plans/shimmying-twirling-piglet.md`)

**Status:** PASS (against a mocked SDK -- see the explicit boundary below; **not** run against a
real Azure AI Search resource)

#### Environment constraint, stated plainly

This session has no Azure CLI (`az` is not installed), no Azure subscription, and no Azure AI
Search service reachable over the network. Every claim below that could only be confirmed by a
real Azure AI Search resource is marked **NOT PROVEN** rather than asserted or implied. Nothing in
this log or in the code comments presents a mocked-test pass as if it were a live-service proof.

#### What was actually run and what it showed

1. **Unit tests**, `libs/agno/tests/unit/memory/test_azure_ai_search_odata.py` (19 tests) and
   `libs/agno/tests/unit/memory/test_azure_ai_search_memory_substrate.py` (72 tests) -- 91 tests,
   all passing, against the real installed `azure-search-documents==11.6.0` SDK classes with only
   the HTTP-transport-touching methods mocked (`SearchIndexClient.create_or_update_index`,
   `SearchClient.merge_or_upload_documents`, `SearchClient.search`, and their `aio` twins). Full
   output:

   ```
   $ .venv/bin/python -m pytest libs/agno/tests/unit/memory/test_azure_ai_search_odata.py \
       libs/agno/tests/unit/memory/test_azure_ai_search_memory_substrate.py -q
   ...
   91 passed in 0.67s
   ```

   The suite grew from 53 to 91 in round-1 review response; every added test was
   mutation-tested (the defect reintroduced, the test confirmed red, the defect reverted) --
   13 mutations run, 13 caught. See "Round-1 review dispositions" at the end of this entry.

   Covered, with the request/response shape asserted directly on what the mocked SDK methods were
   called with (the actual thing a real Azure AI Search REST call would carry):
   - Construction refusals: missing endpoint, missing credential, an illegal index name, HNSW
     parameters outside Azure's own documented `m`/`ef_construction`/`ef_search` bounds, an
     embedder reporting more dimensions than Azure AI Search's 3072 ceiling, and a
     `create_or_update_index` rejection carrying HTTP 400/409 (simulated `HttpResponseError`)
     translated to `MemoryProviderConfigError` naming `dim` -- and, separately, a 401/403/503 or
     status-less failure on that same call asserted **not** to be reported as schema drift, on both
     the sync and the async index client.
   - `upsert`/`aupsert`: the exact document dict sent to `merge_or_upload_documents`, including the
     derived `doc_key`, every scope slot, the JSON-serialized `topics`/`provenance`/`metadata`
     fields, and the embedding vector's width.
   - `search`/`asearch`: a vector-only query (`search_text=None`, `vector_queries` set), the score
     passed through unchanged (Azure's cosine `@search.score` is already the `[0.0, 1.0]`
     similarity the port requires -- see the decisions doc), and the resulting `MemoryRecord`.
   - **`search_candidates`/`asearch_candidates`: proven to issue two independent single-leg
     requests** (one `search_text=None` with `vector_queries` set, one `search_text=<query>` with
     `vector_queries=None`), both carrying the **identical** scope filter, and **not** Azure's
     native hybrid/RRF call shape -- this is Decision 2 from
     `AZ_B_dimension_and_scoring_decisions.md`, checked directly on the two calls the mocked client
     received, not asserted in prose.
   - **The security-critical property: tenant isolation, proven on the literal request.** For two
     different scopes (`tenant_id="acme"` and `tenant_id="globex"`), the `filter=` string the
     mocked `SearchClient.search` receives for one tenant is asserted to **never contain** the
     other tenant's `tenant_id` or `user_id` value (`test_search_filter_for_one_tenant_never_names_another`).
     A parallel test proves the same property for the underlying `odata.scope_filter` builder with
     "unmistakable" per-slot values (`test_every_scope_slot_reaches_the_filter_with_unmistakable_values`),
     and a dedicated injection test proves a scope value containing a literal `'` cannot splice a
     structurally new clause into the filter (`test_a_quote_in_a_scope_value_cannot_splice_a_second_clause`).
   - `scan`/`ascan`: ordering (`order_by=["created_at asc", "id asc"]`), the composite `after`
     cursor narrowing (`created_at gt ... or (created_at eq ... and id gt ...)`), and -- matching
     `PgVectorMemorySubstrate.scan` -- **no** current-belief filter, so a superseded row is still
     enumerable.
   - `get_record`/`aget_record`: filters by scope + `id`, `top=1`, returns `None` on no hits.
   - `_document_key`: deterministic for the same `(tenant_id, id)`, **injective** -- every pair
     drawn from a component set built to break the join (each component either contains the `|`
     separator, or mimics the length prefix's own `digits + ":"` shape, or is an empty/boundary
     case) is enumerated and asserted to produce a distinct key, which is the property that makes
     one shared index safe despite per-tenant `id` reuse -- and always inside Azure's allowed key
     charset even for an `id` containing `/`, spaces, `?`, `=`, `'` and `|`.
   - `_lexical_similarity`: monotonic and bounded in `[0.0, 1.0)`.
   - Async/sync parity: every method pair (`upsert`/`aupsert`, `search`/`asearch`,
     `search_candidates`/`asearch_candidates`, `scan` (async not separately re-tested beyond the
     shared filter builder), `get_record`/`aget_record`) is exercised on both halves and asserted
     to send the same filter/request shape.

2. **The existing pgvector memory-substrate suite, re-run unchanged** --
   `test_pgvector_memory_substrate.py` (100 tests) and `test_pgvector_memory_statements.py`
   (139 tests), 239 tests, all still passing after this change, confirming the shared
   `Protocol`/registry machinery (`egpt.memory.substrates`, `egpt.registry.discovery`) was not
   disturbed.

3. **The registry seam**, `discovery.construct("vectorstore", "azure_ai_search", **kwargs)`,
   exercised by `AZ_B_ai_search_substrate.py` and run directly. Real output:

   ```
   === AZ-B: Azure AI Search memory substrate, via the registry seam ===

   1. Resolved via discovery.construct('vectorstore', 'azure_ai_search', ...): AzureAISearchMemorySubstrate
      Index schema requested: 'egpt-memory-demo', dim=8

   2. upsert() sent this document to merge_or_upload_documents:
        doc_key: 'NDphY21lfGUx'
        id: 'e1'
        tenant_id: 'acme'
        user_id: 'ada'
        tier: 'episodic'
        content: 'the invoice total was 4200 dollars'
        embedding_version: '__main__.DemoEmbedder|d8|cos|v1'
        embedding: 8 floats

   3. search() returned:
        id='e1' score=0.87 content='the invoice total was 4200 dollars'
      Request sent: search_text=None, filter="tenant_id eq 'acme' and user_id eq 'ada' and (tier eq 'episodic' or tier eq 'semantic' or tier eq 'procedural') and expired_at eq null"

   4. Tenant isolation -- the literal filter each tenant's search would send:
        acme:   "tenant_id eq 'acme' and user_id eq 'ada' and (tier eq 'episodic' or tier eq 'semantic' or tier eq 'procedural') and expired_at eq null"
        globex: "tenant_id eq 'globex' and user_id eq 'wile' and (tier eq 'episodic' or tier eq 'semantic' or tier eq 'procedural') and expired_at eq null"
        -> confirmed: neither tenant's filter names the other tenant's scope.

   5. search_candidates() issues two unfused legs, not a native RRF call:
        leg 1 (vector): search_text=None, vector_queries set=True
        leg 2 (lexical): search_text='invoice total', vector_queries set=False

   === All assertions passed against the mocked SDK. See TEST_LOG.md for the ===
   === explicit boundary between what this proves and what needs a live Azure ===
   === AI Search resource. ===
   ```

4. **Static checks**, all clean:
   - `MYPYPATH=libs/agno mypy libs/agno/egpt/integrations/memory/vector/azure_ai_search
     libs/agno/egpt/registry/_declarations/vectorstores.py --config-file libs/agno/pyproject.toml`
     -> `Success: no issues found in 4 source files`.
   - `ruff format` + `ruff check` on every file touched -> clean.
   - `pytest libs/agno/tests/unit/test_import_linter_contracts.py` and a direct `lint-imports` run
     -> both contracts kept (`core, orchestration, services, and api must not import integrations`;
     `integrations must not import orchestration, services, or api`) -- this new integration module
     does not reach back into the services layer.

#### What is proven vs. what needs a real Azure AI Search resource

**Proven, against the real `azure-search-documents==11.6.0` SDK with only HTTP calls mocked:**
- The registry seam resolves `"azure_ai_search"` correctly (item 3 above).
- The `VectorSubstrate`/`HybridSearchSubstrate` Protocols are satisfied structurally
  (`isinstance` checks pass, every named method is present and callable) and every method's
  request-building logic runs without error against realistic inputs.
- The **document shape** `upsert` sends, and the **`$filter` shape** every read sends, are exactly
  what this backend's code builds -- this is real evidence about what request a live service would
  receive, because only the transport call itself (the actual TCP/TLS round trip) is faked, not the
  SDK's own request-construction code.
- **Tenant isolation at the request-construction layer**: a call scoped to one tenant cannot
  produce a filter naming another tenant, proven directly on the string sent, for both the
  low-level `odata` builders and the substrate's own methods.
- Construction-time refusals (bad HNSW params, oversized dimension, bad index name, missing
  endpoint/credential) fire before any request is attempted.
- Score normalization: the vector leg's Azure `@search.score` formula for cosine
  (`1 / (1 + cosine_distance)`, confirmed via Microsoft Learn's "Vector Relevance and Ranking" page,
  fetched in this session) is already in the port's required `[0.0, 1.0]` range, so it is passed
  through unmodified; the lexical leg's unbounded BM25 score is squashed through the same
  saturating transform `pgvector`'s lexical leg uses (`r / (r + 0.1)`), proven monotonic.

**NOT proven -- needs a real Azure AI Search resource:**
- **Actual index creation success.** `create_or_update_index` is mocked; whether the exact schema
  this module builds (field types, the `HnswAlgorithmConfiguration`/`VectorSearchProfile` pairing,
  the `doc_key` as the key field) is accepted by a real service, and whether `_reject_schema_drift`
  correctly recognizes every real-world rejection shape Azure actually returns for an incompatible
  change, is unverified.
- **Real dimension-limit enforcement.** The 3072 ceiling this module checks at construction is
  taken from Microsoft Learn documentation, not from a real service's actual rejection behavior at
  the boundary.
- **Real RRF/BM25/cosine scoring output.** The score-normalization formulas above are taken from
  Microsoft Learn documentation, not measured against real query results. Whether Azure's actual
  BM25 score distribution makes the `_RANK_SATURATION = 0.1` constant (reused from pgvector's own,
  itself calibrated against `ts_rank_cd`, not BM25) a reasonable choice for *this* engine is
  unmeasured -- flagged explicitly in `AZ_B_dimension_and_scoring_decisions.md`'s "Decision 2" as a
  parity choice, not a calibration.
- **Real retrieval quality/recall.** No benchmark of recall@k, latency, or ranking quality against
  a real index exists. The migration plan's own "Verify" section for AZ-B calls for "a benchmark at
  the chosen embedder dimension" and a pgvector-vs-Azure diff on the same fixture data -- **neither
  is possible in this environment** and neither was attempted or fabricated.
- **The shared-index tenant-isolation *recall* property**, as opposed to the *request-construction*
  property proven above. The filter is proven to be scoped correctly; whether Azure AI Search's
  filtered HNSW search *degrades* with tenant count the way pgvector's shared-table shape measurably
  did (SM-R3: recall 0.80 -> 0.56 -> 0.22 at 1 -> 10 -> 50 tenants) is explicitly unmeasured and named as
  an open question in both the decisions doc and the module's own docstring.
- **Cross-leg consistency under concurrent writes.** `search_candidates` issues two separate HTTP
  requests with no shared snapshot (unlike pgvector's pinned `REPEATABLE READ` transaction across
  both legs); whether a document committed between the two calls produces a visible inconsistency
  in practice is unverified -- the code comment names this as a real, not cosmetic, behavioural gap
  from the pgvector backend.
- **Which real Azure rejection shapes mean schema drift.** `_reject_schema_drift` now fires only
  for HTTP 400/409 from `create_or_update_index` (an auth failure, a throttle or an outage on that
  call is classified by retryability like any other request instead of misreporting as drift), but
  a 400 still cannot distinguish "the existing index disagrees about `dim`" from "the definition
  this module built was refused" without the service's own error code, which cannot be pinned to a
  verified string with no live resource to observe. The error message names both possibilities
  rather than asserting one.
- **Real credential/auth paths.** Only `AzureKeyCredential` (API-key auth) was exercised, via a
  fake key string. A `TokenCredential` (Entra ID / managed identity) path is accepted by the type
  signature and passed through, but never actually constructed or exercised -- this module does not
  depend on `azure-identity`, so building one is the caller's responsibility, untested here.

#### Round-1 review dispositions

Recorded here so the next ticket in this seam does not re-derive them. Full detail, including the
mutation results, is in PR #216's summary comment.

| Finding | Severity | Disposition |
| --- | --- | --- |
| `_document_key` `\|`-join collision | Critical | Fixed: length-prefixed, injective encoding + an enumerated injectivity test |
| `merge_or_upload_documents`' per-document `IndexingResult` discarded | Major | Fixed on both write twins: a partial batch failure now raises `MemoryOperationError` naming the record ids |
| `_DETERMINISTIC = (HttpResponseError,)` called every HTTP status permanent | Major | Fixed: retryability decided on the status code (`_is_retryable`); 408/429/5xx and an unknown status propagate |
| `_reject_schema_drift` inferred drift from any HTTP failure | Major | Fixed: only 400/409 reach it; remaining ambiguity documented under NOT PROVEN above |
| `azure-ai-search` extra missing from the aggregate extras | Major | Fixed: added to `vectordbs` (so `tests` and CI's unit job install it) and to `demo`; `azure-search-documents` added to `unit-drivers-absent`'s uninstall list so the `importorskip` gates stay honest |
| `get_record`/`scan`-cursor id escaping untested with hostile input | Minor | Fixed: hostile-quote tests on all four read twins |
| Injection test never put `'` in `tenant_id` | Minor | Fixed: `test_a_quote_in_tenant_id_cannot_splice_a_second_clause` |
| `ScanCursor`'s `after_created_at` unguarded | Minor | Fixed: type checked in `_scan_filter`, matching `ValidWindow`'s precedent |
| HNSW refusal test did not mock the client | Minor | Fixed: mocked, so a validator regression fails on the assertion rather than on the network |
| `search_candidates` bypasses the embedding pipeline's dimension check and cost metering | Minor (inherited) | **Not fixed.** Byte-for-byte identical in `PgVectorMemorySubstrate.search_candidates`; changing it here alone would make the two backends disagree about what `search_candidates` costs and validates. Belongs to a follow-up shared with pgvector, not to AZ-B. |
| The two async hybrid legs run sequentially | Minor (perf) | **Not fixed.** Nothing in the code or docs claims otherwise, and the benefit cannot be measured without a live service -- the same reason recall and latency are NOT PROVEN above. Revisit with the AZ-B benchmark the migration plan's "Verify" section calls for. |
| `_lexical_similarity` docstring claimed a "loud `ValueError`" | Nit | Fixed: a negative rank is now actually refused, and the docstring says what the division does and does not catch |
| Module docstring called base64 "hashing" | Nit | Fixed: it is an encoding, and the docstring now explains why injectivity, not hashing, is the property that matters |

### AZ_C_knowledge_prose.py

**Package:** AZ-C (`docs/plan/azure-migration-plan.md`, K2 knowledge-prose migration)

**Status:** PASS on the pgvector leg (real compose Postgres, real local embedder); the Azure AI
Search leg was **NOT RUN** -- see the boundary below. Nothing is claimed for it.

#### Environment constraint, stated plainly

Same boundary AZ-B recorded: this session has no Azure CLI, no Azure subscription, and no
Azure AI Search service reachable over the network. `EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT` was
unset, so section 2 of the script printed its skip notice and exited without touching the
`azure_ai_search` backend. The Azure-side claims of the plan's Evidence entry -- that the
search returns the correct section on Azure AI Search and that its ranking is consistent with
the RRF semantic AZ-B documented -- are **NOT PROVEN** here. The script is written to prove them
the moment an endpoint and key are set (it diffs the two result sets and asserts the top section
agrees), and that run must be recorded as a new entry when it happens.

The `AzureAISearch` `VectorDb` itself is AZ-B's framework deliverable, written in parallel with
this package; the product side imports it lazily inside `build_knowledge_vector_db` against the
agreed constructor contract (`index_name`, `endpoint`, `api_key`, `embedder`, `search_type`).
On this checkout the module does not exist yet, so the Azure branch is covered by unit tests
with a recorded fake standing in for the class, not by any run.

#### What was actually run and what it showed

Environment: `.venvs/demo` does not exist on this machine, so the script ran under the dev
`.venv` with `sentence-transformers` installed into it for the occasion (`uv pip install`),
`PYTHONPATH=product:libs/agno` pointing at this branch's checkout, and compose Postgres
(`egpt-retail-postgres-1`, host port 5532) already up. `EGPT_RETAIL_PROVIDER_MODE=local`,
`BAAI/bge-base-en-v1.5` at 768 -- the same embedder K02's own entry ran with.

```
$ PYTHONPATH=product:libs/agno .venv/bin/python cookbook/95_azure_migration/AZ_C_knowledge_prose.py

0. Chunk boundaries fall on headings (no backend involved)
  08-inventory-management-and-demand-forecasting.md: 55 chunks under the chosen setting, 55 start at a heading, 13 heading-only shells
  [ok] every chunk begins at a heading under the chosen setting
  [ok] expected heading '### 8.1 Definition and Mechanics' exists and the query is not its title
  [ok] expected heading '### 8.3 Bullwhip Mitigation Strategies' exists and the query is not its title
  embedder: BAAI/bge-base-en-v1.5 @ 768d (mode local)

1. pgvector (compose Postgres) -- the baseline
  vector db: PgVector
  1 shipped document(s) ingested
  'four primary causes of the bullwhip effect identified by Hau Lee'
      1. '### 8.1 Definition and Mechanics'  score=0.4817
      2. '## 16. Further Reading'  score=0.3788
      3. '### 8.2 Bullwhip in a Retail-Specific Context'  score=0.3533
  [ok] pgvector: top result is '### 8.1 Definition and Mechanics'
  'sharing daily store-level POS data with suppliers through Retail Link'
      1. '### 8.3 Bullwhip Mitigation Strategies'  score=0.3714
      2. '### Case Study 1: Walmart — Integrated Demand Sensing and In'  score=0.3377
      3. '### 9.4 Demand Sensing vs. Demand Planning'  score=0.3327
  [ok] pgvector: top result is '### 8.3 Bullwhip Mitigation Strategies'
  stored in egpt_retail_prose_az_c: 42 chunks, 42 begin at a heading
  [ok] pgvector: every stored chunk begins at a heading
  [ok] pgvector: stored chunk count is the heading split minus the dropped shells

2. azure_ai_search -- the same scenario, diffed against the baseline
  SKIPPED: EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT is not set. ...

pgvector leg holds: chunk boundaries fall on headings, every stored chunk begins at
its heading, and a mid-section phrase returns the whole section it came from.
Azure AI Search leg: NOT RUN (no service configured). Nothing is claimed for it.
exit 0
```

**Description:** The plan's Verify scenario, run through the product's own wiring rather than a
private copy of it: the document with known headings (K02's sample,
`08-inventory-management-and-demand-forecasting.md`) is ingested through
`egpt_retail.knowledge.vector.build_knowledge_vector_db` -- the same factory `load-knowledge`
and `suggest` now call -- with `EGPT_RETAIL_KNOWLEDGE_BACKEND=pgvector`, into its own table.
Two phrases from the *middle* of a section (neither is a heading or a first line) are searched
with `SearchType.hybrid`, top 3, and the result set recorded. Section 0 measures the chunking
with no backend at all, since the boundary property is chunking logic and holds regardless of
where the vectors go.

**Result:** 55 heading-split chunks, 55 starting at a heading, 13 heading-only shells dropped
before ingest, 42 stored -- and the stored count equals the split minus the shells, so nothing
was re-split by size on the way in. Both mid-section phrases return the whole section they came
from as the top hit (`### 8.1 Definition and Mechanics`, `### 8.3 Bullwhip Mitigation
Strategies`), every returned chunk begins at its heading, and the pgvector `similarity_score`
column carries pgvector's weighted vector/text blend (0.48, 0.37 at the top). That column is the
baseline the Azure leg will be diffed against; per AZ-B decision 2 the Azure score is an
RRF-derived value of a different magnitude, so the script asserts agreement on the top section
and prints, rather than asserts, the lower ranks and scores.

**What changed in the product for this package:**

- `product/egpt_retail/app/settings.py` -- `retail_knowledge_backend` (`pgvector` |
  `azure_ai_search`, default `azure_ai_search`, env `EGPT_RETAIL_KNOWLEDGE_BACKEND`),
  `retail_knowledge_search_index` (default `egpt-retail-knowledge`),
  `retail_knowledge_search_endpoint`, `retail_knowledge_search_api_key`. The default is the
  production truth, same posture as `retail_provider_mode`; with the default backend and no
  endpoint, construction refuses naming `EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT` and the
  `pgvector` escape hatch, at the point of use, like `build_warehouse_engine`.
- `product/egpt_retail/knowledge/vector.py` -- `build_knowledge_vector_db(settings, embedder)`,
  pure construction (no async twin, per the standing exemption). The pgvector branch passes
  exactly what the two entrypoints passed before (`table_name`, `db_url`,
  `SearchType.hybrid`, `embedder`), now importing from `egpt.integrations.vectordb.pgvector`
  rather than the deprecated `egpt.vectordb.pgvector` shim.
- `entrypoints/load_knowledge.py` and `entrypoints/suggest.py` -- the two hardcoded `PgVector`
  constructions replaced by the factory; `suggest`'s shim import removed. The load report line
  names the table on pgvector and `azure_ai_search:<index>` otherwise.
- `cookbook/94_retail/K02_domain_prose.py`, `K10_unified_retrieval.py`, `K12_kpi_suggestion.py`,
  `K13_use_case_suggestion.py` -- vector db built through the factory; each sets
  `EGPT_RETAIL_KNOWLEDGE_BACKEND=pgvector` at the top unless an Azure AI Search endpoint is
  configured, since they run by hand against compose Postgres. Shim imports fixed.
- `product/tests/test_az_c_knowledge_vector.py` -- 11 tests: default backend, env binding,
  unknown backend refused, pgvector kwargs byte-for-byte, Azure kwargs, key pass-through as
  `None`, no-endpoint refusal naming the variable with nothing imported or built, cross-backend
  import isolation, `load-knowledge` calling the factory (behavioural), the report line, and a
  source-level check that neither entrypoint names `PgVector(` or the shim.

Product suite: `1057 passed, 51 skipped`. Product mypy (`--python-version 3.12`,
`MYPYPATH=../libs/agno`): clean, with one `type: ignore[import-not-found]` on the lazy
`AzureAISearch` import that is inert once AZ-B's module is on the checkout.

**Not done here, and why:** the Azure leg (above); `.venvs/demo` provisioning
(`demo_setup.sh` is not this package's to change; note K02's own entry already flagged
`unstructured` as missing from it). K10/K12/K13 were re-wired but not re-run: they need Neo4j,
the loaded corpus and (K12/K13) Azure OpenAI; their edit is the same four-line substitution K02
carries, and K02 was re-run in full to prove that substitution.

---

### AZ-B memory backend flip

**Package:** AZ-B (the product-side piece PR #216 deliberately left out: `product/egpt_retail/memory/config.py`
selecting the substrate by config, plus the deployment wiring)
**Script:** none -- `product/tests/test_az_b_memory_backend.py` (12 unit tests), `helm lint` /
`helm template`, and the full `product/tests` suite. There is still no Azure AI Search service or
subscription in this environment (the same boundary the entry above states), so no script was
written that would claim to run against one.
**Status:** PASS for everything listed under "proven"; **NOT RUN** against a real index.

**Description:** `RetailSettings` gains `retail_memory_backend` (`EGPT_RETAIL_MEMORY_BACKEND`,
`pgvector | azure_ai_search`, default `azure_ai_search` -- the production truth, same posture as
`retail_provider_mode`), `retail_memory_search_endpoint` / `_api_key` / `_index`
(`EGPT_RETAIL_MEMORY_SEARCH_*`, index default `egpt-retail-memory`). `build_memory_config` builds the
`vector` dict per backend: the pgvector dict is byte-identical to before; the Azure dict is
`{backend, endpoint, api_key, index_name, ef_search}`, which `resolve_substrate` forwards as kwargs to
`AzureAISearchMemorySubstrate.__init__` (plus `mode`, because the constructor names it). The endpoint
and key are passed through explicitly; the substrate's own `EGPT_MEMORY_AZURE_SEARCH_*` fallbacks are
not relied on. `azure_ai_search` with no endpoint is refused at config-build time by
`MemoryProviderConfigError`, naming `EGPT_RETAIL_MEMORY_SEARCH_ENDPOINT` and the `pgvector` way out.
Compose (`api`, `consolidator`) and the kind overlay (`values.local.yaml`) set `pgvector` explicitly;
`values.yaml` gains `memory.backend`, `search.endpoint`, `search.memoryIndex`,
`secrets.values.searchApiKey` (Key Vault object `search-api-key`).

**Result:**
```
product/tests/test_az_b_memory_backend.py: 12 passed
product/tests (whole suite, -p no:randomly): 1057 passed, 51 skipped (12 of them this file)
ruff format + ruff check on the touched files: clean
cd product && MYPYPATH=../libs/agno mypy egpt_retail --python-version 3.12: Success: no issues found in 136 source files
helm lint deploy/helm/egpt: 1 chart(s) linted, 0 chart(s) failed
helm template ... -f values.local.yaml --set auth.enabled=false:
  EGPT_RETAIL_MEMORY_BACKEND: "pgvector", EGPT_RETAIL_MEMORY_SEARCH_ENDPOINT: "",
  EGPT_RETAIL_MEMORY_SEARCH_INDEX: "egpt-retail-memory"; no EGPT_RETAIL_MEMORY_SEARCH_API_KEY line
helm template ... --set secrets.provider=keyVault:
  EGPT_RETAIL_MEMORY_BACKEND: "azure_ai_search"; SecretProviderClass maps search-api-key ->
  EGPT_RETAIL_MEMORY_SEARCH_API_KEY and lists the search-api-key object
helm template ... --set secrets.values.searchApiKey=abc --set search.endpoint=https://x.search.windows.net:
  EGPT_RETAIL_MEMORY_SEARCH_API_KEY: "abc" rendered in the Secret; endpoint rendered in the ConfigMap
```

**Proven (no network, no Postgres):**
- The default backend is `azure_ai_search`; every new env name binds; an unknown backend is refused
  at settings time.
- The `pgvector` dict is unchanged by the flip (asserted as a literal).
- The Azure dict's keys (minus `backend`) are a subset of `AzureAISearchMemorySubstrate.__init__`'s
  parameters, read with `inspect.signature` against the merged substrate -- a renamed constructor
  parameter fails this test rather than as a `TypeError` inside `discovery.construct`.
- A missing or blank endpoint refuses naming `EGPT_RETAIL_MEMORY_SEARCH_ENDPOINT` (and never the
  substrate's `EGPT_MEMORY_AZURE_SEARCH_ENDPOINT`), through `build_memory_vector_settings`,
  `build_memory_resolver`, `build_memory_provider`, and the memory lifespan (`memory_lifespan_for`)
  -- the last as the one fault a boot raises, from startup, not import. `build_agent_os(RetailSettings())`
  with the Azure default and no endpoint still constructs (import stays pure). An unset customer
  slug still short-circuits to `None` before the backend is consulted, so G1's ordering is unchanged.
- A `MemoryProvider` builds through the real seam -- `build_memory_provider` ->
  `build_memory_provider_for(build_memory_resolver(settings), tenant_id="acme")` -> registry ->
  `AzureAISearchMemorySubstrate` -- with a fake endpoint, and the constructed substrate carries the
  configured endpoint, index name, `ef_search=256`, `mode=FRONTIER` and `dim=1024`.
- The consolidator needs nothing more: `entrypoints/consolidator.py` calls
  `build_memory_provider_for(build_memory_resolver(settings), tenant_id=slug)`, the same path.

**Recorded, not forced -- construction is NOT lazy:** `AzureAISearchMemorySubstrate.__init__` calls
`init_schema()` (`auto_init_schema=True`, the same self-provisioning posture as pgvector's
`CREATE EXTENSION ... / CREATE TABLE` on construction), which is exactly one
`SearchIndexClient.create_or_update_index` HTTPS call. The construction test mocks that one method
and asserts it was called once with an index named `egpt-retail-memory-test`, and that
`SearchClient.search` was never called. So: constructing the provider needs the service reachable
and the credential allowed to create indexes; a no-work consolidator tick still needs no embedder
key. The task's "show construction is lazy" therefore does not hold and is not claimed.

**Not proven -- needs a real Azure AI Search resource (unchanged from the entry above):** index
creation against a live service, the first real boot of `api`/`consolidator` with
`EGPT_RETAIL_MEMORY_BACKEND=azure_ai_search`, the cross-process recall scenario the plan's AZ-B
"Verify" step asks for, any recall or latency number, and the `TokenCredential` path (only
`api_key` is wired from settings; managed identity would need the product to build a credential
and pass it, which no setting does today).

**Known consequence outside this change's write scope:** `cookbook/94_retail/K1_memory_wiring.py`
(and any other cookbook that builds `RetailSettings()` for memory against the local pgvector) now
needs `EGPT_RETAIL_MEMORY_BACKEND=pgvector` in its environment or it refuses at
`build_memory_provider`, naming the endpoint variable. That file is owned by another lane and was
not edited here; the fix is one `os.environ.setdefault("EGPT_RETAIL_MEMORY_BACKEND", "pgvector")`
next to its existing `EGPT_RETAIL_MEMORY_TABLE` default.

---

### Integration of AZ-C and the AZ-B flip (one branch, `feat/az-c-knowledge-prose`)

**Package:** AZ-C + AZ-B (the two entries above, merged with the framework `AzureAISearch`
`VectorDb` into one branch and re-verified together)
**Status:** PASS for everything the entries above claim, on the merged tree; the Azure AI Search
legs remain **NOT RUN** (no service in this environment).

**What the merge added, and why:**
- `AZ_C_knowledge_prose.py` was not idempotent: with the store left from the previous run, a
  second run stored 84 chunks for 42 and failed its own count check. It now drops its own table
  before ingesting; run twice on the merged tree, 42 stored both times, exit 0.
- The knowledge backend had settings but no deployment wiring: `values.yaml` gains
  `knowledge.backend` (default `azure_ai_search`) and `search.knowledgeIndex`; the ConfigMap maps
  `EGPT_RETAIL_KNOWLEDGE_BACKEND` / `_SEARCH_ENDPOINT` / `_SEARCH_INDEX`; the Secret and the Key
  Vault object map the same `search-api-key` to `EGPT_RETAIL_KNOWLEDGE_SEARCH_API_KEY` (same
  service and key as memory, separate index); `values.local.yaml` and both compose services set
  `pgvector`. `helm lint` clean; rendered with the kind overlay, with `secrets.provider=keyVault`,
  and with a set api key -- the knowledge keys render, and the api-key line is absent when empty.
- `K1_memory_wiring.py`, `K05_semantic_graph.py`, `K10_unified_retrieval.py` set
  `EGPT_RETAIL_MEMORY_BACKEND=pgvector` explicitly (the AZ-B entry's "known consequence");
  `K1_memory_wiring.py` re-run against compose Postgres with the local embedder: PASS.
- `test_load_knowledge_builds_its_store_through_the_factory` is gated on the optional
  `unstructured` package (it walks the real markdown reader), so a partial install skips it
  rather than failing it -- the `unit-drivers-absent` rule.

**Result on the merged tree:** `product/tests` 1069 passed, 51 skipped (3.12); framework
`test_azure_ai_search.py` 80 passed; product mypy (`--python-version 3.12`) clean; framework
mypy on the new package clean. On the Python 3.10 venv the product suite passes except
`test_r4_storage.py`'s sixteen Azure Blob tests, which fail only because that venv lacks the
`azure` SDK -- AZ-A's tests, already on `main`, not this branch's.
