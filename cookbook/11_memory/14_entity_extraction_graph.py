"""Entity extraction from episodes into temporal-graph nodes (SM-G2 #100).

Needs Neo4j, Postgres + pgvector, and an OpenAI key -- extraction is a model pass, one call per
interaction:

    ./cookbook/scripts/run_neo4j.sh
    ./cookbook/scripts/run_pgvector.sh

What it shows is where the pass belongs. Extraction runs on the **consolidation lane**, driven by
`provider.consolidate()`, and nothing on the read path calls it: a query costs no model call, which
is SM-R8's zero-query-time-LLM rule. Wiring it is one `entity_pipeline` option; leave it out and
`consolidate` reports `entities` as not extracted rather than looking like it found none.

What lands is a **candidate**, not a resolved entity. Two spellings of one company stay two nodes
below, deliberately: matching and merging them is entity resolution, SM-G8 (#106). This ticket's
job is to put every mention in the graph with an embedding, so SM-G8 has something to match over.
"""

import os

from egpt.integrations.knowledge.embedder.sentence_transformer import (
    SentenceTransformerEmbedder,
)
from egpt.integrations.memory.graph.neo4j import Neo4jGraphSubstrate
from egpt.integrations.models.openai import OpenAIResponses
from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.graph.entities import EntityExtractionPipeline, LLMEntityExtractor
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope
from egpt.providers.model_provider import ProviderMode

ACME_ADA = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")

# The substrate is built here rather than named by a registry key, because the pipeline and the
# provider must share **one** graph and the provider is what would otherwise construct it. Both are
# handed the same object through `options`, which is the supported injection path.
graph = Neo4jGraphSubstrate(
    uri=os.getenv("NEO4J_URI", "bolt://localhost:7691"),
    username=os.getenv("NEO4J_USERNAME", "neo4j"),
    password=os.getenv("NEO4J_PASSWORD", "smg1testpassword"),
)

# 768 is stated because `SentenceTransformerEmbedder` defaults to 384 whatever model it loads, and
# bge-base emits 768. One instance serves both the vector substrate and the entity pipeline: two
# would be two dimensions in one deployment, which the pipeline refuses per pass and pgvector
# refuses per column.
embedder = SentenceTransformerEmbedder(id="BAAI/bge-base-en-v1.5", dimensions=768)

pipeline = EntityExtractionPipeline(
    graph=graph,
    extractor=LLMEntityExtractor(model=OpenAIResponses(id="gpt-5.5")),
    embedder=embedder,
)

# The vector substrate is not optional: `consolidate` enumerates the episodic tier through it, so
# the graph alone has nothing to extract from. Needs `./cookbook/scripts/run_pgvector.sh`.
provider = build_memory_provider(
    MemoryProviderConfig(
        provider="co_located",
        mode=ProviderMode.LOCAL,
        vector={
            "backend": "pgvector",
            "db_url": os.getenv("EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"),
            "embedder": embedder,
            # Its own table, because `consolidate` reads **every** episode in the scope: sharing the
            # default with 13_vector_substrate_pgvector.py made this file extract entities from that
            # one's memories and print a node list nobody could match to the writes below.
            "table": "memory_vectors_sm_g2",
        },
        options={"graph_substrate": graph, "entity_pipeline": pipeline},
    )
)

MARCH = 1_600_000_000

# Ids are stated so a second run replaces these rows rather than accumulating a fresh set.
provider.write(
    ACME_ADA,
    [
        MemoryEvent(
            content="Ada is evaluating NorthStar Analytics; their VP of Engineering is Sarah Chen.",
            scope=ACME_ADA,
            id="ada-northstar",
            occurred_at=MARCH,
        ),
        MemoryEvent(
            content="NorthStar Analytics runs Snowflake and dbt, and Sarah Chen owns the decision.",
            scope=ACME_ADA,
            id="ada-northstar-stack",
            occurred_at=MARCH + 60,
        ),
    ],
)
provider.write(
    ACME_BOB,
    [
        MemoryEvent(
            content="Bob met NorthStar, the same analytics startup, at the Lisbon conference.",
            scope=ACME_BOB,
            id="bob-northstar",
            occurred_at=MARCH + 120,
        )
    ],
)

print("A read costs no model call -- extraction is not on this path:")
slice_ = provider.read(ACME_ADA, MemoryQuery(text="northstar", tiers=[MemoryTier.EPISODIC]))
print(f"  {len(slice_.records)} episode(s) for acme/ada")

print("\nConsolidating acme/ada -- this is where extraction runs:")
report = provider.consolidate(ACME_ADA)
print(f"  episodes scanned: {report.episodic_scanned}")
print(f"  entities: {report.details['entities']}")

print("\nConsolidating acme/bob:")
print(f"  entities: {provider.consolidate(ACME_BOB).details['entities']}")


def entity_nodes(scope):
    """Read the entity nodes back with Cypher.

    The port has no read verb for them: querying the semantic subgraph is SM-G7 (#105) / SM-G9
    (#107), and inventing one here would ship a surface this ticket cannot test.
    """
    with graph._driver.session(database=graph.database) as session:
        return [
            dict(row)
            for row in session.run(
                "MATCH (n:Entity {scope_key: $scope_key}) WHERE n.key IS NOT NULL "
                "RETURN n.name AS name, n.entity_type AS type, n.key AS key, "
                "size(n.embedding) AS dims, n.source_episode_ids AS episodes ORDER BY n.name",
                scope_key=scope.key(),
            )
        ]


print("\nEntity nodes for acme/ada:")
for node in entity_nodes(ACME_ADA):
    print(f"  {node['name']} ({node['type']}) key={node['key']} dims={node['dims']} from={node['episodes']}")

# Both users mentioned the same company. The nodes are separate, because entity identity is scoped
# to the full scope and not the tenant -- sharing them would make isolation depend on a dedup
# policy nobody has written yet.
print("\nEntity nodes for acme/bob (a separate node for the same company):")
for node in entity_nodes(ACME_BOB):
    print(f"  {node['name']} ({node['type']}) key={node['key']} from={node['episodes']}")

print("\nWhat SM-G8 (#106) inherits: candidates sharing no key that a human would still merge,")
print("each embedded so a similarity match can find them.")

provider.close()
