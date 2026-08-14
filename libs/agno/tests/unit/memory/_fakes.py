"""In-memory substrate doubles for the memory seam tests (SM-C1).

These live in the test tree, not the package. A shipped in-memory substrate would be an
integration (like ``egpt/integrations/db/in_memory/``), issue #89 scopes concrete stores out to
SM-V1/SM-G1, and a non-persistent memory store that is easy to import is a thing someone
eventually runs in production.

They are deliberately faithful on the two behaviours the provider depends on:

* Tenant scoping is real -- records are bucketed by ``scope.tenant_id``, so a cross-tenant leak
  would show up here rather than being assumed away.
* Every scope-taking method calls ``require_scope`` first (SM-C4 #92), because a double must be
  **stricter than the loosest real backend** and the one real substrate in the tree refuses an
  unscoped call at its statement layer. A double that answered one would be modelling a backend
  weaker than any that exists, and ``CoLocatedMemoryProvider.vector`` / ``.graph`` are public, so
  a caller can reach these directly.
* ``FakeGraph.invalidate`` does genuine **expire-and-reinsert (SCD-2)**: it expires the live row
  on the transaction axis and inserts a bounded copy. Nothing is mutated in place, because
  SM-R10 section 6 proved in-place wrong and a double that cheated would hide it.
"""

import asyncio
from dataclasses import replace
from typing import Dict, List, Optional, Sequence
from uuid import uuid4

from egpt.memory.substrates import GraphSubstrate, VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    SCOPE_SLOTS,
    FactWrite,
    MemoryEvent,
    MemoryQuery,
    MemoryRecord,
    Scope,
    require_scope,
    Substrate,
    TemporalBounds,
)


async def _yield() -> None:
    """A real suspension point in every ``a*`` double.

    Without one, ``asyncio.gather`` in the provider never actually interleaves and the async paths
    are exercised as thinly disguised sync ones -- which is a contributing reason the ``awrite``
    failure-path divergence went unnoticed for six rounds.
    """
    await asyncio.sleep(0)


def scoped(record_scope: Scope, call_scope: Scope) -> bool:
    """Every slot the call scope sets must match -- not only ``tenant_id``.

    A double that partitions on tenant alone cannot observe a per-user leak, so no test could see
    one. Modelling the full conjunctive predicate is what makes the port's scoping contract
    testable at all.
    """
    for slot in SCOPE_SLOTS:
        wanted = getattr(call_scope, slot)
        if wanted is not None and getattr(record_scope, slot) != wanted:
            return False
    return True


