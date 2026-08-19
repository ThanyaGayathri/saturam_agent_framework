"""Relation/fact edge extraction into typed graph edges (SM-G3 #101).

Needs Neo4j, Postgres + pgvector, and an OpenAI key -- extraction is a model pass, one call per
interaction:

    ./cookbook/scripts/run_neo4j.sh
    ./cookbook/scripts/run_pgvector.sh

What it shows is where the pass belongs, same as `17_entity_extraction_graph.py`: extraction runs
on the **consolidation lane**, driven by `provider.consolidate()`, and nothing on the read path
calls it. Wiring it is one `relation_pipeline` option; leave it out and `consolidate` reports
`relations` as not extracted rather than looking like it found none.

What lands is a **typed edge between two entity nodes** -- `(source)-[relation]->(target)` -- not a
resolved fact: two spellings of one entity address two different endpoint nodes here, same as they
address two different nodes in `17_`, and merging them is SM-G8's (#106).
"""

import os

from egpt.integrations.knowledge.embedder.sentence_transformer import (
    SentenceTransformerEmbedder,
)
from egpt.integrations.memory.graph.neo4j import Neo4jGraphSubstrate
from egpt.integrations.models.openai import OpenAIResponses
from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.graph.entities import EntityExtractionPipeline, LLMEntityExtractor
from egpt.memory.graph.relations import LLMRelationExtractor, RelationExtractionPipeline
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope
from egpt.providers.model_provider import ProviderMode

ACME_ADA = Scope(tenant_id="acme", user_id="ada")

# The substrate is built here rather than named by a registry key, because both pipelines and the
# provider must share **one** graph and the provider is what would otherwise construct it.
graph = Neo4jGraphSubstrate(
    uri=os.getenv("NEO4J_URI", "bolt://localhost:7691"),
    username=os.getenv("NEO4J_USERNAME", "neo4j"),
    password=os.getenv("NEO4J_PASSWORD", "smg1testpassword"),
)

# Only the entity pipeline embeds -- see `RelationExtractionPipeline`'s own docstring for why a
# relation edge has nothing to embed.
embedder = SentenceTransformerEmbedder(id="BAAI/bge-base-en-v1.5", dimensions=768)

model = OpenAIResponses(id="gpt-5.5")

entity_pipeline = EntityExtractionPipeline(
    graph=graph,
    extractor=LLMEntityExtractor(model=model),
    embedder=embedder,
)
relation_pipeline = RelationExtractionPipeline(
    graph=graph,
    extractor=LLMRelationExtractor(model=model),
)

# The vector substrate is not optional: `consolidate` enumerates the episodic tier through it.
provider = build_memory_provider(
    MemoryProviderConfig(
        provider="co_located",
        mode=ProviderMode.LOCAL,
        vector={
            "backend": "pgvector",
            "db_url": os.getenv(
                "EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
            ),
            "embedder": embedder,
            # Its own table, matching `17_`'s own note: `consolidate` reads **every** episode in
            # the scope, so sharing a table with another cookbook mixes their writes into this run.
            "table": "memory_vectors_sm_g3",
        },
        options={
            "graph_substrate": graph,
            "entity_pipeline": entity_pipeline,
            "relation_pipeline": relation_pipeline,
        },
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
            content="Sarah Chen reports to the CTO and owns NorthStar's decision on the deal.",
            scope=ACME_ADA,
            id="ada-northstar-reports",
            occurred_at=MARCH + 60,
        ),
    ],
)

print("A read costs no model call -- extraction is not on this path:")
slice_ = provider.read(
    ACME_ADA, MemoryQuery(text="northstar", tiers=[MemoryTier.EPISODIC])
)
print(f"  {len(slice_.records)} episode(s) for acme/ada")

print("\nConsolidating acme/ada -- this is where both extraction passes run:")
report = provider.consolidate(ACME_ADA)
print(f"  episodes scanned: {report.episodic_scanned}")
print(f"  entities: {report.details['entities']}")
print(f"  relations: {report.details['relations']}")


print("\nRelation edges for acme/ada, read through the semantic subgraph (SM-G7 #105):")
# `recall_facts` is the port's read for this shape -- fact edges by entity/relation/valid-time,
# unranked, distinct from `traverse`'s relevance-ranked search over every tier at once. `since=0`
# asks for the whole history rather than narrowing by entity or relation.
for edge in graph.recall_facts(ACME_ADA, since=0):
    episodes = edge.provenance["source_episode_ids"]
    print(
        f"  {edge.provenance['subject']} --[{edge.provenance['predicate']}]--> "
        f"{edge.provenance['object']}  from={episodes}"
    )

print(
    "\nWhat SM-G8 (#106) inherits: edges whose endpoints a human would still merge across a corpus,"
)
print("each addressed by the entity's own surface name.")

provider.close()
