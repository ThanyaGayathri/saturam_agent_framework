"""Personalized PageRank multi-hop retrieval (SM-H4 #134), optional and off by default.

Needs a GDS-plugin Neo4j, not the plain SM-G1 dev instance `28_multihop_graph_traversal.py` uses:

    ./cookbook/scripts/run_neo4j_gds.sh

`traverse`'s anchor-seeded BFS leg (SM-G9 #107, `28_multihop_graph_traversal.py`) is a bounded-hop
walk -- ranked by hop distance, but it stops at `max_traverse_hops` (default 2) regardless of what
lies beyond. PPR is a single-step *ranked* walk over the whole graph's stationary distribution: no
hop ceiling, so it reaches evidence a bounded BFS cannot, at the cost of a GDS graph projection per
call. Gated behind `multihop_ppr_enabled` (default `False`, SM-R9's ratified switch) because that
projection cost is real and most tenants do not need multi-hop at all.

No query-time LLM: PPR's seed entities come from the identical word-overlap anchor selection the
BFS leg already uses (`egpt.integrations.memory.graph.neo4j.cypher._anchor_predicate`), not a
separate NER pass.
"""

import os

from egpt.integrations.memory.graph.neo4j import Neo4jGraphSubstrate
from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import EntityWrite, FactWrite, MemoryQuery, MemoryRecord, Scope
from egpt.providers.model_provider import ProviderMode

ACME_ADA = Scope(tenant_id="acme", user_id="ada")


def gds_settings():
    return {
        "uri": os.getenv("NEO4J_GDS_URI", "bolt://localhost:7692"),
        "username": os.getenv("NEO4J_GDS_USERNAME", "neo4j"),
        "password": os.getenv("NEO4J_GDS_PASSWORD", "smh4testpassword"),
    }


config = MemoryProviderConfig(
    provider="co_located",
    mode=ProviderMode.LOCAL,
    graph={
        "backend": "neo4j",
        "multihop_ppr_enabled": True,
        "multihop_ppr_seed_top_m": 3,
        **gds_settings(),
    },
)

provider = build_memory_provider(config)
assert provider.graph is not None, (
    "This cookbook needs a GDS-plugin graph backend -- check NEO4J_GDS_URI / NEO4J_GDS_USERNAME / "
    "NEO4J_GDS_PASSWORD and that ./cookbook/scripts/run_neo4j_gds.sh is running."
)
graph = provider.graph

# A four-hop chain: Ada -> NorthStar -> Berlin -> Germany -> EU. Written directly, as
# `28_multihop_graph_traversal.py` does -- no LLM extraction pass needed to demonstrate retrieval.
graph.upsert_entities(
    ACME_ADA,
    [
        EntityWrite(name="Ada", entity_type="person", scope=ACME_ADA),
        EntityWrite(name="NorthStar Analytics", entity_type="company", scope=ACME_ADA),
        EntityWrite(name="Berlin", entity_type="place", scope=ACME_ADA),
        EntityWrite(name="Germany", entity_type="place", scope=ACME_ADA),
        EntityWrite(name="EU", entity_type="place", scope=ACME_ADA),
    ],
)


def edge(id_, subject, predicate, object_):
    return FactWrite(
        record=MemoryRecord(
            id=id_,
            tier=MemoryTier.SEMANTIC,
            content=f"{subject} {predicate} {object_}",
            scope=ACME_ADA,
            t_valid=1_600_000_000,
        ),
        subject=subject,
        predicate=predicate,
        object=object_,
    )


graph.upsert_facts(
    ACME_ADA,
    [
        edge("ada-works-at-northstar", "Ada", "works_at", "NorthStar Analytics"),
        edge(
            "northstar-located-in-berlin", "NorthStar Analytics", "located_in", "Berlin"
        ),
        edge("berlin-part-of-germany", "Berlin", "part_of", "Germany"),
        edge("germany-member-of-eu", "Germany", "member_of", "EU"),
    ],
)

print("--- BFS alone (max_traverse_hops=2): stops two hops from the anchor ---")
bfs_only = Neo4jGraphSubstrate(**gds_settings(), auto_init_schema=False)
bfs_hits = {
    r.id
    for r in bfs_only.traverse(
        ACME_ADA, MemoryQuery(text="tell me about Ada", limit=10)
    )
}
print(f"  reached: {sorted(bfs_hits)}")
assert "germany-member-of-eu" not in bfs_hits, (
    "hop 4 is well past the default 2-hop bound"
)
bfs_only.close()

print()
print("--- with PPR enabled, the same query reaches the fourth hop ---")
ppr_hits = {
    r.id
    for r in graph.traverse(ACME_ADA, MemoryQuery(text="tell me about Ada", limit=10))
}
print(f"  reached: {sorted(ppr_hits)}")
assert "germany-member-of-eu" in ppr_hits, (
    "PPR's ranked walk is not hop-bounded the way BFS is"
)

print()
print(
    "--- composing with bi-temporal filtering: an invalidated fact drops out of the projection ---"
)
graph.invalidate(
    ACME_ADA, "ada-works-at-northstar", t_invalid=1_700_000_000, at=1_700_000_100
)
still_valid = graph._ppr_rows(
    ACME_ADA, MemoryQuery(text="Ada", limit=10, as_of=1_650_000_000)
)
past_invalidation = graph._ppr_rows(
    ACME_ADA, MemoryQuery(text="Ada", limit=10, as_of=1_800_000_000)
)
print(
    f"  as_of before t_invalid: {'ada-works-at-northstar' in {r['id'] for r in still_valid}}"
)
print(
    f"  as_of after  t_invalid: {'ada-works-at-northstar' in {r['id'] for r in past_invalidation}}"
)
assert "ada-works-at-northstar" in {r["id"] for r in still_valid}
assert "ada-works-at-northstar" not in {r["id"] for r in past_invalidation}

provider.close()
