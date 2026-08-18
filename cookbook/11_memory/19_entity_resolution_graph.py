"""Entity resolution: merging duplicate candidates the graph already holds (SM-G8 #106).

Like `17_entity_extraction_graph.py`, this talks to a real Neo4j:

    ./cookbook/scripts/run_neo4j.sh

That cookbook's whole point was the boundary it deliberately did not cross: "Acme" and "Acme
Corporation" land as two separate candidate nodes, embedded so *this* ticket has something to
match over. This one closes it -- `EntityResolutionPipeline` matches each candidate against what
the graph already holds (an exact-key lookup plus an embedding-similarity lookup, both index-backed)
and folds a match into one node, re-pointing its `MENTIONS` edges and keeping every surface form as
an alias.

No LLM call: the entities below are constructed directly (as `EntityExtractionPipeline`'s output
would arrive) so this cookbook needs only an embedder, not an API key.
"""

import os

from egpt.integrations.knowledge.embedder.sentence_transformer import (
    SentenceTransformerEmbedder,
)
from egpt.integrations.memory.graph.neo4j import Neo4jGraphSubstrate
from egpt.memory.graph.resolve import EntityResolutionPipeline
from egpt.memory.types import EntityWrite, MemoryEvent, Scope

ACME_ADA = Scope(tenant_id="acme", user_id="ada")

graph = Neo4jGraphSubstrate(
    uri=os.getenv("NEO4J_URI", "bolt://localhost:7691"),
    username=os.getenv("NEO4J_USERNAME", "neo4j"),
    password=os.getenv("NEO4J_PASSWORD", "smg1testpassword"),
)

# 768 dimensions, matching the vector index `init_schema` declares (Mode A, ADR 0003 A.3). A
# mismatched width is not "no match" -- it is a query the index cannot answer.
embedder = SentenceTransformerEmbedder(id="BAAI/bge-base-en-v1.5", dimensions=768)


def candidate(name: str, description: str, episode_id: str) -> EntityWrite:
    """What `EntityExtractionPipeline.run` would have written for one mention -- entity type
    folded into the embedding text (`entities.embedding_text`) so "atlas" the project and "atlas"
    the person do not collide here for want of it."""
    write = EntityWrite(
        name=name,
        entity_type="company",
        scope=ACME_ADA,
        description=description,
        source_episode_ids=[episode_id],
    )
    vector = embedder.get_embedding(
        f"{write.name} | {write.entity_type} | {write.description}"
    )
    return write.with_embedding(vector, f"{embedder.id}/d{len(vector)}")


# Two episodes, two spellings of one company -- exactly the pair `17_entity_extraction_graph.py`
# leaves as two nodes.
graph.upsert_episodes(
    ACME_ADA,
    [
        MemoryEvent(
            content="Ada is evaluating Acme.",
            scope=ACME_ADA,
            id="ep-acme-1",
            entities=["Acme"],
        )
    ],
)
graph.upsert_episodes(
    ACME_ADA,
    [
        MemoryEvent(
            content="Acme Corporation's VP confirmed the pilot.",
            scope=ACME_ADA,
            id="ep-acme-2",
            entities=["Acme Corporation"],
        )
    ],
)
# The description is the same on purpose: `embedding_text` folds description into what gets
# embedded, so two candidates for one real company need a similar description to clear the default
# similarity floor -- differing descriptions of a *shared* entity are ADR 0003's job for a future
# fact-consolidation pass, not this ticket's exact-name/near-duplicate match.
candidates = [
    candidate("Acme", "an enterprise analytics company", "ep-acme-1"),
    candidate("Acme Corporation", "an enterprise analytics company", "ep-acme-2"),
]
graph.upsert_entities(ACME_ADA, candidates)

# The three `graph._driver` blocks below are demo narrative only, not the public API: the port has
# no read verb for entity nodes (querying the semantic subgraph is SM-G7 #105 / SM-G9 #107), the
# same gap `17_entity_extraction_graph.py`'s own `entity_nodes` helper notes.
print("Before resolution -- two nodes for one company:")
with graph._driver.session(database=graph.database) as session:
    for row in session.run(
        "MATCH (n:Entity {scope_key: $scope_key}) RETURN n.name AS name ORDER BY n.name",
        scope_key=ACME_ADA.key(),
    ):
        print(f"  {row['name']}")

pipeline = EntityResolutionPipeline(graph=graph)
report = pipeline.run(ACME_ADA, candidates)
print(
    f"\nResolution report: {report.candidates} candidates, {report.merged} merged into {report.groups} survivor(s)"
)

print("\nAfter resolution -- one node, aliases retained:")
with graph._driver.session(database=graph.database) as session:
    for row in session.run(
        "MATCH (n:Entity {scope_key: $scope_key}) RETURN n.name AS name, n.aliases AS aliases, "
        "n.source_episode_ids AS episodes",
        scope_key=ACME_ADA.key(),
    ):
        print(f"  {row['name']}  aliases={row['aliases']}  from={row['episodes']}")

print("\nBoth episodes' MENTIONS edges now point at the one surviving node:")
with graph._driver.session(database=graph.database) as session:
    for episode_id in ("ep-acme-1", "ep-acme-2"):
        names = [
            row["name"]
            for row in session.run(
                "MATCH (e:Episode {scope_key: $scope_key, id: $id})-[:MENTIONS]->(n:Entity) RETURN n.name AS name",
                scope_key=ACME_ADA.key(),
                id=episode_id,
            )
        ]
        print(f"  {episode_id}: {names}")

graph.close()
