"""
The MemoryProvider Seam
=======================

This example shows the Symbiotic Memory seam (SM-C1): one interface -- read,
write, consolidate -- over two substrates, where which backend and which
deployment mode are running is a configuration choice, not a code change.

Key concepts:
- A caller depends only on MemoryProvider, never on a backend.
- build_memory_provider(config) is the single construction call site, so
  swapping Mode A (co-located) for Mode B (external) edits config alone.
- Scope carries tenant, user, and agent through every call. tenant_id is
  required, because an unscoped memory call reads every tenant's rows.
- Writes are cheap and non-lossy; distilling episodes into semantic facts
  happens later, on the consolidation lane.

The two substrates below are tiny in-memory stand-ins so the example runs with
no database. Real backends -- pgvector and a temporal knowledge graph -- arrive
with SM-V1 and SM-G1; they satisfy these same two ports, which is exactly why
this file needs no model, no API key, and no network.
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence

from egpt.memory import (
    FactWrite,
    MemoryEvent,
    MemoryProvider,
    MemoryProviderConfig,
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    Scope,
    Substrate,
    build_memory_provider,
)
from egpt.providers.model_provider import ProviderMode

# ---------------------------------------------------------------------------
# Two toy substrates
# ---------------------------------------------------------------------------
# A real VectorSubstrate embeds and searches; a real GraphSubstrate walks a
# temporal knowledge graph. These do neither -- they only need to satisfy the
# ports so the seam has something to compose.


class InMemoryVector:
    """Keyword overlap standing in for embedding similarity."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, MemoryRecord]] = {}

    def _bucket(self, scope: Scope) -> Dict[str, MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, {})

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        # A copy, not the caller's object. Storing the reference made this substrate and the graph
        # below share one row, so graph.invalidate() also stamped expired_at on the "vector" copy
        # and the bi-temporal demo worked partly for the wrong reason.
        for record in records:
            self._bucket(scope)[record.id] = replace(record, substrate=Substrate.VECTOR)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        self.upsert(scope, records)

    def search(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        wanted = {tier} if tier else set(query.resolved_tiers())
        terms = set(query.text.lower().split())
        hits = []
        for record in self._bucket(scope).values():
            if record.tier not in wanted:
                continue
            overlap = len(terms & set(record.content.lower().split()))
            if overlap:
                hits.append(MemoryRecord(**{**vars(record), "score": float(overlap)}))
        # Honour limit, and treat 0 as zero rows rather than "unlimited" -- every real backend
        # distinguishes LIMIT 0 from an omitted limit, and a permissive double hides a provider
        # that fetches everything when it asked for nothing.
        ranked = sorted(hits, key=lambda r: -(r.score or 0.0))
        return ranked[: query.limit] if query.limit else []

    async def asearch(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        return self.search(scope, query, tier)

    def scan(
        self,
        scope: Scope,
        tier: MemoryTier,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        # The port's contract, not a shortcut: ascending created_at then id, `since` inclusive,
        # `limit` respected. Reference code gets copied, so it should model the contract it teaches.
        rows = [
            record for record in self._bucket(scope).values() if record.tier is tier
        ]
        if since is not None:
            rows = [record for record in rows if (record.created_at or 0) >= since]
        rows.sort(key=lambda record: ((record.created_at or 0), record.id))
        return rows if limit is None else rows[:limit]

    async def ascan(
        self,
        scope: Scope,
        tier: MemoryTier,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        return self.scan(scope, tier, since, limit)


class InMemoryGraph:
    """Rows accumulate. An invalidation never deletes and never edits in place."""

    def __init__(self) -> None:
        self.rows: Dict[str, List[MemoryRecord]] = {}

    def _bucket(self, scope: Scope) -> List[MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, [])

    def upsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        self._bucket(scope).append(
            MemoryRecord(
                id=event.id or "",
                tier=event.tier,
                content=event.content,
                scope=scope,
                substrate=Substrate.GRAPH,
                t_valid=event.occurred_at,
            )
        )

    async def aupsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        self.upsert_episode(scope, event)

    def upsert_fact(self, scope: Scope, fact: FactWrite) -> None:
        self._bucket(scope).append(replace(fact.record, substrate=Substrate.GRAPH))

    async def aupsert_fact(self, scope: Scope, fact: FactWrite) -> None:
        self.upsert_fact(scope, fact)

    def traverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        wanted = {tier} if tier else set(query.resolved_tiers())
        terms = set(query.text.lower().split())
        hits = [
            row
            for row in self._bucket(scope)
            if row.tier in wanted
            and row.is_current_belief()
            and terms & set(row.content.lower().split())
        ]
        # This double honours limit too. The one that does not is precisely the one that would
        # hide a filter-after-limit bug in the provider above it.
        return hits[: query.limit] if query.limit else []

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        return self.traverse(scope, query, tier)

    def invalidate(
        self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None
    ) -> None:
        recorded_at = at if at is not None else t_invalid
        for row in list(self._bucket(scope)):
            if row.id == edge_id and row.is_current_belief():
                # Transaction time out. The valid-time bound on this row is never edited.
                row.expired_at = recorded_at
                # The tightest bound wins. Overwriting t_invalid outright is the in-place clobber
                # SM-R10 section 6 proved wrong: a later, looser invalidation would widen a bound
                # that had already been narrowed, and belief evolution stops reconstructing.
                tightest = (
                    t_invalid
                    if row.t_invalid is None
                    else min(row.t_invalid, t_invalid)
                )
                bounded = MemoryRecord(
                    **{**vars(row), "t_invalid": tightest, "expired_at": None}
                )
                self._bucket(scope).append(bounded)

    async def ainvalidate(
        self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None
    ) -> None:
        self.invalidate(scope, edge_id, t_invalid, at)


# ---------------------------------------------------------------------------
# Configuration: this is the only place a backend or a mode is named
# ---------------------------------------------------------------------------
# Built per call rather than at import: a config holds *live* substrate objects, so a
# module-level one would carry state between runs and quietly share it between examples.
def mode_a_config() -> MemoryProviderConfig:
    """Mode A: co-located substrates, both in-VPC."""
    return MemoryProviderConfig(
        provider="co_located",
        mode=ProviderMode.LOCAL,
        options={
            "vector_substrate": InMemoryVector(),
            "graph_substrate": InMemoryGraph(),
        },
    )


def mode_b_config() -> MemoryProviderConfig:
    """Mode B: same contract, external services. Nothing below this line changes."""
    return MemoryProviderConfig(
        provider="external",
        mode=ProviderMode.FRONTIER,
        options={
            "vector_substrate": InMemoryVector(),
            "graph_substrate": InMemoryGraph(),
        },
    )


# ---------------------------------------------------------------------------
# A consumer: depends on the seam, never on a backend
# ---------------------------------------------------------------------------
def remember_and_recall(provider: MemoryProvider, scope: Scope) -> None:
    """Write two episodes, consolidate them, then read memory back as prompt context."""
    receipt = provider.write(
        scope,
        [
            MemoryEvent(content="the customer prefers email over phone", scope=scope),
            MemoryEvent(content="the customer is on the enterprise plan", scope=scope),
        ],
    )
    print(
        "  accepted:",
        receipt.accepted,
        "into",
        [tier.value for tier in receipt.tiers_written],
    )

    report = provider.consolidate(scope)
    print(
        "  consolidated:",
        report.episodic_scanned,
        "episodes into",
        report.semantic_written,
        "facts",
    )

    slice_ = provider.read(
        scope, MemoryQuery(text="what does the customer prefer", limit=5)
    )
    print(
        "  fusion:",
        slice_.fusion["method"],
        "over",
        slice_.fusion["vector_hits"],
        "vector hits",
    )
    # A read spans every persistent tier by default, so after consolidating you
    # see both the raw episodes and the facts distilled from them. Episodes stay
    # the source of truth; semantic memory is a derived, rebuildable view.
    print("  context:")
    for line in slice_.as_context().splitlines():
        print("   ", line)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # tenant_id is required. There is no unscoped call, because unscoped is the
    # widest tenancy rather than the narrowest.
    scope = Scope(tenant_id="acme", user_id="ada@example.com", agent_id="support-agent")

    print("Mode A (co-located substrates):")
    remember_and_recall(build_memory_provider(mode_a_config()), scope)

    print("")
    print(
        "Mode B (external services) -- same consumer function, only the config changed:"
    )
    remember_and_recall(build_memory_provider(mode_b_config()), scope)

    # ---------------------------------------------------------------------
    # Point-in-time reads: the graph is the authority for what was true when
    # ---------------------------------------------------------------------
    print("")
    print("Bi-temporal read:")
    provider = build_memory_provider(mode_a_config())
    provider.write_tier(
        scope,
        MemoryTier.SEMANTIC,
        [
            MemoryEvent(
                content="ada works at acme", scope=scope, id="fact-1", occurred_at=100
            )
        ],
    )
    print(
        "  before invalidation:",
        len(provider.read(scope, MemoryQuery(text="ada acme")).records),
        "record(s)",
    )

    # The fact stopped being true at t=300. Nothing is deleted: the old row is
    # expired on the transaction axis and a bounded copy is inserted, so history
    # stays reconstructible.
    provider.graph.invalidate(scope, "fact-1", t_invalid=300, at=310)

    still_true = provider.read(scope, MemoryQuery(text="ada acme", as_of=200)).records
    no_longer = provider.read(scope, MemoryQuery(text="ada acme", as_of=400)).records
    print("  as of t=200:", len(still_true), "record(s) -- it was true then")
    print("  as of t=400:", len(no_longer), "record(s) -- it was not")

    # ---------------------------------------------------------------------
    # Tenant isolation is structural, not a filter a caller can forget
    # ---------------------------------------------------------------------
    other = Scope(tenant_id="globex", user_id="ada@example.com")
    print("")
    print("Tenant isolation:")
    print(
        "  globex sees",
        len(provider.read(other, MemoryQuery(text="ada acme")).records),
        "of acme's records",
    )

    # Which providers config can select. Adapters self-register on import.
    from egpt.memory import registered_providers

    print("")
    print("Registered providers:", ", ".join(registered_providers()))
