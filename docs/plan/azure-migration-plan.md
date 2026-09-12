# Azure Migration Plan

Supersedes `deploy/AKS_CUTOVER.md`'s AKS commitment and `docs/plan/build-plan.md`'s "the
architecture is sound and it is the standing decision, so the plan follows it" (§02
Foundation). The new standing decision: Azure Functions Flex Consumption for the API,
Azure Container Apps for Neo4j and the long-lived/scheduled processes, Azure AI Search
replacing pgvector for vector retrieval. The gap analysis and sequencing rationale are recorded
inline in this document -- the "Where the code lives" and "Sequencing" tables, and each package's
Build / Verify / Evidence entries -- rather than in an external artifact.

Five packages below: one phase-zero (docs), four tracks. Each is independently ownable and
independently verifiable, so they run in parallel. Sync rows mark the one real dependency.

## Where the code lives

Same rule as `delivery-plan.md`: framework fixes belong in `libs/agno/egpt/` because every
future `egpt`-based deployment inherits them; everything retail-specific belongs in
`product/egpt_retail/`.

| Package | Home | Why |
|---|---|---|
| **AZ-D** auth (`scopes_claim`, JWKS URL) | `libs/agno/egpt/` | Any Entra-backed deployment needs this, not just retail. A retail-only patch would be the wrong asset. |
| **AZ-B** Azure AI Search vector/memory substrate | `libs/agno/egpt/` | The backend implementation is a general-purpose `VectorDb`/`VectorSubstrate`, same class as pgvector, qdrant, etc. |
| **AZ-A** Azure Blob storage backend | `product/egpt_retail/` | `ObjectStore` itself is product-owned already (`storage/s3.py`, `storage/local.py`); the new backend follows. |
| **AZ-C** K2 knowledge-prose call-site migration | `product/egpt_retail/` | Retail's own entrypoints and cookbooks; no framework surface. |
| **AZ-P0** doc updates | `deploy/`, `docs/plan/` | Documentation only. |

## Sequencing

| Phase | Package |
|---|---|
| P0 | AZ-P0 — doc updates, no code |
| P1 | AZ-A (storage) · AZ-B (K1 vector substrate) · AZ-D (auth) — run all three in parallel, no dependency between them |
| Sync | AZ-B must land, including its embedder-dimension and hybrid-scoring decisions, before AZ-C starts. AZ-C would otherwise re-derive both decisions independently and risk disagreeing with itself. |
| P2 | AZ-C (K2 migration), after AZ-B |

## How each deliverable is verified

Same convention as the rest of this repo, in a dedicated folder for this initiative rather than
mixed into `cookbook/94_retail/`'s retail-specific log.

- **Write a runnable script** under `cookbook/95_azure_migration/`, one file per package
  (`AZ_A_blob_roundtrip.py`, `AZ_B_ai_search_substrate.py`, `AZ_C_knowledge_prose.py`,
  `AZ_D_entra_scopes.py`). It must run against the real service (Azurite / a real or emulated
  Azure AI Search index / a real Entra tenant or a minted token shaped like one) — not mocks,
  same rule `delivery-plan.md` states for the retail packages.
- **Run the scenario** in the package below, by hand, and read the output.
- **Record the result** in `cookbook/95_azure_migration/TEST_LOG.md`: package identifier, script
  path, PASS or FAIL, what was tested, anything notable. A failing run is recorded too.
- `./scripts/format.sh` and `./scripts/validate.sh` before every PR. Every new public method gets
  both a sync and an async variant, per the standing repository rule.

**Example format -- a template, not a real run.** No AZ-A run exists;
`cookbook/95_azure_migration/` is created by the first package that lands.

```
### <script>.py                        e.g. AZ_A_blob_roundtrip.py

**Package:** <AZ-A | AZ-B | AZ-C | AZ-D>
**Status:** <PASS | FAIL>

**Description:** <what was run, against which real service, and which cases were exercised --
for AZ-A that would be: ran against Azurite, listed the fixture container, read back every
NDJSON file, deliberately tried a path escaping the container root>

**Result:** <what the output actually showed, including a failure if it failed>
```

## AZ-P0 — Doc updates

*P0 · no code*

- **Build** — Update `docs/plan/build-plan.md`'s "Decisions taken" table and §02 Foundation to
  record Functions/Container Apps/Azure AI Search as the standing decision, with a note on why
  (operational cost for a small team — the exact concern §02's own "What AKS costs two people"
  callout already raised). Update or supersede `deploy/AKS_CUTOVER.md` into an Azure-native
  cutover doc: the same resource-by-resource checklist shape, retargeted, plus the four
  R2-equivalent rehearsal scenarios adapted to Entra tokens instead of `devauth` and to Functions
  + Container Apps instead of `kubectl`.
- **Verify** — Read both updated docs end to end. Confirm no sentence still asserts AKS as current
  or pending. Confirm every resource in the user's target architecture table appears somewhere in
  the updated `AKS_CUTOVER.md`-equivalent, and every gap this plan names (auth, vector backend,
  storage backend, compute split) is named there too, not just in the plan file.
