"""Mode A: co-located memory, end to end (SM-X1 #137).

`13_vector_substrate_pgvector.py` and `10_graph_substrate_neo4j.py` each talk to one real backend.
Neither runs both at once, and neither drives the write -> consolidate -> read pass that is this
design's differentiator (research doc section 5.3): raw episodes go in cheap and non-lossy, and the
async/sleep-time lane is what turns them into semantic facts on *both* substrates.

This file is that pass, over real infrastructure, with the mode this ADR calls Mode A:

    ./cookbook/scripts/run_pgvector.sh
    ./cookbook/scripts/run_neo4j.sh

**What "Mode A" means here, precisely.** `mode=ProviderMode.LOCAL` does two things at once, from one
config field: it selects the offline-capable embedder (`16_memory_embedding_pipeline.py`), and it is
what SM-C5 (#93) will read per tenant to route a deployment to this adapter instead of an external
one. Nothing below names a model or branches on mode -- `build_memory_provider` is still the only
construction call site.

**What this deliberately does not do.** Consolidation here is the deterministic default (`_distil`):
group near-duplicate episodes, keep one, write it as a fact. Real extraction -- entities, relations,
contradiction resolution -- is SM-G2/SM-G3/SM-L3's, each with its own cookbook (`17_`, `19_`) and
each needing an `OPENAI_API_KEY` this file does not. Per-tenant Mode A/B selection is SM-C5's; this
file always builds the one config, the way `09_memory_provider_seam.py` does. Mode B's own
real-backend walkthrough is SM-X2's (#138) -- there is no external adapter to show yet.
"""

from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope
from egpt.providers.model_provider import ProviderMode

# A scope namespaced to this file, not shared with `09_`/`10_`/`13_`'s `acme`/`globex`: this is the
# only cookbook driving pgvector and Neo4j together, and a shared tenant name would pick up rows
# `test_mode_a.py`'s integration suite left on the same Neo4j instance -- Community has no
# database-per-tenant, so cross-file isolation is by scope name, not by engine.
SCOPE = Scope(tenant_id="mode-a-demo", user_id="ada")

config = MemoryProviderConfig(
    provider="co_located",
    mode=ProviderMode.LOCAL,  # Mode A: co-located, in-VPC, air-gapped embedder.
    vector={
        "backend": "pgvector",
        "db_url": "postgresql+psycopg://ai:ai@localhost:5532/ai",
        "table": "mode_a_demo_vectors",
        # No embedder is named: `mode` selects the Mode A default (bge-base-en-v1.5 @768), the
        # same reasoning `13_` states in full.
    },
    graph={
        "backend": "neo4j",
        "uri": "bolt://localhost:7691",
        "username": "neo4j",
        "password": "smg1testpassword",
    },
)

provider = build_memory_provider(config)

print("Mode A binding")
print("=" * 40)
print(f"mode: {provider.config.mode.value}")
print(
    f"substrates wired: {[s.value for s, wired in provider.substrates().items() if wired]}"
)

# Two episodes, identical content, reported by two different sessions: `_distil`'s deterministic
# grouping folds them into one fact rather than two, which is the smallest case that exercises the
# merge at all -- real extraction's dedup (SM-L3 #124) is a separate, LLM-backed strategy.
provider.write(
    SCOPE,
    [
        MemoryEvent(
            id="e1", content="the deploy pipeline moved to owned compute", scope=SCOPE
        ),
        MemoryEvent(
            id="e2", content="the deploy pipeline moved to owned compute", scope=SCOPE
        ),
    ],
)

print()
print("Write (episodic, cheap and non-lossy)")
print("=" * 40)
episodes = provider.read(
    SCOPE, MemoryQuery(text="deploy pipeline", tiers=[MemoryTier.EPISODIC])
)
print(f"episodic rows: {len(episodes.records)}")

report = provider.consolidate(SCOPE)

print()
print("Consolidate (episodic -> semantic, the sleep-time lane)")
print("=" * 40)
print(f"episodic_scanned: {report.episodic_scanned}")
print(f"duplicates_merged: {report.duplicates_merged}")
print(f"semantic_written: {report.semantic_written}")

facts = provider.read(
    SCOPE,
    MemoryQuery(text="deploy pipeline owned compute", tiers=[MemoryTier.SEMANTIC]),
)

print()
print("Read (fused across both real substrates)")
print("=" * 40)
print(f"fusion: {facts.fusion}")
for record in facts.records:
    print(f"  [{record.tier.value}] {record.content!r} (score={record.score:.3f})")
