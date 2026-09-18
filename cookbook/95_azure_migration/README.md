# 95 · Azure migration

Runnable checks for the Azure migration plan (`docs/plan/azure-migration-plan.md`), one folder
for the whole initiative rather than mixed into `cookbook/94_retail/`'s retail-specific log,
per that plan's own "How each deliverable is verified" section. One folder, one `TEST_LOG.md`,
entries tagged by package (AZ-A/B/C/D).

| Script | Package | What it proves |
|---|---|---|
| `AZ_A_blob_roundtrip.py` | AZ-A | `AzureBlobObjectStore` against a real local Azurite: lists and reads C1's committed landing fixtures byte-for-byte identically to `LocalDirectoryStore`, refuses a path that escapes the landing root, and refuses a half-configured credential pair at construction. Needs Docker (Azurite). |
| `AZ_C_knowledge_prose.py` | AZ-C | K2's domain prose through `build_knowledge_vector_db`, the product's backend-selected factory (`EGPT_RETAIL_KNOWLEDGE_BACKEND`, default `azure_ai_search`). Ingests a document with known headings, searches two mid-section phrases, and asserts chunk boundaries fall on headings and the whole section comes back -- on pgvector as the baseline, then diffed against Azure AI Search when `EGPT_RETAIL_KNOWLEDGE_SEARCH_ENDPOINT` is set (skipped and said so otherwise). Needs compose Postgres and the local embedder. |

| `AZ_B_ai_search_substrate.py` | AZ-B | `AzureAISearchMemorySubstrate` resolved through the registry seam (`discovery.construct("vectorstore", "azure_ai_search", ...)`), against the real `azure-search-documents` classes with only the transport calls mocked -- there is no Azure AI Search emulator. See `TEST_LOG.md` for the proven/not-proven boundary. |
| `product/tests/test_az_b_memory_backend.py` (unit tests, not a script) | AZ-B | The product-side flip: `EGPT_RETAIL_MEMORY_BACKEND` defaults to `azure_ai_search`, the `pgvector` dict is unchanged, the Azure dict carries exactly the substrate's constructor kwargs, a missing `EGPT_RETAIL_MEMORY_SEARCH_ENDPOINT` is refused by name at startup, and a provider builds through `build_memory_provider` with one mocked `create_or_update_index` call. Recorded under `AZ-B memory backend flip` in `TEST_LOG.md`. |
