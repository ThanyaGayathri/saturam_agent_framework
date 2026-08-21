"""Fact extraction + merge decision: ADD / UPDATE / DELETE / NOOP (SM-L3 #124).

Needs Neo4j, Postgres + pgvector, and an OpenAI key -- extraction and the merge decision are each
a model pass:

    ./cookbook/scripts/run_neo4j.sh
    ./cookbook/scripts/run_pgvector.sh

What it shows is the seam `_distil` (SM-C1) leaves open: `_distil`'s deterministic dedup groups by
content overlap and can never retire what it subsumes -- a group whose membership changes mints a
new fact id and the old row stays "current belief" forever. `FactExtractionPipeline` replaces that
strategy, opt-in via `fact_pipeline`, the same shape as `entity_pipeline`/`relation_pipeline`: it
extracts atomic candidate facts, compares each against the top-k most similar *existing* facts, and
lets the model choose ADD / UPDATE / DELETE / NOOP.

The second `consolidate()` call below is the point of the file: ada's city changes, and the pass
retires the Berlin fact (`expired_at` + `superseded_by`, transaction time, not valid time -- the
old row did not become false, a better-sourced row replaced it) rather than leaving two "current"
facts that both read back as true.
"""

import os

from egpt.integrations.knowledge.embedder.sentence_transformer import (
    SentenceTransformerEmbedder,
)
from egpt.integrations.memory.graph.neo4j import Neo4jGraphSubstrate
from egpt.integrations.memory.vector.pgvector.pgvector import PgVectorMemorySubstrate
from egpt.integrations.models.openai import OpenAIResponses
from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.consolidation.extract import (
    FactExtractionPipeline,
    LLMFactExtractor,
    LLMMergeDecider,
)
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope
from egpt.providers.model_provider import ProviderMode

ACME_ADA = Scope(tenant_id="acme", user_id="ada")

graph = Neo4jGraphSubstrate(
    uri=os.getenv("NEO4J_URI", "bolt://localhost:7691"),
    username=os.getenv("NEO4J_USERNAME", "neo4j"),
    password=os.getenv("NEO4J_PASSWORD", "smg1testpassword"),
)

embedder = SentenceTransformerEmbedder(id="BAAI/bge-base-en-v1.5", dimensions=768)

# Built here, like `graph` above, rather than left to `build_memory_provider` to construct from
# `vector={...}`: the pipeline's top-k lookup and the provider's own reads/writes must be the same
# table, and the provider is what would otherwise build it. Both are handed this one object through
# `options`, the same injection path `graph_substrate` uses.
vector = PgVectorMemorySubstrate(
    db_url=os.getenv(
        "EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
    ),
    embedder=embedder,
    # Its own table: `consolidate` reads every episode in the scope, so sharing a table with
    # another cookbook would have this file extract facts from that one's memories.
    table="memory_vectors_sm_l3",
)

model = OpenAIResponses(id="gpt-5.5")

# One model, two roles: extraction reads episodes and proposes candidates, the merge decider reads
# one candidate plus its nearest existing facts and picks ADD/UPDATE/DELETE/NOOP. Separate classes
# because a caller wiring only one (a rules-based decider over an LLM extractor, say) should not
# have to satisfy the other's construction requirements too.
pipeline = FactExtractionPipeline(
    vector=vector,
    extractor=LLMFactExtractor(model=model),
    decider=LLMMergeDecider(model=model),
)

provider = build_memory_provider(
    MemoryProviderConfig(
        provider="co_located",
        mode=ProviderMode.LOCAL,
        options={
            "vector_substrate": vector,
            "graph_substrate": graph,
            "fact_pipeline": pipeline,
        },
    )
)

MARCH = 1_600_000_000

# Ids are stated so a second run replaces these rows rather than accumulating a fresh set.
provider.write(
    ACME_ADA,
    [
        MemoryEvent(
            content="Ada told the team she lives in Berlin and prefers async standups.",
            scope=ACME_ADA,
            id="ada-berlin",
            occurred_at=MARCH,
        )
    ],
)

print("First pass -- nothing to compare against yet, so this should ADD:")
report = provider.consolidate(ACME_ADA)
print(f"  episodes scanned: {report.episodic_scanned}")
print(f"  semantic written: {report.semantic_written}")
print(f"  derived_by: {report.details['derived_by']}")


def semantic_facts(scope: Scope) -> list[str]:
    """Current belief only -- a plain read never returns a retired row (`is_current_belief()`),
    which is exactly what this demonstrates: after pass 2, the Berlin fact is gone from here, not
    sitting beside a second, contradictory Lisbon one."""
    slice_ = provider.read(
        scope, MemoryQuery(text="ada", tiers=[MemoryTier.SEMANTIC], limit=10)
    )
    return [r.content for r in slice_.records]


print("\nSemantic facts after pass 1:")
for content in semantic_facts(ACME_ADA):
    print(f"  {content}")

# A changed circumstance -- the second pass should UPDATE the Berlin fact rather than leave a
# second, contradictory one standing beside it.
provider.write(
    ACME_ADA,
    [
        MemoryEvent(
            content="Ada moved to Lisbon last month; she still prefers async standups.",
            scope=ACME_ADA,
            id="ada-lisbon",
            occurred_at=MARCH + 3600,
        )
    ],
)

print(
    "\nSecond pass -- the city changed, so this should UPDATE and retire the Berlin fact:"
)
report = provider.consolidate(ACME_ADA)
print(f"  derived_by: {report.details['derived_by']}")
print(f"  supersede: {report.details['supersede']}")

print(
    "\nSemantic facts after pass 2 (retired Berlin row is gone from here, kept in Neo4j):"
)
for content in semantic_facts(ACME_ADA):
    print(f"  {content}")

print(
    "\nWhat this pipeline does not do: contradiction resolution between two already-stored facts "
    "(SM-L5 #126) -- UPDATE/DELETE here only ever compare a fresh candidate against its own top-k "
    "neighbours."
)

provider.close()
