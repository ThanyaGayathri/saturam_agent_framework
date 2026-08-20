"""The semantic fact store: one write path across vector and graph (SM-S1 #118).

Talks to real infrastructure, both substrates at once:

    ./cookbook/scripts/run_pgvector.sh
    ./cookbook/scripts/run_neo4j.sh

`13_vector_substrate_pgvector.py` and `25_semantic_subgraph.py` each show one substrate's fact
handling in isolation -- writing straight through `VectorSubstrate.upsert` / `GraphSubstrate
.upsert_facts`. `SemanticFactStore` is the seam SM-L3 (#124, fact extraction) depends on to write
both from one call, and this file's point is the acceptance criterion itself: a fact written once is
**retrievable via both paths, in the same shape**. Before this ticket, `CoLocatedMemoryProvider
.consolidate` wrote the vector leg from `FactWrite.record` verbatim, so a fact read back through
`VectorSubstrate.search` carried none of the subject/predicate/object/confidence/derived_by a
`GraphSubstrate.recall_facts` read already surfaced -- two retrieval paths, two different shapes for
one fact. `egpt.memory.schemas.semantic.fact_record` is the projection that closes that; this file
shows its effect end to end rather than the schema module in isolation.

Fact **extraction** from episodes is explicitly out of scope here and is SM-L3's (#124) -- the
`FactWrite`s below are handed to the store directly, the way a distillation pass would after
deciding what to write, not derived from raw episodes by an LLM call.
"""

import os

from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.semantic import SemanticFactStore
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import FactWrite, MemoryQuery, MemoryRecord, Scope
from egpt.providers.model_provider import ProviderMode

ACME_ADA = Scope(tenant_id="acme", user_id="ada")

config = MemoryProviderConfig(
    provider="co_located",
    mode=ProviderMode.LOCAL,
    vector={
        "backend": "pgvector",
        "db_url": os.getenv(
            "EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
        ),
        "table": "memory_vectors_sm_s1",
    },
    graph={
        "backend": "neo4j",
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7691"),
        "username": os.getenv("NEO4J_USERNAME", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "smg1testpassword"),
    },
)

provider = build_memory_provider(config)
assert provider.vector is not None and provider.graph is not None, (
    "This cookbook needs both substrates -- check EGPT_MEMORY_PG_URL / NEO4J_URI and that "
    "./cookbook/scripts/run_pgvector.sh and ./cookbook/scripts/run_neo4j.sh are both running."
)

MARCH = 1_600_000_000

store = SemanticFactStore(vector=provider.vector, graph=provider.graph)

# A triple fact and an untripled one -- `FactWrite.is_triple()` requires all three of
# subject/predicate/object, and a fact that resists triple extraction is still storable (SM-R12's
# `SemanticFact` makes SPO optional structure over the free-text `content`).
receipt = store.write(
    ACME_ADA,
    [
        FactWrite(
            record=MemoryRecord(
                id="ada-works-at-northstar",
                tier=MemoryTier.SEMANTIC,
                content="Ada works at NorthStar Analytics",
                scope=ACME_ADA,
                t_valid=MARCH,
            ),
            subject="Ada",
            predicate="works_at",
            object="NorthStar Analytics",
            confidence=0.95,
            source_episode_ids=["ep-1", "ep-2"],
            derived_by="sm_l3.extract",
        ),
        FactWrite(
            record=MemoryRecord(
                id="ada-prefers-async-standups",
                tier=MemoryTier.SEMANTIC,
                content="Ada prefers async standups over live ones",
                scope=ACME_ADA,
                t_valid=MARCH,
            ),
            confidence=0.7,
            source_episode_ids=["ep-3"],
            derived_by="sm_l3.extract",
        ),
    ],
)
print(f"--- write receipt: accepted={receipt.accepted}, ids={receipt.event_ids} ---")

print()
print("--- retrieved through the vector leg directly (VectorSubstrate.search) ---")
# `provider.vector` directly, not `provider.read` -- SEMANTIC's TIER_POLICY routes to both
# substrates, so a fused read would merge this with the graph leg's own `traverse` hits and blur
# the point of this file, which is that *each leg on its own* now carries the same shape.
vector_hits = provider.vector.search(
    ACME_ADA, MemoryQuery(text="Ada NorthStar", tiers=[MemoryTier.SEMANTIC])
)
for record in vector_hits:
    print(f"  content={record.content!r}")
    print(f"    provenance={record.provenance}")
    print(f"    metadata={record.metadata}")

print()
print("--- retrieved through the graph leg directly (GraphSubstrate.recall_facts) ---")
graph_hits = provider.graph.recall_facts(ACME_ADA, entities=["Ada"])
for edge in graph_hits:
    print(
        f"  {edge.provenance['subject']} --[{edge.provenance['predicate']}]--> {edge.provenance['object']}"
    )
    print(f"    metadata={edge.metadata}")

print()
print("--- both paths agree on provenance and confidence for the triple fact ---")
vector_row = next(r for r in vector_hits if r.id == "ada-works-at-northstar")
graph_row = next(r for r in graph_hits if r.id == "ada-works-at-northstar")
print(
    f"  vector: subject={vector_row.provenance.get('subject')!r} confidence={vector_row.metadata.get('confidence')}"
)
print(
    f"  graph:  subject={graph_row.provenance.get('subject')!r} confidence={graph_row.metadata.get('confidence')}"
)
assert vector_row.provenance["subject"] == graph_row.provenance["subject"]
assert vector_row.metadata["confidence"] == graph_row.metadata["confidence"] == 0.95
assert (
    vector_row.metadata["derived_by"]
    == graph_row.metadata["derived_by"]
    == "sm_l3.extract"
)

provider.close()