- **Evidence** — Grepping both docs for "AKS" finds only historical references explicitly marked
  as superseded, never a live instruction. The new cutover doc's resource table has no blank
  "Values key it fills" column for anything the other four tracks deliver.

## AZ-A — Azure Blob storage backend

*P1 · product*

- **Build** — `AzureBlobObjectStore` in `product/egpt_retail/storage/azure_blob.py`, implementing
  `ObjectStore`'s four methods (`list_objects`/`alist_objects`/`get_bytes`/`aget_bytes`) against
  `azure-storage-blob`, following `storage/s3.py`'s shape exactly: lazy client construction,
  credential validation at construction (account key, connection string, or ambient/managed
  identity — same "both or neither explicit, else ambient chain" pattern as S3's access-key
  pair), a `_to_key`/`_to_path` prefix-normalization pair, `.as_posix()` care for
  `parse_landing_path`'s forward-slash-anchored regex. Wire it into
  `product/egpt_retail/storage/factory.py`: replace the bare fallthrough `else` with explicit
  `elif provider == "s3"` / `elif provider == "azure_blob"` branches (fixes the latent bug where
  today any unrecognized provider value silently becomes S3), add `"azure_blob"` to the
  `StorageProvider` literal in `product/egpt_retail/app/settings.py`, add the matching
  `RetailSettings` fields (account URL, credential), export from `storage/__init__.py`. Add
  `azure-storage-blob` (and `azure-identity` if targeting managed identity) to
  `product/pyproject.toml`, mirroring the existing `boto3` dependency line; check whether a mypy
  `ignore_missing_imports` override is even needed (Azure's SDK ships its own types, unlike
  boto3).
- **Verify** — Run against Azurite (the local Azure Storage emulator — the direct analog of
  MinIO for the S3 backend). List a fixture container, read back every file, confirm output
  matches `LocalDirectoryStore` byte-for-byte on the same fixtures. Try a path that escapes the
  container root. Construct the store with a half-set credential pair (account name with no
  key). Pass an unrecognized `provider` value to the factory.
- **Evidence** — `list_objects`/`get_bytes` produce identical results to the local backend on
  identical fixtures. The escaping path is refused with the same `ValueError` shape
  `LocalDirectoryStore._resolve` already raises. The half-configured credential pair is refused
  at construction, not silently degraded to an ambient chain. The unrecognized provider value now
  raises instead of silently becoming S3. Extend
  `product/tests/test_r4_storage.py`'s existing pattern with the new backend's equivalent tests.

## AZ-B — Azure AI Search vector/memory substrate

*P1 · framework*

