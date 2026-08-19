"""The semantic subgraph: resolved entities and the fact edges between them (SM-G7 #105).

Like `18_episodic_subgraph.py`, this talks to a real Neo4j and needs no API key -- the entity and
fact edges are written directly here rather than through an LLM extraction pass, because what this
file shows is the *read* side (SM-G2 #100 / SM-G3 #101 already ship the write side; `17_` and `19_`
show those, model call and all):

    ./cookbook/scripts/run_neo4j.sh

Where `18_` reads the **episodic** subgraph -- raw episodes, recalled by valid time and by mention
link -- this reads the **semantic** one: resolved `(:Entity)` nodes and the `(:Entity)-[:REL]->
(:Entity)` fact edges between them, recalled by entity, by relation label, and by valid time. The
two subgraphs are deliberately distinct reads over distinct tiers (EPISODIC vs SEMANTIC) -- neither
`recall_episodes` nor `recall_facts` answers the other's question, and `traverse` is the third,
relevance-ranked way to read across both at once.

`recall_facts` covers **fact edges only**, not the untripled `(:Fact)` node SM-G3's docstring also
describes: a node carries no subject or object to filter on, so it stays reachable only through
`traverse`.
"""

import os

from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import EntityWrite, FactWrite, MemoryRecord, Scope
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

MARCH = 1_600_000_000
JUNE = 1_700_000_000

# The resolved entities SM-G2's pipeline would otherwise extract -- written directly so this file
# needs no model call. Fixed ids (via deterministic (scope, name) merge) mean a second run updates
# these nodes rather than duplicating them.
graph.upsert_entities(
    ACME_ADA,
    [
        EntityWrite(name="Ada", entity_type="person", scope=ACME_ADA),
        EntityWrite(name="NorthStar Analytics", entity_type="company", scope=ACME_ADA),
        EntityWrite(name="Berlin", entity_type="place", scope=ACME_ADA),
    ],
)

# The fact edges SM-G3's pipeline would otherwise extract. Ids are stable (`relation_edge_id_for`
# is deterministic in the triple), so a second run replaces these three rows rather than growing
# a fresh set each time this is run.
graph.upsert_facts(
    ACME_ADA,
    [
        FactWrite(
            record=MemoryRecord(
                id="ada-works-at-northstar",
                tier=MemoryTier.SEMANTIC,
                content="Ada works_at NorthStar Analytics",
                scope=ACME_ADA,
                t_valid=MARCH,
            ),
            subject="Ada",
            predicate="works_at",
            object="NorthStar Analytics",
        ),
        FactWrite(
            record=MemoryRecord(
                id="northstar-located-in-berlin",
                tier=MemoryTier.SEMANTIC,
                content="NorthStar Analytics located_in Berlin",
                scope=ACME_ADA,
                t_valid=MARCH,
            ),
            subject="NorthStar Analytics",
            predicate="located_in",
            object="Berlin",
        ),
        FactWrite(
            record=MemoryRecord(
                id="ada-relocated-to-berlin",
                tier=MemoryTier.SEMANTIC,
                content="Ada relocated_to Berlin",
                scope=ACME_ADA,
                t_valid=JUNE,
            ),
            subject="Ada",
            predicate="relocated_to",
            object="Berlin",
        ),
    ],
)

print("--- by entity: every fact edge involving Ada, at either end ---")
for edge in graph.recall_facts(ACME_ADA, entities=["Ada"]):
    print(
        f"  {edge.provenance['subject']} --[{edge.provenance['predicate']}]--> {edge.provenance['object']}"
    )

print()
print("--- by relation label: everything Ada or anyone else is located_in ---")
for edge in graph.recall_facts(ACME_ADA, relation="located_in"):
    print(
        f"  {edge.provenance['subject']} --[{edge.provenance['predicate']}]--> {edge.provenance['object']}"
    )

print()
print("--- by valid time: what was true before June ---")
for edge in graph.recall_facts(ACME_ADA, since=0, until=JUNE):
    print(
        f"  {edge.t_valid}  {edge.provenance['subject']} --[{edge.provenance['predicate']}]--> {edge.provenance['object']}"
    )

print()
print("--- the two subgraphs are distinct reads over distinct tiers ---")
# `recall_episodes` reads the EPISODIC tier, `recall_facts` the SEMANTIC one -- separate reads,
# not one query filtered after the fact. The episode count below is whatever `18_` (run against
# this same acme/ada scope) left behind, not zero -- the point is that `recall_facts` never
# returns one of them, and `recall_episodes` never returns one of this file's fact edges.
episodes = graph.recall_episodes(ACME_ADA, since=0)
facts = graph.recall_facts(ACME_ADA, since=0)
print(
    f"  episodic subgraph (SM-G6): {len(episodes)} row(s), tiers={ {r.tier for r in episodes} }"
)
print(
    f"  semantic subgraph (SM-G7): {len(facts)} fact edge(s), tiers={ {r.tier for r in facts} }"
)

print()
print("--- an unfiltered recall is refused, not answered ---")
# Every fact edge in scope is an enumeration, and what enumeration means over a temporal graph is
# SM-G9's (#107) to define -- the same refusal `recall_episodes` gives for the same reason.
try:
    graph.recall_facts(ACME_ADA)
except ValueError as exc:
    print(f"  {exc}")

provider.close()
