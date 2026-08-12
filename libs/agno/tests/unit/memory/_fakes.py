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


def scoped(record_scope: Scope, call_scope: Scope) -> bool:
    """Every slot the call scope sets must match -- not only ``tenant_id``.

    A double that partitions on tenant alone cannot observe a per-user leak, so no test could see
    one. Modelling the full conjunctive predicate is what makes the port's scoping contract
    testable at all.
    """
    for slot in ("tenant_id", "user_id", "agent_id", "team_id", "session_id"):
        wanted = getattr(call_scope, slot)
        if wanted is not None and getattr(record_scope, slot) != wanted:
            return False
    return True


def current_belief(record: MemoryRecord) -> bool:
    """Transaction time only, per the port: ``expired_at IS NULL``, filtered before ranking.

    Deliberately *not* valid time. A substrate that filtered ``as_of`` would withhold the SCD-2
    successor row carrying ``t_invalid`` -- which is the evidence the provider merges onto the
    vector leg's stale projection to catch an invalidated fact (finding F2).
    """
    return record.expired_at is None


def overlap(query: str, content: str) -> float:
    """Crude lexical similarity, standing in for embeddings. Deterministic by design."""
    q, c = set(query.lower().split()), set(content.lower().split())
    return 0.0 if not q or not c else len(q & c) / len(q | c)


def _ranked(hits: List[MemoryRecord], limit: Optional[int] = None) -> List[MemoryRecord]:
    """Drop non-matches, sort by score, and honour ``limit`` -- as a real backend does.

    Three details that a lazier double got wrong, each of which hid a provider bug:

    * **``limit`` is applied here.** Both in-repo doubles ignored it, which is the structural
      reason the post-fusion filter could return zero records while five valid ones existed and no
      test could observe it. A double more permissive than every real backend cannot catch a bug
      that only appears when the backend is strict.
    * **Non-matches are dropped on ``> 0``, explicitly.** Zero overlap means no match, which is
      what a search excludes -- but ``if hit.score`` would also discard a *deliberate* 0.0 and read
      as an accident.
    * **Ties keep insertion order.** Python's sort is stable, so equal scores come back in write
      order, which is what a real ranking supplies. Breaking ties on ``id`` would order by UUID and
      make the double's output differ run to run.
    """
    matches = [hit for hit in hits if hit.score is not None and hit.score > 0.0]
    ranked = sorted(matches, key=lambda r: -(r.score or 0.0))
    # `limit=0` returns nothing; only `None` means unlimited. See the note in `FakeVector.scan`.
    return ranked if limit is None else ranked[:limit]


class FakeVector:
    """An in-memory VectorSubstrate keyed by tenant."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, MemoryRecord]] = {}
        self.calls: List[str] = []
        #: Every ``limit`` this substrate was *asked* for, in order. Asserting on the request
        #: rather than the response is what makes an over-fetch or a batch bound testable at all:
        #: with `limit=0` the result is empty whatever was requested, so a result-only assertion
        #: is vacuous.
        self.search_limits: List[Optional[int]] = []
        self.scan_limits: List[Optional[int]] = []

    def _bucket(self, scope: Scope) -> Dict[str, MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, {})

    def _matching(self, scope: Scope) -> List[MemoryRecord]:
        """Rows this scope may see -- every set slot, not just the tenant."""
        return [r for r in self._bucket(scope).values() if scoped(r.scope, scope)]

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
        self.search_limits.append(query.limit)
        tiers = {tier} if tier else set(query.resolved_tiers())
        hits = [
            replace(record, score=overlap(query.text, record.content))
            for record in self._matching(scope)
            # Validity filtered BEFORE ranking, as the port requires: applying `limit` first and
            # validity second lets a page of invalidated rows consume the whole limit.
            if record.tier in tiers and current_belief(record)
        ]
        return _ranked(hits, query.limit)

    async def asearch(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        self.calls.append("asearch")
        return self.search(scope, query, tier)

    def scan(self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None) -> List[MemoryRecord]:
        self.calls.append("scan")
        self.scan_limits.append(limit)
        rows = [r for r in self._matching(scope) if r.tier is tier]
        rows.sort(key=lambda r: ((r.created_at or 0), r.id))
        # `limit=0` means zero rows, not "unlimited". Every real backend distinguishes `LIMIT 0`
        # from an omitted limit, and conflating them here would hide a provider that fetches
        # everything when it asked for nothing.
        return rows if limit is None else rows[:limit]

    async def ascan(self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None) -> List[MemoryRecord]:
        self.calls.append("ascan")
        return self.scan(scope, tier, limit)


class FakeGraph:
    """An in-memory GraphSubstrate with real SCD-2 invalidation.

    Rows accumulate: an invalidation never deletes and never edits a bound, so the full belief
    history stays reconstructible and ``traverse`` filters to current belief.
    """

    def __init__(self) -> None:
        self.rows: Dict[str, List[MemoryRecord]] = {}
        self.facts: List[FactWrite] = []
        self.calls: List[str] = []
        self.traverse_limits: List[Optional[int]] = []

    def _bucket(self, scope: Scope) -> List[MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, [])

    def _matching(self, scope: Scope) -> List[MemoryRecord]:
        return [r for r in self._bucket(scope) if scoped(r.scope, scope)]

    def upsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        """Upsert by id, as the port requires. Appending let a repeat write look idempotent."""
        self.calls.append("upsert_episode")
        row = MemoryRecord(
            id=event.id or "",
            tier=event.tier,
            content=event.content,
            scope=scope,
            substrate=Substrate.GRAPH,
            topics=list(event.topics) if event.topics is not None else None,
            t_valid=event.occurred_at,
            created_at=event.occurred_at,
        )
        bucket = self._bucket(scope)
        for index, existing in enumerate(bucket):
            if existing.id == row.id and existing.is_current_belief():
                bucket[index] = row
                return
        bucket.append(row)

    async def aupsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        self.calls.append("aupsert_episode")
        self.upsert_episode(scope, event)

    def upsert_fact(self, scope: Scope, fact: FactWrite) -> None:
        """Upsert by id, as the port name says -- appending would let a re-run look idempotent."""
        self.calls.append("upsert_fact")
        self.facts.append(fact)
        bucket = self._bucket(scope)
        row = replace(fact.record, substrate=Substrate.GRAPH)
        for index, existing in enumerate(bucket):
            if existing.id == row.id and existing.is_current_belief():
                bucket[index] = row
                return
        bucket.append(row)

    async def aupsert_fact(self, scope: Scope, fact: FactWrite) -> None:
        self.calls.append("aupsert_fact")
        self.upsert_fact(scope, fact)

    def traverse(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        self.calls.append("traverse")
        self.traverse_limits.append(query.limit)
        tiers = {tier} if tier else set(query.resolved_tiers())
        hits = [
            replace(row, score=overlap(query.text, row.content))
            for row in self._matching(scope)
            if row.tier in tiers and current_belief(row)  # transaction time, before ranking
        ]
        return _ranked(hits, query.limit)

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