def reject_scope_mismatch(call_scope: Scope, record: MemoryRecord) -> None:
    """A write's records must carry the scope the write was made under.

    The port says a record is stored *within* the call scope, so a record arriving with a different
    one is ambiguous -- store it under the call scope and a per-user fact is filed tenant-wide;
    store it under its own and the call scope was decorative. A real backend resolves that ambiguity
    silently and per-implementation, so the double refuses it instead.

    Not enforcing this is what made a provider bug undetectable: the double bucketed by the call
    scope but filtered on ``record.scope``, so writing a narrow record under a broad scope round-
    tripped correctly here and would have widened it in Postgres.
    """
    if record.scope != call_scope:
        raise ValueError(
            f"Record {record.id!r} carries scope {record.scope.key()} but is being written under "
            f"{call_scope.key()}. Batch per scope; the two must agree."
        )


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
        require_scope(scope, method="upsert")
        self.calls.append("upsert")
        bucket = self._bucket(scope)
        for record in records:
            reject_scope_mismatch(scope, record)
            bucket[record.id] = replace(record, substrate=Substrate.VECTOR)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="aupsert")
        await _yield()
        self.calls.append("aupsert")
        self.upsert(scope, records)

    def search(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        require_scope(scope, method="search")
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
        require_scope(scope, method="asearch")
        await _yield()
        self.calls.append("asearch")
        return self.search(scope, query, tier)

    def scan(self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None) -> List[MemoryRecord]:
        require_scope(scope, method="scan")
        self.calls.append("scan")
        self.scan_limits.append(limit)
        # Copies, like `search` returns. `scan` handing back live objects made a provider that
        # mutates what it scanned unobservable -- and the consolidate path did exactly that.
        rows = [replace(r) for r in self._matching(scope) if r.tier is tier]
        rows.sort(key=lambda r: ((r.created_at or 0), r.id))
        # `limit=0` means zero rows, not "unlimited". Every real backend distinguishes `LIMIT 0`
        # from an omitted limit, and conflating them here would hide a provider that fetches
        # everything when it asked for nothing.
        return rows if limit is None else rows[:limit]

    async def ascan(self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        await _yield()
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
        #: Every id set the provider asked bounds for -- asserting on the *request* is what makes a
        #: missing reconciliation visible, since a result-only assertion cannot see one.
        self.bounds_requests: List[List[str]] = []
        #: One entry per write call. A result-only assertion cannot tell one batched round trip
        #: from `len(events)` of them, which is the property the batched verbs exist for.
        self.episode_batch_sizes: List[int] = []
        self.fact_batch_sizes: List[int] = []

    def _bucket(self, scope: Scope) -> List[MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, [])

    def _matching(self, scope: Scope) -> List[MemoryRecord]:
        return [r for r in self._bucket(scope) if scoped(r.scope, scope)]

    def upsert_episodes(self, scope: Scope, events: Sequence[MemoryEvent]) -> None:
        """Upsert by id, as the port requires. Appending let a repeat write look idempotent."""
        require_scope(scope, method="upsert_episodes")
        self.calls.append("upsert_episodes")
        self.episode_batch_sizes.append(len(events))
        for event in events:
            self._upsert_episode(scope, event)

    def _upsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        row = MemoryRecord(
            # Never `event.id or ""`: `types.py` rejects an empty id precisely because it is the
            # dedup key, and this file is copied as reference by anyone building a double. The
            # provider always stamps one, so this branch is unreachable in production -- which is
            # exactly why it has to be right here rather than relied on there.
            id=event.id or str(uuid4()),
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

    async def aupsert_episodes(self, scope: Scope, events: Sequence[MemoryEvent]) -> None:
        require_scope(scope, method="aupsert_episodes")
        # Suspends, then delegates to the sync name. The delegation is load-bearing for tests that
        # patch one verb to fail: patching only the sync name and having the async half reimplement
        # it is how the two halves get compared against different code.
        await _yield()
        self.calls.append("aupsert_episodes")
        self.upsert_episodes(scope, events)

    def upsert_facts(self, scope: Scope, facts: Sequence[FactWrite]) -> None:
        """Upsert by id, as the port name says -- appending would let a re-run look idempotent."""
        require_scope(scope, method="upsert_facts")
        self.calls.append("upsert_facts")
        self.fact_batch_sizes.append(len(facts))
        for fact in facts:
            self._upsert_fact(scope, fact)

    def _upsert_fact(self, scope: Scope, fact: FactWrite) -> None:
        reject_scope_mismatch(scope, fact.record)
        self.facts.append(fact)
        bucket = self._bucket(scope)
        row = replace(fact.record, substrate=Substrate.GRAPH)
        for index, existing in enumerate(bucket):
            if existing.id == row.id and existing.is_current_belief():
                bucket[index] = row
                return
        bucket.append(row)

    async def aupsert_facts(self, scope: Scope, facts: Sequence[FactWrite]) -> None:
        require_scope(scope, method="aupsert_facts")
        await _yield()
        self.calls.append("aupsert_facts")
        self.upsert_facts(scope, facts)

    def traverse(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        require_scope(scope, method="traverse")
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
        require_scope(scope, method="atraverse")
        await _yield()
        self.calls.append("atraverse")
        return self.traverse(scope, query, tier)

    def bounds_for_ids(self, scope: Scope, ids: Sequence[str]) -> Dict[str, TemporalBounds]:
        """Unranked, unlimited lookup by id -- the whole point is to see what ranking hid.

        Reads the same rows ``traverse`` does but ignores score, ``limit``, and current belief, and
        reports the **tightest** bound across every revision of an id. Reporting only the current
        row would miss nothing today, but a double that answered from the ranked page would make
        the provider's reconciliation untestable, which is the failure this method exists to catch.
        """
        require_scope(scope, method="bounds_for_ids")
        self.calls.append("bounds_for_ids")
        self.bounds_requests.append(list(ids))
        wanted = set(ids)
        found: Dict[str, TemporalBounds] = {}
        for row in self._matching(scope):
            if row.id not in wanted:
                continue
            seen = found.get(row.id)
            if seen is None:
                found[row.id] = TemporalBounds(row.t_valid, row.t_invalid, row.expired_at, row.superseded_by)
                continue
            # `filter(None, ...)` would drop 0 as well as None -- epoch 0 is a legitimate bound,
            # and dropping the tighter one leaves a false fact current.
            candidates = [b for b in (seen.t_invalid, row.t_invalid) if b is not None]
            tightest = min(candidates) if candidates else None
            # `expired_at=None` wins: the live revision is the one that decides current belief.
            expired = None if (seen.expired_at is None or row.expired_at is None) else seen.expired_at
            found[row.id] = TemporalBounds(seen.t_valid, tightest, expired, seen.superseded_by or row.superseded_by)
        return found

    async def abounds_for_ids(self, scope: Scope, ids: Sequence[str]) -> Dict[str, TemporalBounds]:
        require_scope(scope, method="abounds_for_ids")
        await _yield()
        self.calls.append("abounds_for_ids")
        return self.bounds_for_ids(scope, ids)

    def invalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        """Expire the live row on the transaction axis, then insert a bounded copy."""
        require_scope(scope, method="invalidate")
        self.calls.append("invalidate")
        recorded_at = at if at is not None else t_invalid
        bucket = self._bucket(scope)
        for row in list(bucket):
            # Scope-filtered, so an invalidation issued under the wrong scope is observable.
            if row.id != edge_id or not row.is_current_belief() or not scoped(row.scope, scope):
                continue
            row.expired_at = recorded_at  # transaction time out -- not a valid-time edit
            tightest = t_invalid if row.t_invalid is None else min(row.t_invalid, t_invalid)
            bucket.append(replace(row, t_invalid=tightest, expired_at=None, created_at=recorded_at))

    async def ainvalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        require_scope(scope, method="ainvalidate")
        await _yield()
        self.calls.append("ainvalidate")
        self.invalidate(scope, edge_id, t_invalid, at)


def assert_ports_satisfied() -> None:
    """The doubles must satisfy the real ports, or these tests prove nothing about backends."""
    assert isinstance(FakeVector(), VectorSubstrate)
    assert isinstance(FakeGraph(), GraphSubstrate)
