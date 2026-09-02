"""
The MemoryProvider Seam
=======================

This example shows the Symbiotic Memory seam (SM-C1): one interface -- read,
write, consolidate -- over two substrates, where which backend and which
deployment mode are running is a configuration choice, not a code change.

Key concepts:
- A caller depends only on MemoryProvider, never on a backend.
- build_memory_provider(config) is the single construction call site, so
  changing provider or mode edits config alone.
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
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from egpt.memory import (
    SCOPE_SLOTS,
    FactWrite,
    MemoryEvent,
    MemoryProvider,
    MemoryProviderConfig,
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    Scope,
    Substrate,
    TemporalBounds,
    build_memory_provider,
    require_scope,
)
from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.graph.episodic import (
    entity_node_id_for,
    normalized_mentions,
    require_recall_filters,
    require_recall_limit,
    require_time_window,
)
from egpt.providers.model_provider import ProviderMode

# ---------------------------------------------------------------------------
# Two toy substrates
# ---------------------------------------------------------------------------
# A real VectorSubstrate embeds and searches; a real GraphSubstrate walks a
# temporal knowledge graph. These do neither -- they only need to satisfy the
# ports so the seam has something to compose.


def scoped(record_scope: Scope, call_scope: Scope) -> bool:
    """Every slot the call scope *sets* must match -- not only the tenant.

    The port requires the full conjunctive predicate. A double that filters on tenant alone returns
    other users' rows inside the same tenant, which the provider's own scope post-condition then
    rejects -- so a tenant-only double does not even work here, let alone teach the contract.
    """
    for slot in SCOPE_SLOTS:
        wanted = getattr(call_scope, slot)
        if wanted is not None and getattr(record_scope, slot) != wanted:
            return False
    return True


class InMemoryVector:
    """Keyword overlap standing in for embedding similarity."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, MemoryRecord]] = {}

    def _bucket(self, scope: Scope) -> Dict[str, MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, {})

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="upsert")
        # A copy, not the caller's object. Storing the reference made this substrate and the graph
        # below share one row, so graph.invalidate() also stamped expired_at on the "vector" copy
        # and the bi-temporal demo worked partly for the wrong reason.
        for record in records:
            self._bucket(scope)[record.id] = replace(record, substrate=Substrate.VECTOR)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="aupsert")
        self.upsert(scope, records)

    def search(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="search")
        wanted = {tier} if tier else set(query.resolved_tiers())
        terms = set(query.text.lower().split())
        hits = []
        for record in self._bucket(scope).values():
            # Current belief only, filtered before ranking -- the port requires it, and applying
            # limit first would let superseded rows consume the whole page.
            if record.tier not in wanted or record.expired_at is not None:
                continue
            if not scoped(record.scope, scope):
                continue
            overlap = len(terms & set(record.content.lower().split()))
            if overlap:
                # `replace`, not `vars()` splatting: the latter shares nested provenance /
                # metadata / topics with the stored row. `MemoryRecord.__post_init__` copies them
                # now, but reference code should not rely on that.
                hits.append(replace(record, score=float(overlap)))
        # Honour limit, and treat 0 as zero rows rather than "unlimited" -- every real backend
        # distinguishes LIMIT 0 from an omitted limit, and a permissive double hides a provider
        # that fetches everything when it asked for nothing.
        ranked = sorted(hits, key=lambda r: -(r.score or 0.0))
        return ranked[: query.limit] if query.limit else []

    async def asearch(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="asearch")
        return self.search(scope, query, tier)

    def scan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="scan")
        # The port's contract, not a shortcut: ascending created_at then id, `limit` respected,
        # `after` an exclusive composite bound (SM-L1's resumption cursor). Reference code gets
        # copied, so it should model the contract it teaches.
        rows = [
            # A copy, like `search` returns: the provider may annotate what it scanned.
            replace(record)
            for record in self._bucket(scope).values()
            if record.tier is tier and scoped(record.scope, scope)
        ]
        rows.sort(key=lambda record: ((record.created_at or 0), record.id))
        if after is not None:
            rows = [r for r in rows if ((r.created_at or 0), r.id) > after]
        return rows if limit is None else rows[:limit]

    async def ascan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[Tuple[int, str]] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        return self.scan(scope, tier, limit, after)

    def get_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="get_record")
        row = self._bucket(scope).get(id)
        return replace(row) if row is not None and scoped(row.scope, scope) else None

    async def aget_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="aget_record")
        return self.get_record(scope, id)


