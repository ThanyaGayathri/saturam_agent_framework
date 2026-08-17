"""The pgvector memory vector store behind the memory seam (SM-V1 #95).

Like `10_graph_substrate_neo4j.py`, and unlike `09_memory_provider_seam.py`, this one talks to real
infrastructure:

    ./cookbook/scripts/run_pgvector.sh

What it shows is that choosing a vector backend is a **config** edit. No call site names pgvector, a
DSN, or SQLAlchemy: `config.vector` carries a registry key, `build_memory_provider` resolves it, and
the consumer below would read identically against any other registered vector store.

The store is SM-R3's decision and the **layout** is the part that matters: one partition and one
HNSW index per tenant. A shared table with a `tenant_id` filter measured recall 0.22 at 50 tenants,
because it filters a shared index whose traversal rarely lands enough in-tenant neighbours. Nothing
below configures that -- it is what the backend builds.

The embedder is separate from the store: which model, at which dimension, is SM-V2's (#96), and
`mode` below is what selects it. Mode A gets the offline-capable model ADR 0003 section A.3
ratifies, without this file naming it.
"""

import os

from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope
from egpt.providers.model_provider import ProviderMode

# Two tenants and two users inside one of them. The second pair is the interesting one: a tenant
# predicate alone would not separate ada from bob, and the port requires every set scope slot to be
# applied -- a backend that honours the tenant and ignores the user serves one user's memory to
# another inside the same tenant.
ACME_ADA = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")
GLOBEX = Scope(tenant_id="globex", user_id="ada")

config = MemoryProviderConfig(
    provider="co_located",
    mode=ProviderMode.LOCAL,
    vector={
        "backend": "pgvector",
        "db_url": os.getenv(
            "EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
        ),
        # No embedder is named: `mode` selects it. SM-V2 (#96) made bge-base-en-v1.5 at 768 the
        # Mode A default, so stating it here would be a second copy of a value that lives in
        # `egpt/memory/embedding.py` -- and the copy is what drifts when the model changes.
        # `14_memory_embedding_pipeline.py` shows the selection itself.
        # The measured default, stated here because it is a per-deployment parameter rather than
        # a constant: 256 reaches recall@10 0.967 at 100k/tenant for a p95 of 7 ms, where the
        # benchmark's old constant of 64 reached 0.889. See benchmarks/memory_vectordb/EF_SWEEP.md.
        "ef_search": 256,
    },
)

provider = build_memory_provider(config)

MARCH = 1_600_000_000

# Ids are stated so re-running this file replaces the same three rows rather than accumulating a
# fresh set: the substrate upserts by id, and a provider mints one per write when you leave it out.
# `ada-berlin` appearing under two tenants is deliberate, not a copy-paste slip: the primary key
# is (tenant_id, id), so an id is only unique *within* a tenant and these are two distinct rows.
provider.write(
    ACME_ADA,
    [
        MemoryEvent(
            id="ada-berlin",
            content="ada moved to berlin in march",
            scope=ACME_ADA,
            occurred_at=MARCH,
        )
    ],
)
provider.write(
    ACME_BOB,
    [
        MemoryEvent(
            id="bob-lisbon",
            content="bob prefers the lisbon office",
            scope=ACME_BOB,
            occurred_at=MARCH,
        )
    ],
)
provider.write(
    GLOBEX,
    [
        MemoryEvent(
            id="ada-berlin",
            content="ada moved to berlin in march",
            scope=GLOBEX,
            occurred_at=MARCH,
        )
    ],
)

print("Substrates wired:")
for substrate, wired in provider.substrates().items():
    print(f"  {substrate.value}: {wired}")

# Semantic recall, not a keyword match: nothing below shares a word with the stored memory.
query = MemoryQuery(text="which city did ada relocate to", tiers=[MemoryTier.EPISODIC])

print("\nWhat each scope can read for a question about ada's relocation:")
# Each scope reads only its own rows: acme/bob gets bob's memory and never ada's, though the two
# sit in one tenant and only the user slot separates them.
for label, scope in (
    ("acme/ada", ACME_ADA),
    ("acme/bob", ACME_BOB),
    ("globex/ada", GLOBEX),
):
    records = provider.read(scope, query).records
    print(f"  {label}: {[record.content for record in records]}")

print("\nContext for acme/ada:")
print(provider.read(ACME_ADA, query).as_context())

slice_ = provider.read(ACME_ADA, query)
if slice_.records:
    top = slice_.records[0]
    # A similarity in [0.0, 1.0], higher is better -- the port's contract, and load-bearing because
    # the provider adds the legs' scores. pgvector's own operator returns a cosine *distance*.
    print(f"\nTop record score: {top.score:.3f} (a similarity, not a distance)")

# The graph leg is absent here, so a point-in-time read is **refused** rather than answered from a
# projection that carries no authoritative bounds. Wiring `config.graph` as well is what turns this
# into the neuro-symbolic pair ADR 0003 D8 describes.
try:
    provider.read(ACME_ADA, MemoryQuery(text="where does ada live", as_of=MARCH + 1))
except Exception as error:
    print(
        f"\nPoint-in-time read without a graph leg: refused -- {type(error).__name__}"
    )

# Released through the seam: a caller that builds a provider per request should not have to know
# which substrate holds a connection pool.
provider.close()
