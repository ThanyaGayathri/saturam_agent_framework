"""
The CoALA Tier Model
====================

This example shows the four memory tiers (SM-C2) -- working, episodic, semantic,
procedural -- and what the framework decides *because of* a record's tier.

Key concepts:
- One table, egpt.memory.TIER_POLICY, holds every per-tier decision: which
  substrates a write reaches, how a graph write is shaped, which tier a read
  searches by default, and which tier consolidation reads and writes.
- Writes route by tier. A procedural rule reaches the vector substrate only; an
  episode reaches both, as an episode; a semantic fact reaches both, as a fact.
- Reads select tiers. A query naming no tiers searches every persistent one, and
  a leg no requested tier routes to is not consulted at all.
- provider.tiers() says which tiers a deployment actually serves, because a read
  of an unserved tier is empty rather than refused.
- The seed's six learn stores map into the tiers by ADR 0003 section 2.2, and
  each store's read predicate is declared, so a scope it cannot honour is
  refused rather than answered wider.

The toy substrates are the same shape as 09_memory_provider_seam.py's, kept
small here because the subject is the tier axis rather than the seam.
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence

from egpt.memory import (
    TIER_POLICY,
    FactWrite,
    MemoryEvent,
    MemoryProviderConfig,
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    Scope,
    Substrate,
    TemporalBounds,
    build_memory_provider,
    policy_for_tier,
)
from egpt.memory.learn_stores import RECALL_SLOTS, STORE_TIERS

# ---------------------------------------------------------------------------
# Two toy substrates, recording what they were asked
# ---------------------------------------------------------------------------


class InMemoryVector:
    def __init__(self) -> None:
        self.rows: Dict[str, MemoryRecord] = {}
        self.calls: List[str] = []

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        self.calls.append("upsert")
        for record in records:
            self.rows[record.id] = replace(record, substrate=Substrate.VECTOR)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        self.upsert(scope, records)

    def search(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        self.calls.append("search")
        wanted = {tier} if tier else set(query.resolved_tiers())
        hits = [
            replace(record, score=1.0)
            for record in self.rows.values()
            if record.tier in wanted and query.text.split()[0] in record.content
        ]
        return hits[: query.limit] if query.limit else []

    async def asearch(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        return self.search(scope, query, tier)

    def scan(
        self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None
    ) -> List[MemoryRecord]:
        self.calls.append("scan")
        rows = [replace(r) for r in self.rows.values() if r.tier is tier]
        return rows if limit is None else rows[:limit]

    async def ascan(
        self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None
    ) -> List[MemoryRecord]:
        return self.scan(scope, tier, limit)


class InMemoryGraph:
    def __init__(self) -> None:
        self.episodes: List[MemoryEvent] = []
        self.facts: List[FactWrite] = []
        self.calls: List[str] = []

    def upsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        self.calls.append("upsert_episode")
        self.episodes.append(event)

    async def aupsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        self.upsert_episode(scope, event)

    def upsert_fact(self, scope: Scope, fact: FactWrite) -> None:
        self.calls.append("upsert_fact")
        self.facts.append(fact)

    async def aupsert_fact(self, scope: Scope, fact: FactWrite) -> None:
        self.upsert_fact(scope, fact)

    def traverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        self.calls.append("traverse")
        return []

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        return self.traverse(scope, query, tier)

    def bounds_for_ids(
        self, scope: Scope, ids: Sequence[str]
    ) -> Dict[str, TemporalBounds]:
        self.calls.append("bounds_for_ids")
        return {}

    async def abounds_for_ids(
        self, scope: Scope, ids: Sequence[str]
    ) -> Dict[str, TemporalBounds]:
        return self.bounds_for_ids(scope, ids)

    def invalidate(
        self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None
    ) -> None:
        self.calls.append("invalidate")

    async def ainvalidate(
        self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None
    ) -> None:
        self.invalidate(scope, edge_id, t_invalid, at)


def provider_and_substrates():
    vector, graph = InMemoryVector(), InMemoryGraph()
    config = MemoryProviderConfig(
        provider="co_located",
        options={"vector_substrate": vector, "graph_substrate": graph},
    )
    return build_memory_provider(config), vector, graph


if __name__ == "__main__":
    scope = Scope(tenant_id="acme", user_id="ada@example.com")

    # -----------------------------------------------------------------
    # The table: every per-tier decision in one place
    # -----------------------------------------------------------------
    print("Tier policy:")
    for tier, policy in TIER_POLICY.items():
        substrates = ", ".join(sorted(policy.substrates)) or "none (in-context)"
        shape = policy.graph_shape.value if policy.graph_shape else "-"
        print(
            f"  {tier.value:<11} substrates={substrates:<15} graph write={shape:<8} "
            f"searched by default={policy.searched_by_default}"
        )

    # -----------------------------------------------------------------
    # Writes route by tier
    # -----------------------------------------------------------------
    provider, vector, graph = provider_and_substrates()

    provider.write(scope, [MemoryEvent(content="ada asked about pricing", scope=scope)])
    provider.write_tier(
        scope,
        MemoryTier.SEMANTIC,
        [MemoryEvent(content="ada is on the enterprise plan", scope=scope)],
    )
    provider.write_tier(
        scope,
        MemoryTier.PROCEDURAL,
        [MemoryEvent(content="always confirm before refunding", scope=scope)],
    )

    print()
    print("Write routing:")
    # An event naming no tier is episodic -- writes are cheap and non-lossy, and
    # distilling them into facts is consolidation's job, not the request path's.
    print(
        "  an unnamed write lands in:", MemoryEvent(content="x", scope=scope).tier.value
    )
    print("  graph episodes:", [e.content for e in graph.episodes])
    print("  graph facts:   ", [f.record.content for f in graph.facts])
    # Procedural memory is vector-only, so the rule above is in the vector
    # substrate and in neither graph list.
    print("  vector rows:   ", len(vector.rows))

    # -----------------------------------------------------------------
    # Reads select tiers -- and skip a leg no requested tier routes to
    # -----------------------------------------------------------------
    print()
    print("Read tier-selection:")
    default_read = provider.read(scope, MemoryQuery(text="ada"))
    print(
        "  a query naming no tiers searched:",
        [t.value for t in default_read.query.resolved_tiers()],
    )
    print(
        "  ...working is excluded: it has no persistent substrate, and building it out is",
        policy_for_tier(MemoryTier.WORKING).owner,
    )

    graph.calls.clear()
    provider.read(scope, MemoryQuery(text="always", tiers=[MemoryTier.PROCEDURAL]))
    print("  a procedural read consulted the graph:", bool(graph.calls))

    graph.calls.clear()
    provider.read(scope, MemoryQuery(text="ada", tiers=[MemoryTier.SEMANTIC]))
    print("  a semantic read consulted the graph:  ", bool(graph.calls))

    # -----------------------------------------------------------------
    # Which tiers a deployment serves
    # -----------------------------------------------------------------
    # A graph-only deployment cannot serve procedural memory: the table routes it
    # to the vector substrate alone. Which tiers a tenant gets is SM-C5's (#93);
    # this is the same question answered by the wiring.
    graph_only = build_memory_provider(
        MemoryProviderConfig(
            provider="co_located", options={"graph_substrate": InMemoryGraph()}
        )
    )
    print()
    print("Which tiers a provider serves:")
    print(
        "  both substrates:",
        [t.value for t, on in provider.tiers().items() if on],
    )
    print(
        "  graph only:     ",
        [t.value for t, on in graph_only.tiers().items() if on],
    )
    # A read of an unserved tier is empty rather than refused, so tiers() is how
    # a caller tells "nothing stored" from "not served here". A write to one is
    # refused, because writing where nothing will store it loses data.
    print(
        "  reading procedural on the graph-only provider:",
        graph_only.read_tier(scope, MemoryTier.PROCEDURAL, MemoryQuery(text="always")),
    )

    # -----------------------------------------------------------------
    # The seed's six learn stores, mapped in
    # -----------------------------------------------------------------
    print()
    print("The six learn stores (ADR 0003 section 2.2):")
    for store_type, tier in STORE_TIERS.items():
        honoured = ", ".join(sorted(RECALL_SLOTS[store_type])) or "none by default"
        print(f"  {store_type:<19} -> {tier.value:<11} filters on: {honoured}")
    print(
        "  no store maps to episodic: the seed has no episodic memory, which is",
        policy_for_tier(MemoryTier.EPISODIC).owner,
    )
