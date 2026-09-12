"""AZ-B: exercise the Azure AI Search memory substrate end-to-end through the registry seam.

**What this script proves, and what it does not.** There is no Azure CLI, no Azure subscription,
and no reachable Azure AI Search service in the environment this was built and run in -- see
`TEST_LOG.md` for the explicit list. So every HTTP call `azure-search-documents`' `SearchClient`
and `SearchIndexClient` would make is intercepted here with `unittest.mock.patch`, at the method
level (`create_or_update_index`, `merge_or_upload_documents`, `search`). What *is* proven end to
end, against the real SDK's real classes and real request-building code (only the actual transport
call is faked):

* the registry seam resolves `"azure_ai_search"` to `AzureAISearchMemorySubstrate` exactly the way
  it resolves `"pgvector"` to `PgVectorMemorySubstrate` -- `product/egpt_retail/memory/config.py`
  can flip one string and get this backend, with no other code change (that flip itself is
  deliberately out of scope for this PR -- see the migration plan's AZ-B entry);
* a full upsert -> search round trip through the real `AzureAISearchMemorySubstrate` code path,
  including the real document shape sent to `merge_or_upload_documents` and the real OData
  `$filter` sent to `search`;
* the tenant-isolation property, directly: two tenants' scopes produce two different, mutually
  exclusive filters, proven on the literal `filter=` string each request would carry.

What this script cannot prove, because nothing in this environment can: whether Azure AI Search
actually accepts this index definition, actually returns documents ranked the way the SDK's own
scoring documentation says, or actually enforces the vector-dimension limits this module assumes.
"""

from unittest.mock import MagicMock, patch

from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryQuery, MemoryRecord, Scope
from egpt.registry import discovery

DIM = 8


class DemoEmbedder:
    """A tiny deterministic embedder -- offline-capable, so Mode A's air-gap check accepts it with
    no network reachable, matching every other cookbook in this repo that avoids a real model
    download for a script whose point is the substrate, not the embedding."""

    dimensions = DIM
    offline_capable = True

    def get_embedding(self, text: str):
        return [float(len(token) % 5) for token in (text.split() + ["pad"] * DIM)][:DIM]

    async def async_get_embedding(self, text: str):
        return self.get_embedding(text)


def main() -> None:
    print("=== AZ-B: Azure AI Search memory substrate, via the registry seam ===\n")

    with patch(
        "azure.search.documents.indexes.SearchIndexClient.create_or_update_index"
    ) as create_index:
        create_index.return_value = MagicMock()
        substrate = discovery.construct(
            "vectorstore",
            "azure_ai_search",
            endpoint="https://egpt-demo.search.windows.net",
            api_key="demo-key-not-a-real-secret",
            index_name="egpt-memory-demo",
            embedder=DemoEmbedder(),
        )
    print(
        f"1. Resolved via discovery.construct('vectorstore', 'azure_ai_search', ...): {type(substrate).__name__}"
    )
    print(
        f"   Index schema requested: {create_index.call_args.args[0].name!r}, dim={substrate.dim}\n"
    )

    acme = Scope(tenant_id="acme", user_id="ada")
    globex = Scope(tenant_id="globex", user_id="wile")

    record = MemoryRecord(
        id="e1",
        tier=MemoryTier.EPISODIC,
        content="the invoice total was 4200 dollars",
        scope=acme,
    )
    with patch(
        "azure.search.documents.SearchClient.merge_or_upload_documents"
    ) as upsert:
        upsert.return_value = MagicMock()
        substrate.upsert(acme, [record])
    document = upsert.call_args.kwargs["documents"][0]
    print("2. upsert() sent this document to merge_or_upload_documents:")
    for key in (
        "doc_key",
        "id",
        "tenant_id",
        "user_id",
        "tier",
        "content",
        "embedding_version",
    ):
        print(f"     {key}: {document[key]!r}")
    print(f"     embedding: {len(document['embedding'])} floats\n")

    with patch("azure.search.documents.SearchClient.search") as search:
        search.return_value = iter(
            [
                {
                    "@search.score": 0.87,
                    "id": "e1",
                    "tier": "episodic",
                    "content": "the invoice total was 4200 dollars",
                    "tenant_id": "acme",
                    "agent_id": None,
                    "user_id": "ada",
                    "team_id": None,
                    "session_id": None,
                    "topics_json": None,
                    "provenance_json": None,
                    "metadata_json": None,
                    "t_valid": None,
                    "t_invalid": None,
                    "created_at": 1_700_000_000,
                    "expired_at": None,
                    "superseded_by": None,
                }
            ]
        )
        results = substrate.search(acme, MemoryQuery(text="invoice total", limit=5))
    print("3. search() returned:")
    for hit in results:
        print(f"     id={hit.id!r} score={hit.score!r} content={hit.content!r}")
    print(
        f"   Request sent: search_text={search.call_args.kwargs['search_text']!r}, "
        f"filter={search.call_args.kwargs['filter']!r}\n"
    )

    print("4. Tenant isolation -- the literal filter each tenant's search would send:")
    with patch("azure.search.documents.SearchClient.search") as search_acme:
        search_acme.return_value = iter([])
        substrate.search(acme, MemoryQuery(text="invoice", limit=5))
    with patch("azure.search.documents.SearchClient.search") as search_globex:
        search_globex.return_value = iter([])
        substrate.search(globex, MemoryQuery(text="invoice", limit=5))
    acme_filter = search_acme.call_args.kwargs["filter"]
    globex_filter = search_globex.call_args.kwargs["filter"]
    print(f"     acme:   {acme_filter!r}")
    print(f"     globex: {globex_filter!r}")
    assert "globex" not in acme_filter and "wile" not in acme_filter
    assert "acme" not in globex_filter and "ada" not in globex_filter
    print(
        "     -> confirmed: neither tenant's filter names the other tenant's scope.\n"
    )

    print("5. search_candidates() issues two unfused legs, not a native RRF call:")
    with patch("azure.search.documents.SearchClient.search") as candidates_search:
        candidates_search.side_effect = [iter([]), iter([])]
        substrate.search_candidates(acme, MemoryQuery(text="invoice total", limit=5))
    vector_call, lexical_call = (
        call.kwargs for call in candidates_search.call_args_list
    )
    print(
        f"     leg 1 (vector): search_text={vector_call['search_text']!r}, vector_queries set={vector_call['vector_queries'] is not None}"
    )
    print(
        f"     leg 2 (lexical): search_text={lexical_call['search_text']!r}, vector_queries set={lexical_call['vector_queries'] is not None}"
    )

    print(
        "\n=== All assertions passed against the mocked SDK. See TEST_LOG.md for the ==="
    )
    print(
        "=== explicit boundary between what this proves and what needs a live Azure ==="
    )
    print("=== AI Search resource. ===")


if __name__ == "__main__":
    main()