- **Build** — Two things behind one decision. First, settle the embedder-dimension question:
  keep the 1024-dim truncation `MODE_DEFAULTS` forces today
  (`libs/agno/egpt/memory/embedding.py:336-353`), or use Azure AI Search's native vector-field
  width now that pgvector's 2000-dim HNSW ceiling
  (`libs/agno/egpt/integrations/memory/vector/pgvector/sql.py:87-90`, `MAX_HNSW_DIM`) no longer
  applies — decide with a real benchmark, not in the abstract. Second, and depending on that: a
  new `AzureAISearchMemorySubstrate` under
  `libs/agno/egpt/integrations/memory/vector/azure_ai_search/`, implementing `VectorSubstrate`
  and `HybridSearchSubstrate` (`libs/agno/egpt/memory/substrates.py`) — `upsert`/`aupsert`,
  `search`/`asearch`, `search_candidates`/`asearch_candidates`, `scan`/`ascan`,
  `get_record`/`aget_record`, every method applying the full `Scope` predicate per the Protocol's
  contract. Reconcile Azure AI Search's native RRF hybrid scoring against what callers here
  expect from `pgvector`'s weighted `vector_score_weight * vector_score + text_rank_weight *
  text_rank` blend
  (`libs/agno/egpt/integrations/vectordb/pgvector/pgvector.py:1067-1222`) — pick one semantic and
  document it; this decision is what Track C inherits, so make it once, here. Register the new
  backend: a row in `libs/agno/egpt/registry/_declarations/vectorstores.py` alongside the
  existing `"pgvector"` one. Flip `product/egpt_retail/memory/config.py:46`'s
  `"backend": "pgvector"` to `"backend": "azure_ai_search"` (config-only from the product's side —
  this is the seam's whole point).
- **Verify** — Store a fact in one process, ask a fresh process cold, same as K1's own original
  verification. Then the actual migration check: run the same recall scenario against pgvector
  and against the new Azure AI Search backend on the same fixture data, and diff the two result
  sets for the queries `MEMORY_SCOPE.md`/K1's own test suite already exercises. Run a benchmark
  at the chosen embedder dimension and record retrieval quality/latency numbers.
- **Evidence** — The cross-process recall works identically to K1's existing pgvector-backed
  proof. The pgvector-vs-Azure-AI-Search diff either matches or every divergence is explained by
  the documented scoring-semantic choice, not by a bug. The benchmark numbers are the artifact
  that lets AZ-C skip re-deciding the dimension question.

## AZ-C — K2 knowledge-prose migration

*P2 · after AZ-B · product*

- **Build** — Replace the two hardcoded `PgVector` constructions —
  `product/egpt_retail/entrypoints/suggest.py:73-87` and
  `product/egpt_retail/entrypoints/load_knowledge.py:204-214` — with the Azure AI Search
  `VectorDb` backend AZ-B's registered pattern extends to (a plain `VectorDb`, not the memory
  Protocol pair, since K2 goes through `egpt.knowledge.Knowledge`, not `egpt.memory`). Fix the
  deprecated-shim import (`egpt.vectordb.pgvector` → `egpt.integrations.vectordb.pgvector`, or
  its Azure AI Search equivalent) in the same pass. Update the four mirroring cookbook files
  (`cookbook/94_retail/K02_domain_prose.py`, `K10_unified_retrieval.py`, `K12_kpi_suggestion.py`,
  `K13_use_case_suggestion.py`). Reuse AZ-B's dimension and hybrid-scoring decisions verbatim —
  do not re-derive them here.
- **Verify** — Ingest a document with known headings (same scenario K2's original delivery-plan
  entry specifies). Search for a phrase sitting in the middle of one section. Confirm the result
  against what the pgvector-backed version returns for the same query on the same corpus.
- **Evidence** — Chunk boundaries still fall on headings (unaffected by the backend swap — this
  is chunking logic, not vector-storage logic). The search returns the correct section, and its
  ranking/score is consistent with the hybrid-scoring semantic AZ-B documented, not a surprise.

## AZ-D — Entra auth: scopes claim and JWKS URL

*P1 · framework*

- **Build** — Two gaps, both in `libs/agno/egpt/os/`. First: add `scopes_claim: Optional[str]`
  to `AuthorizationConfig` (`libs/agno/egpt/os/config.py`), thread it through
  `build_jwt_middleware_kwargs` (`libs/agno/egpt/os/middleware/jwt.py:409-463`) to the
  `AuthMiddleware`/`JWTValidator` constructors that already accept it — closing the gap where the
  field exists two layers down but has no way to reach it from the product-facing config. Add a
  roles/scp expansion: Entra emits `roles` (a list) or `scp` (a space-delimited string) instead of
  a `scopes` list; add a configurable expansion step (a new field, e.g. `scopes_claim_format:
  Literal["list", "roles", "scp"]` or equivalent) in the same claim-extraction path
  (`JWTValidator.extract_claims`, `AuthMiddleware.dispatch`'s duplicate inline logic) so a
  space-delimited `scp` string splits into real scope tokens instead of becoming one bogus
  string. Second: give `AuthorizationConfig.jwks_file` a URL mode — either accept a URL directly
  and fetch+cache via `PyJWKClient` (which already knows how to do this), or add a distinct
  `jwks_url` field, with a refresh policy so a rotated Entra signing key doesn't require a manual
  file re-mount and pod restart.
- **Verify** — Unit tests: a `scp` string with two scopes expands to two real scope checks; a
  `roles` list of one role maps to the expected scope set; the existing `scopes`-list behavior is
  unchanged for callers not using the new format. Integration test: mint a token shaped like a
  real Entra token (via the `devauth` module's pattern, extended to emit `roles`/`scp` instead of
  `scopes`) and confirm the middleware resolves it to the correct internal scopes. For JWKS: point
  at a URL, rotate the underlying key, confirm the middleware picks up the new key without a
  restart (within whatever refresh interval is chosen).
- **Evidence** — The three claim-format unit tests pass and a fourth, deliberately malformed
  input (neither list nor string) still refuses cleanly rather than crashing. The minted-token
  integration test authorizes/denies exactly as a hand-computed expected-scopes set predicts. The
  JWKS rotation test shows the middleware serving requests correctly on the new key inside the
  refresh window, with no manual intervention.

## Definition of done

A package is done when all seven are true.

1. **The scenario in the package has been run by hand**, against the real service, output read
   rather than assumed.
2. **The result is recorded** in `cookbook/95_azure_migration/TEST_LOG.md` with the package
   identifier.
3. **A runnable script exists** under `cookbook/95_azure_migration/`.
4. **Both variants exist** for every new public method, sync and async.
5. **Formatting and validation pass**, via `./scripts/format.sh` and `./scripts/validate.sh`.
6. **The pull request names which package (AZ-A/B/C/D/P0) and which gap number** from the plan
   file it closes.
7. **Nothing retail landed in `libs/agno/egpt/`**, and nothing framework-general landed only in
   `product/egpt_retail/` (AZ-A stays product; AZ-B/AZ-D stay framework; AZ-C is a product-side
   consumer of AZ-B, never a fork of it).
