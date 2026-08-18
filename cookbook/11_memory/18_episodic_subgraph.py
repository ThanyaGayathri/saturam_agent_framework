"""Raw episodes as graph nodes, linked to the entities they mention (SM-G6 #104).

Like `10_graph_substrate_neo4j.py`, this talks to a real Neo4j:

    ./cookbook/scripts/run_neo4j.sh

The episodic tier's defining marker is that it is **non-lossy**: the episode is stored as it
happened, not distilled into facts. What SM-G6 adds is the other half of Zep's episodic subgraph --
edges from the episode to each entity it names -- so an episode is reachable from the semantic side
and back, and can be recalled by *when* it happened and by *who or what it was about*.

Two things this deliberately does not do, because they belong to other tickets:

* it does not read `content` to find the entities. Extraction is SM-G2 (#100); `MemoryEvent.entities`
  is the carrier, and here the caller fills it.
* it does not decide that "Berlin" and "berlin" are one entity. That is entity resolution, SM-G8
  (#106), and folding here would settle it by side effect.
"""

import os

from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.graph.episodic import entity_node_id_for
from egpt.memory.types import MemoryEvent, Scope
from egpt.providers.model_provider import ProviderMode

ACME_ADA = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")

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

# Valid time -- when it happened, not when it was recorded. Fixed here so the window reads below
# have a timeline to answer against.
MARCH = 1_600_000_000
APRIL = 1_620_000_000
JUNE = 1_700_000_000

# The ids are stable because upsert is by id: a second run replaces these four episodes rather than
# writing four more under fresh uuids, so the output below does not grow each time this is run.
provider.write(
    ACME_ADA,
    [
        MemoryEvent(
            content="ada moved to berlin and joined the platform team",
            scope=ACME_ADA,
            id="ep-ada-march",
            entities=["ada", "berlin", "platform team"],
            occurred_at=MARCH,
        ),
        MemoryEvent(
            content="ada shipped the scheduler rewrite",
            scope=ACME_ADA,
            id="ep-ada-april",
            entities=["ada", "scheduler"],
            occurred_at=APRIL,
        ),
        MemoryEvent(
            content="ada spoke at the berlin meetup",
            scope=ACME_ADA,
            id="ep-ada-june",
            entities=["ada", "berlin"],
            occurred_at=JUNE,
        ),
    ],
)
provider.write(
    ACME_BOB,
    [
        MemoryEvent(
            content="bob visited the berlin office",
            scope=ACME_BOB,
            id="ep-bob-april",
            entities=["bob", "berlin"],
            occurred_at=APRIL,
        )
    ],
)

assert provider.graph is not None, (
    "This cookbook needs a graph backend -- check NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD "
    "and that ./cookbook/scripts/run_neo4j.sh is running."
)
graph = provider.graph

print("--- by mention link: everything about berlin, oldest first ---")
for record in graph.recall_episodes(ACME_ADA, entities=["berlin"]):
    print(f"  {record.t_valid}  {record.content}")

print()
print("--- by time: the first half of the timeline ---")
for record in graph.recall_episodes(ACME_ADA, since=MARCH, until=JUNE):
    print(f"  {record.t_valid}  {record.content}")

print()
print("--- time and link together, which is narrower than either ---")
for record in graph.recall_episodes(
    ACME_ADA, entities=["berlin"], since=MARCH, until=JUNE
):
    print(f"  {record.t_valid}  {record.content}")

print()
print("--- store an episode, traverse to its entities ---")
# The ids come off the MENTIONS edges, not off a copy stored on the row -- and they are derived
# from the entity names, so they are the same ids the episode row carries in `entity_ids`.
first = graph.recall_episodes(ACME_ADA, entities=["berlin"], limit=1)[0]
for entity_id in first.provenance["entity_ids"]:
    print(f"  {entity_id}")

print()
print("--- one entity name, two users, two entity nodes ---")
# Entities merge on the full scope, not the tenant. Sharing them across users would make the
# isolation guarantee depend on an entity-resolution policy nobody has written yet.
print(f"  ada's berlin: {entity_node_id_for(ACME_ADA, 'berlin')}")
print(f"  bob's berlin: {entity_node_id_for(ACME_BOB, 'berlin')}")
print(
    f"  bob's episodes about berlin: {[r.id for r in graph.recall_episodes(ACME_BOB, entities=['berlin'])]}"
)

print()
print("--- an unfiltered recall is refused, not answered ---")
# Every episode in scope is an enumeration, and what enumeration means over a temporal graph is
# SM-G9's (#107) to define. A verb that answered it would settle that question by accident.
try:
    graph.recall_episodes(ACME_ADA)
except ValueError as exc:
    print(f"  {exc}")

provider.close()
