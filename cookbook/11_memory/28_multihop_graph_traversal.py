"""Anchor-seeded multi-hop graph traversal (SM-G9 #107).

Like `25_semantic_subgraph.py`, this talks to a real Neo4j and needs no API key -- the entities and
fact edges are written directly here rather than through an LLM extraction pass:

    ./cookbook/scripts/run_neo4j.sh

`recall_facts` (SM-G7 #105) answers "which fact edges match this filter set" -- it is not ranked
and does not follow an edge. `traverse` is the ranked retriever: it scores rows whose own content
matches the query text directly, **and** (since SM-G9) fact edges reached by walking a bounded
number of `REL` hops from an entity the query text names -- the associative recall a lexical or
vector scorer cannot infer, because it surfaces a fact the query shares no words with at all.

No embedding and no model call: an anchor is chosen by word overlap against a stored entity's name,
matching the port's "no query-time LLM" contract.
"""

import os

from egpt.integrations.memory.graph.neo4j import Neo4jGraphSubstrate
from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import EntityWrite, FactWrite, MemoryQuery, MemoryRecord, Scope
from egpt.providers.model_provider import ProviderMode

ACME_ADA = Scope(tenant_id="acme", user_id="ada")

config = MemoryProviderConfig(
    provider="co_located",
    mode=ProviderMode.LOCAL,
    graph={
        "backend": "neo4j",
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7691"),
        "username": os.getenv("NEO4J_USERNAME", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "smg1testpassword"),
    },
)

provider = build_memory_provider(config)
assert provider.graph is not None, (
    "This cookbook needs a graph backend -- check NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD "
    "and that ./cookbook/scripts/run_neo4j.sh is running."
)
graph = provider.graph

# A three-hop chain: Ada -> NorthStar Analytics -> Berlin -> Germany. Entities are written
# explicitly here (SM-G2's extraction pipeline would otherwise do this), but the traversal below
# would find the same edges even if they had only ever been written through `upsert_facts` alone
# (SM-G3's `RelationExtractionPipeline` never calls `upsert_entities`) -- anchor selection falls
# back to a bare edge endpoint's name when no explicit entity write ever set its normalised `key`.
graph.upsert_entities(
    ACME_ADA,
    [
        EntityWrite(name="Ada", entity_type="person", scope=ACME_ADA),
        EntityWrite(name="NorthStar Analytics", entity_type="company", scope=ACME_ADA),
        EntityWrite(name="Berlin", entity_type="place", scope=ACME_ADA),
        EntityWrite(name="Germany", entity_type="place", scope=ACME_ADA),
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
    ],
)

print(
    "--- a query naming only 'Ada' surfaces facts about Ada's whole neighbourhood ---"
)
# Anchor: "Ada". Hop 1 crosses the "works_at" edge to NorthStar; hop 2 crosses the "located_in"
# edge to Berlin. "NorthStar Analytics located_in Berlin" shares no word with the query text at
# all -- it is surfaced purely by being graph-adjacent to the anchor within the hop bound.
hits = graph.traverse(ACME_ADA, MemoryQuery(text="tell me about Ada"))
by_id = {record.id: record for record in hits}
for record in hits:
    print(f"  score={record.score:.3f}  {record.content}")

assert "ada-works-at-northstar" in by_id, "hop 1 from the anchor"
assert "northstar-located-in-berlin" in by_id, "hop 2 from the anchor"
assert (
    by_id["ada-works-at-northstar"].score > by_id["northstar-located-in-berlin"].score
), "a nearer hop must outrank a farther one"

print()
print("--- the default hop bound (2) does not reach a third hop ---")
# "Berlin part_of Germany" is three hops from the anchor "Ada" (Ada->NorthStar is hop 1,
# NorthStar->Berlin is hop 2, Berlin->Germany is hop 3) -- one hop past the default bound.
assert "berlin-part-of-germany" not in by_id, "hop 3 is beyond the default 2-hop bound"

print()
print("--- the bound is set at construction, not per call -- try 1 hop and 3 hops ---")


def substrate_with(max_traverse_hops):
    return Neo4jGraphSubstrate(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7691"),
        username=os.getenv("NEO4J_USERNAME", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "smg1testpassword"),
        auto_init_schema=False,
        max_traverse_hops=max_traverse_hops,
    )


for hops in (1, 2, 3):
    bounded = substrate_with(hops)
    reached = {
        r.id for r in bounded.traverse(ACME_ADA, MemoryQuery(text="tell me about Ada"))
    } & {
        "ada-works-at-northstar",
        "northstar-located-in-berlin",
        "berlin-part-of-germany",
    }
    print(f"  max_traverse_hops={hops} -> {sorted(reached)}")
    bounded.close()

provider.close()
