"""The temporal knowledge graph behind the memory seam (SM-G1 #99).

Unlike `09_memory_provider_seam.py`, which uses in-file reference substrates and needs no
infrastructure, this one talks to a real Neo4j:

    ./cookbook/scripts/run_neo4j.sh

What it shows is that choosing a graph backend is a **config** edit. No call site names Neo4j, a
Bolt URI, or the driver: `config.graph` carries a registry key, `build_memory_provider` resolves it,
and the consumer below would read identically against any other registered graph backend.

The engine is SM-R2's decision -- raw Neo4j with our own bi-temporal layer, because the Graphiti
library's temporal write path measured 19/30 on the point-in-time probes our layer scores 30/30 on.
"""

import os

from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope
from egpt.providers.model_provider import ProviderMode

# Two tenants and two users inside one of them. Isolation on Neo4j Community is a property filter on
# every statement -- Community denies CREATE DATABASE, so database-per-tenant needs Enterprise --
# which makes the second pair the interesting one: a tenant filter alone would not separate them.
ACME_ADA = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")
GLOBEX = Scope(tenant_id="globex", user_id="ada")

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

# `occurred_at` is valid time -- when it happened, not when it was recorded. Set explicitly here so
# the point-in-time reads below have a fixed timeline to answer against.
MARCH = 1_600_000_000
JUNE = 1_700_000_000

provider.write(
    ACME_ADA,
    [
        MemoryEvent(
            content="ada moved to berlin in march", scope=ACME_ADA, occurred_at=MARCH
        )
    ],
)
provider.write(
    ACME_BOB,
    [
        MemoryEvent(
            content="bob prefers the lisbon office", scope=ACME_BOB, occurred_at=MARCH
        )
    ],
)
provider.write(
    GLOBEX,
    [
        MemoryEvent(
            content="ada moved to berlin in march", scope=GLOBEX, occurred_at=MARCH
        )
    ],
)

print("Substrates wired:")
for substrate, wired in provider.substrates().items():
    print(f"  {substrate.value}: {wired}")

print("\nWhat each scope can read for 'ada moved berlin':")
query = MemoryQuery(text="ada moved berlin", tiers=[MemoryTier.EPISODIC])
for label, scope in (
    ("acme/ada", ACME_ADA),
    ("acme/bob", ACME_BOB),
    ("globex/ada", GLOBEX),
):
    records = provider.read(scope, query).records
    print(f"  {label}: {len(records)} record(s)")

print("\nContext for acme/ada:")
print(provider.read(ACME_ADA, query).as_context())

# Bi-temporal invalidation applies to a **semantic fact**, not an episode. ADR 0003 section B.6
# gives the two different lifecycles: a contradiction on a graph edge resolves by expire-and-reinsert
# here, while an episodic row is retired by its own lifecycle writing `expired_at`. The substrate
# refuses an episode id rather than obliging, so the boundary is enforced and not just documented.
provider.write_tier(
    ACME_ADA,
    MemoryTier.SEMANTIC,
    [MemoryEvent(content="ada lives in berlin", scope=ACME_ADA, occurred_at=MARCH)],
)

fact_query = MemoryQuery(text="ada lives berlin", tiers=[MemoryTier.SEMANTIC])
fact_id = provider.read(ACME_ADA, fact_query).records[0].id

graph = provider.graph
graph.invalidate(ACME_ADA, fact_id, t_invalid=JUNE, at=JUNE + 100)

bounds = graph.bounds_for_ids(ACME_ADA, [fact_id])[fact_id]
print("\nAfter invalidating the fact:")
print(f"  t_invalid: {bounds.t_invalid}")
print(f"  still current belief: {bounds.expired_at is None}")

print("\nRead as of march, before it became false:")
before = provider.read(
    ACME_ADA,
    MemoryQuery(text="ada lives berlin", as_of=MARCH + 1, tiers=[MemoryTier.SEMANTIC]),
)
print(f"  {len(before.records)} record(s)")

print("\nRead as of june, after it became false:")
after = provider.read(
    ACME_ADA,
    MemoryQuery(text="ada lives berlin", as_of=JUNE + 1, tiers=[MemoryTier.SEMANTIC]),
)
print(f"  {len(after.records)} record(s)")

# Released through the seam, not by reaching into `provider.graph`: a caller that builds a
# provider per request should not have to know which substrate holds a connection pool.
provider.close()