class InMemoryGraph:
    """Rows accumulate. An invalidation never deletes and never edits in place."""

    def __init__(self) -> None:
        self.rows: Dict[str, List[MemoryRecord]] = {}

    def _bucket(self, scope: Scope) -> List[MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, [])

    def upsert_episodes(self, scope: Scope, events: Sequence[MemoryEvent]) -> None:
        require_scope(scope, method="upsert_episodes")
        # Upsert by id, which the port requires. Appending would make a repeat write duplicate the
        # row, and PartialWriteError tells callers to retry with the same ids precisely because
        # that retry is meant to be idempotent.
        for event in events:
            self._upsert(
                scope,
                MemoryRecord(
                    # Never `event.id or ""` -- `types.py` rejects an empty id precisely because it
                    # is the dedup key, and reference code gets copied. The provider always stamps one.
                    id=event.id or str(uuid4()),
                    tier=event.tier,
                    content=event.content,
                    scope=scope,
                    substrate=Substrate.GRAPH,
                    t_valid=event.occurred_at,
                    # The episode's mention links (SM-G6). Replaced with the episode, not unioned
                    # into it -- the real backend clears the MENTIONS edges before relinking.
                    provenance={
                        "entity_ids": [
                            entity_node_id_for(scope, name)
                            for name in normalized_mentions(
                                event.entities, episode_id=event.id or ""
                            )
                        ]
                    },
                ),
            )

    def _upsert(self, scope: Scope, row: MemoryRecord) -> None:
        bucket = self._bucket(scope)
        for index, existing in enumerate(bucket):
            # `existing.scope`, not only `existing.id`: `_bucket` keys on `tenant_id` alone, so two
            # users of one tenant share a bucket, and an id collision between them silently
            # overwrote the first write with the second (round-2 review, PR #90 -- the same defect
            # round 1 fixed in `tests/unit/memory/_fakes.py`, missed here in the sibling double).
            if (
                existing.id == row.id
                and existing.scope == row.scope
                and existing.is_current_belief()
            ):
                bucket[index] = row
                return
        bucket.append(row)

    async def aupsert_episodes(
        self, scope: Scope, events: Sequence[MemoryEvent]
    ) -> None:
        require_scope(scope, method="aupsert_episodes")
        self.upsert_episodes(scope, events)

    def upsert_facts(self, scope: Scope, facts: Sequence[FactWrite]) -> None:
        require_scope(scope, method="upsert_facts")
        for fact in facts:
            self._upsert(scope, replace(fact.record, substrate=Substrate.GRAPH))

    async def aupsert_facts(self, scope: Scope, facts: Sequence[FactWrite]) -> None:
        require_scope(scope, method="aupsert_facts")
        self.upsert_facts(scope, facts)

    def traverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="traverse")
        wanted = {tier} if tier else set(query.resolved_tiers())
        terms = set(query.text.lower().split())
        hits = [
            # Scored, like the vector leg. An unscored leg makes fusion fall back to rank
            # position, which imports whatever order this store happens to hold -- a real graph
            # backend ranks its traversal, and a double that does not hides that in the provider.
            replace(row, score=float(len(terms & set(row.content.lower().split()))))
            for row in self._bucket(scope)
            if row.tier in wanted
            and row.is_current_belief()
            and scoped(row.scope, scope)
            and terms & set(row.content.lower().split())
        ]
        # Ranked *then* limited. Slicing an unranked list is exactly the permissive double this
        # comment claims not to be -- the one that would hide a filter-after-limit bug above it.
        hits.sort(key=lambda r: -(r.score or 0.0))
        return hits[: query.limit] if query.limit else []

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="atraverse")
        return self.traverse(scope, query, tier)

    def recall_episodes(
        self,
        scope: Scope,
        entities: Optional[Sequence[str]] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        # Episodes by valid-time window and mention link, oldest first (SM-G6). The guards are the
        # port's, imported rather than restated, so this double refuses exactly what Neo4j refuses.
        require_scope(scope, method="recall_episodes")
        require_recall_filters(entities, since, until)
        require_time_window(since, until)
        require_recall_limit(limit)
        wanted = None if entities is None else set(entities)
        hits = []
        for row in self._bucket(scope):
            if (
                row.tier is not MemoryTier.EPISODIC
                or not row.is_current_belief()
                or not scoped(row.scope, scope)
            ):
                continue
            if since is not None and (row.t_valid is None or row.t_valid < since):
                continue
            if until is not None and (row.t_valid is None or row.t_valid >= until):
                continue
            if wanted is not None:
                # Built once per row, not once per name in `wanted`: rebuilding it inside the
                # membership check below would be O(len(wanted)) sets for one row instead of one.
                linked = set(row.provenance.get("entity_ids") or [])
                if not any(
                    entity_node_id_for(row.scope, name) in linked for name in wanted
                ):
                    continue
            hits.append(replace(row))
        # Oldest first, with a null valid time last -- the order Neo4j returns. Ties never break on
        # the uuid before content, so identical episodes do not reorder between runs.
        hits.sort(
            key=lambda r: (
                r.t_valid is None,
                r.t_valid or 0,
                r.created_at or 0,
                r.content,
                r.id,
            )
        )
        # `limit=0` means zero rows, not "unlimited".
        return hits if limit is None else hits[:limit]

    async def arecall_episodes(
        self,
        scope: Scope,
        entities: Optional[Sequence[str]] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="arecall_episodes")
        return self.recall_episodes(scope, entities, since, until, limit)

    def bounds_for_ids(
        self, scope: Scope, ids: Sequence[str]
    ) -> Dict[str, TemporalBounds]:
        require_scope(scope, method="bounds_for_ids")
        # Unranked and unlimited, by id. The provider calls this on every read because ranking
        # cannot be trusted to surface a bound: the SCD-2 successor row carries the same content
        # as the live row, so it scores the same and falls off the page.
        wanted = set(ids)
        found: Dict[str, TemporalBounds] = {}
        for row in self._bucket(scope):
            if row.id not in wanted:
                continue
            seen = found.get(row.id)
            if seen is None:
                found[row.id] = TemporalBounds(
                    row.t_valid, row.t_invalid, row.expired_at, row.superseded_by
                )
                continue
            # `filter(None, ...)` would drop 0 as well as None, and epoch 0 is a legitimate bound.
            candidates = [b for b in (seen.t_invalid, row.t_invalid) if b is not None]
            tightest = min(candidates) if candidates else None
            expired = (
                None
                if (seen.expired_at is None or row.expired_at is None)
                else seen.expired_at
            )
            found[row.id] = TemporalBounds(
                seen.t_valid, tightest, expired, seen.superseded_by or row.superseded_by
            )
        return found

    async def abounds_for_ids(
        self, scope: Scope, ids: Sequence[str]
    ) -> Dict[str, TemporalBounds]:
        require_scope(scope, method="abounds_for_ids")
        return self.bounds_for_ids(scope, ids)

    def invalidate(
        self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None
    ) -> None:
        require_scope(scope, method="invalidate")
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
                bounded = replace(row, t_invalid=tightest, expired_at=None)
                self._bucket(scope).append(bounded)

    async def ainvalidate(
        self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None
    ) -> None:
        require_scope(scope, method="ainvalidate")
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


def frontier_config() -> MemoryProviderConfig:
    """Mode B is declared here and served by the same adapter, on purpose.

    There is no external adapter yet: writing one against substrates that do not exist would prove
    only that a class can be subclassed. SM-X2 (#138) builds it against a real remote backend. What
    this shows is the part that is real today -- the mode reaches the provider, and the consumer
    below never sees it.
    """
    return MemoryProviderConfig(
        provider="co_located",
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

    print()
    print("Mode FRONTIER -- same consumer function, only the config changed:")
    remember_and_recall(build_memory_provider(frontier_config()), scope)

    # ---------------------------------------------------------------------
    # Point-in-time reads: the graph is the authority for what was true when
    # ---------------------------------------------------------------------
    print()
    print("Bi-temporal read:")
    # Deliberately the concrete adapter, not MemoryProvider: recording that a fact became false is
    # GraphSubstrate.invalidate, a substrate port, and SM-C1 does not promote it onto the seam --
    # that would expand the verb set ADR 0003 ratifies. Consolidation is the only thing in-tree
    # that drives it; SM-L5 (#126) owns making it a first-class operation. A demo-only reach.
    provider: CoLocatedMemoryProvider = build_memory_provider(mode_a_config())  # type: ignore[assignment]
    assert isinstance(
        provider, CoLocatedMemoryProvider
    )  # the reach below is adapter-specific
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
    print()
    print("Tenant isolation:")
    print(
        "  globex sees",
        len(provider.read(other, MemoryQuery(text="ada acme")).records),
        "of acme's records",
    )

    # Which providers config can select. Adapters self-register on import.
    from egpt.memory import registered_providers

    print()
    print("Registered providers:", ", ".join(registered_providers()))
