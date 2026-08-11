"""In-memory substrate doubles for the memory seam tests (SM-C1).

These live in the test tree, not the package. A shipped in-memory substrate would be an
integration (like ``egpt/integrations/db/in_memory/``), issue #89 scopes concrete stores out to
SM-V1/SM-G1, and a non-persistent memory store that is easy to import is a thing someone
eventually runs in production.

They are deliberately faithful on the two behaviours the provider depends on:

* Tenant scoping is real -- records are bucketed by ``scope.tenant_id``, so a cross-tenant leak
  would show up here rather than being assumed away.
* ``FakeGraph.invalidate`` does genuine **expire-and-reinsert (SCD-2)**: it expires the live row
  on the transaction axis and inserts a bounded copy. Nothing is mutated in place, because
  SM-R10 section 6 proved in-place wrong and a double that cheated would hide it.
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence

from egpt.memory.substrates import GraphSubstrate, VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import FactWrite, MemoryEvent, MemoryQuery, MemoryRecord, Scope, Substrate


def overlap(query: str, content: str) -> float:
    """Crude lexical similarity, standing in for embeddings. Deterministic by design."""
    q, c = set(query.lower().split()), set(content.lower().split())
    return 0.0 if not q or not c else len(q & c) / len(q | c)


class FakeVector:
    """An in-memory VectorSubstrate keyed by tenant."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, MemoryRecord]] = {}
        self.calls: List[str] = []

    def _bucket(self, scope: Scope) -> Dict[str, MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, {})

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        self.calls.append("upsert")
        bucket = self._bucket(scope)
        for record in records:
            bucket[record.id] = replace(record, substrate=Substrate.VECTOR)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        self.calls.append("aupsert")
        self.upsert(scope, records)

    def search(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        self.calls.append("search")
        assert query.as_of is None, "the provider must strip as_of before it reaches a substrate"
        tiers = {tier} if tier else set(query.resolved_tiers())
        hits = [
            replace(record, score=overlap(query.text, record.content))
            for record in self._bucket(scope).values()
            if record.tier in tiers
        ]
        return sorted([h for h in hits if h.score], key=lambda r: (-(r.score or 0.0), r.id))

    async def asearch(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        self.calls.append("asearch")
        return self.search(scope, query, tier)

    def scan(
        self,
        scope: Scope,
        tier: MemoryTier,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        self.calls.append("scan")
        rows = [r for r in self._bucket(scope).values() if r.tier is tier]
        if since is not None:
            rows = [r for r in rows if (r.created_at or 0) > since]
        rows.sort(key=lambda r: ((r.created_at or 0), r.id))
        return rows[:limit] if limit else rows

    async def ascan(
        self,
        scope: Scope,
        tier: MemoryTier,
        since: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        self.calls.append("ascan")
        return self.scan(scope, tier, since, limit)


class FakeGraph:
    """An in-memory GraphSubstrate with real SCD-2 invalidation.

    Rows accumulate: an invalidation never deletes and never edits a bound, so the full belief
    history stays reconstructible and ``traverse`` filters to current belief.
    """

    def __init__(self) -> None:
        self.rows: Dict[str, List[MemoryRecord]] = {}
        self.facts: List[FactWrite] = []
        self.calls: List[str] = []

    def _bucket(self, scope: Scope) -> List[MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, [])

    def upsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        self.calls.append("upsert_episode")
        self._bucket(scope).append(
            MemoryRecord(
                id=event.id or "",
                tier=event.tier,
                content=event.content,
                scope=scope,
                substrate=Substrate.GRAPH,
                topics=event.topics,
                t_valid=event.occurred_at,
                created_at=event.occurred_at,
            )
        )

    async def aupsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        self.calls.append("aupsert_episode")
        self.upsert_episode(scope, event)

    def upsert_fact(self, scope: Scope, fact: FactWrite) -> None:
        self.calls.append("upsert_fact")
        self.facts.append(fact)
        self._bucket(scope).append(replace(fact.record, substrate=Substrate.GRAPH))

    async def aupsert_fact(self, scope: Scope, fact: FactWrite) -> None:
        self.calls.append("aupsert_fact")
        self.upsert_fact(scope, fact)

    def traverse(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        self.calls.append("traverse")
        assert query.as_of is None, "the provider must strip as_of before it reaches a substrate"
        tiers = {tier} if tier else set(query.resolved_tiers())
        hits = [
            replace(row, score=overlap(query.text, row.content))
            for row in self._bucket(scope)
            if row.tier in tiers and row.is_current_belief()  # current belief only, SM-R10 §7 5a
        ]
        return sorted([h for h in hits if h.score], key=lambda r: (-(r.score or 0.0), r.id))

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        self.calls.append("atraverse")
        return self.traverse(scope, query, tier)

    def invalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        """Expire the live row on the transaction axis, then insert a bounded copy."""
        self.calls.append("invalidate")
        recorded_at = at if at is not None else t_invalid
        bucket = self._bucket(scope)
        for row in list(bucket):
            if row.id != edge_id or not row.is_current_belief():
                continue
            row.expired_at = recorded_at  # transaction time out -- not a valid-time edit
            tightest = t_invalid if row.t_invalid is None else min(row.t_invalid, t_invalid)
            bucket.append(replace(row, t_invalid=tightest, expired_at=None, created_at=recorded_at))

    async def ainvalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        self.calls.append("ainvalidate")
        self.invalidate(scope, edge_id, t_invalid, at)


def assert_ports_satisfied() -> None:
    """The doubles must satisfy the real ports, or these tests prove nothing about backends."""
    assert isinstance(FakeVector(), VectorSubstrate)
    assert isinstance(FakeGraph(), GraphSubstrate)
