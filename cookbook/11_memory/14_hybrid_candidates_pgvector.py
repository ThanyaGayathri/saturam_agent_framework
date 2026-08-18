"""Vector and BM25-style lexical candidates from one memory store (SM-V3 #97).

Talks to real infrastructure, like `13_vector_substrate_pgvector.py`:

    ./cookbook/scripts/run_pgvector.sh

What it shows is **why the two candidate lists are kept apart**. The store ranks the same query two
ways -- dense cosine over embeddings, and Postgres full-text over the content column -- and hands
back both rankings rather than one blended number. Run it and watch the three queries below: the
legs return different lengths, different orders, and on the last one a different top hit.

That disagreement is the point. `PgVector.hybrid_search` blends the two in SQL and returns a single
`similarity_score`, from which neither the per-leg ranks nor a different weighting can be recovered
-- a caller wanting either has to run both searches again.

A note against the easy story, because the output contradicts it: this does **not** show that dense
retrieval cannot find identifiers. On the ADR-ratified embedder below, the vector leg ranks the
record carrying `ORD-99417-XZ` first as well. What the legs actually differ on is *shape* --
full-text answers with the one record that matches and nothing else, dense ranks the entire corpus
by proximity, and neither ordering is recoverable from the other.

**Nothing here fuses the lists**, and that is deliberate rather than unfinished. ADR 0003 section
A.6 ratifies weighted / relative-score fusion for this two-leg case and holds the three-leg
(+ graph) choice open, gated on SM-G9 (#107); implementing either is SM-H2's (#132), and running
the legs in parallel behind `provider.read` is SM-H1's (#131). Until then `read` is the vector leg
alone and this file reaches the substrate directly.
"""

import os

from egpt.integrations.knowledge.embedder.sentence_transformer import (
    SentenceTransformerEmbedder,
)
from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.substrates import HybridSearchSubstrate
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope
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
        "embedder": SentenceTransformerEmbedder(
            id="BAAI/bge-base-en-v1.5", dimensions=768
        ),
        # The language the lexical leg stems and indexes with. It is per-deployment because the
        # right answer is the corpus' language, and it is baked into the GIN index expression --
        # changing it later warns at startup that the index no longer matches, rather than silently
        # dropping every lexical read to a sequential scan.
        "text_search_config": "english",
        # Its own table, so re-running this file shows only its own rows rather than whatever
        # `13_vector_substrate_pgvector.py` left in the default one.
        "table": "memory_hybrid_demo",
    },
)

provider = build_memory_provider(config)

# The store is reached directly because fusing the legs behind `read` is SM-H1/SM-H2's work. A
# vector store that cannot rank lexically is a legitimate backend, so the capability is an optional
# port an orchestrator feature-detects rather than something every substrate must implement.
store = provider.vector
print("Store offers both legs:", isinstance(store, HybridSearchSubstrate))

MARCH = 1_600_000_000
IDENTIFIER = "ORD-99417-XZ"

# Ids are stated so re-running this file replaces the same rows rather than accumulating a fresh
# set: the substrate upserts by id. `decoy` repeats the ordinary words of the queries below without
# carrying the identifier, which is what makes the two legs rank it differently.
provider.write(
    ACME_ADA,
    [
        MemoryEvent(
            id="carrier",
            content=f"Order {IDENTIFIER} status confirmed, shipped from the Rotterdam depot",
            scope=ACME_ADA,
            occurred_at=MARCH,
        ),
        MemoryEvent(
            id="decoy",
            content="Order status page shows every order status update for each order",
            scope=ACME_ADA,
            occurred_at=MARCH,
        ),
        MemoryEvent(
            id="commute",
            content="ada takes the tram to the office most mornings",
            scope=ACME_ADA,
            occurred_at=MARCH,
        ),
    ],
)


def show(title, query_text):
    candidates = store.search_candidates(
        ACME_ADA, MemoryQuery(text=query_text, limit=5)
    )
    print(f"\n{title}")
    print(f"  query: {query_text}")
    for leg, records in candidates.legs().items():
        ranked = [f"{record.id}({record.score:.3f})" for record in records]
        print(f"  {leg:<8}: {ranked or 'no candidates'}")


# Precision against proximity. The identifier is a rare token, so full-text returns the one record
# that carries it and stops; the dense leg ranks the whole corpus, correctly putting that record
# first and then offering others that are merely nearby. Both are useful and they are not the same
# answer -- a fusion stage wants to see which record *only one* leg nominated.
show(
    "Exact identifier -- one leg answers, the other ranks everything",
    f"order {IDENTIFIER} status",
)

# The vector leg's case, and the reason a lexical-only store is not enough. Postgres' web search
# syntax is conjunctive: one unmatched term empties the leg entirely, however well the rest fits.
show(
    "Unmatched term -- the lexical leg empties, the vector leg degrades gracefully",
    "tram commute to the berlin office",
)

# The clearest case: both legs answer and they **disagree about the top hit**. This is what a
# blended score destroys -- one number cannot say that the lexical leg's first choice was the dense
# leg's second. Each list is bounded by `limit` independently, because a shared budget would let one
# leg's hits displace the other's before anything had compared them.
show("Ordinary query -- two legs, one corpus, different top hit", "order status")

print(
    "\nThe lists are returned unfused on purpose: rank-based fusion cannot be recovered from a\n"
    "blended score, and which method to use is ADR 0003 section A.6 / SM-H2 (#132)."
)

provider.close()
