"""
The Consolidation Worker
=========================

This example shows the async consolidation runner (SM-L1): draining
`MemoryProvider.consolidate` off the hot path, over a tier larger than one
bounded pass, idempotent and retryable.

Key concepts:
- `consolidate` (SM-C1) runs one bounded pass and reports `details["complete"]`.
  A tier larger than the pass cap needs more than one call to drain -- that is
  `ConsolidationWorker`'s job, not the caller's.
- The provider tracks its own resumption cursor internally (an exclusive
  `(created_at, id)` bound passed to `VectorSubstrate.scan`), so the worker
  does nothing more than call `aconsolidate` again. No page token, no `since`
  -- the interface's method signature is exactly what SM-C1 shipped.
- A failed pass is safe to retry outright: `consolidate` upserts a fact by an
  id derived from its source episodes, so re-running it lands the same row
  rather than a duplicate.
- `worker.start()` runs this as a background asyncio task, on an interval --
  nothing on the read or write path calls it.

The vector substrate below is the same tiny in-memory stand-in
`09_memory_provider_seam.py` uses, kept vector-only (SM-C5: a graph is
optional) so this file needs no database, no API key, and no network.
"""

import asyncio
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from egpt.memory import (
    MemoryEvent,
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    Scope,
    Substrate,
    require_scope,
)
from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig
from egpt.memory.consolidation import ConsolidationWorker

SCOPE = Scope(tenant_id="acme", user_id="ada")

_SCOPE_SLOTS = ("tenant_id", "agent_id", "user_id", "team_id", "session_id")


def scoped(record_scope: Scope, call_scope: Scope) -> bool:
    """Every slot the call scope *sets* must match -- not only the tenant."""
    return all(
        getattr(call_scope, slot) is None
        or getattr(record_scope, slot) == getattr(call_scope, slot)
        for slot in _SCOPE_SLOTS
    )


class InMemoryVector:
    """Keyword overlap standing in for embedding similarity -- see
    `09_memory_provider_seam.py` for the fuller, annotated version this trims."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, MemoryRecord]] = {}

    def _bucket(self, scope: Scope) -> Dict[str, MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, {})

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="upsert")
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
        hits = [
            replace(
                record, score=float(len(terms & set(record.content.lower().split())))
            )
            for record in self._bucket(scope).values()
            if record.tier in wanted
            and record.expired_at is None
            and scoped(record.scope, scope)
        ]
        ranked = sorted((h for h in hits if h.score), key=lambda r: -(r.score or 0.0))
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
        rows = [
            replace(r)
            for r in self._bucket(scope).values()
            if r.tier is tier and scoped(r.scope, scope)
        ]
        rows.sort(key=lambda r: ((r.created_at or 0), r.id))
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


def semantic_rows(vector: InMemoryVector) -> int:
    return sum(
        1
        for r in vector.rows.get(SCOPE.tenant_id, {}).values()
        if r.tier is MemoryTier.SEMANTIC
    )


async def main() -> None:
    vector = InMemoryVector()
    # A cap of 3 over 7 episodes, so draining the tier needs more than one pass --
    # the scenario `ConsolidationWorker` exists for.
    provider = CoLocatedMemoryProvider(
        vector=vector,
        config=MemoryProviderConfig(provider="co_located"),
        max_episodes_per_pass=3,
    )
    topics = [
        "deploys",
        "billing",
        "onboarding",
        "latency",
        "support",
        "pricing",
        "security",
    ]
    for i, topic in enumerate(topics):
        provider.write(
            SCOPE,
            [MemoryEvent(content=f"a note about {topic}", scope=SCOPE, id=f"e{i}")],
        )

    print("Draining a tier larger than one bounded pass")
    print("=" * 45)
    print(
        f"episodic rows written: {len(topics)}, pass cap: {provider.max_episodes_per_pass}"
    )

    worker = ConsolidationWorker(
        provider, [SCOPE], max_passes_per_scope=10, retry_delay=0.01
    )
    outcomes = await worker.run_once()
    outcome = outcomes[0]

    print(
        f"one worker tick ran {len(outcome.reports)} pass(es): "
        f"{[r.episodic_scanned for r in outcome.reports]}"
    )
    print(f"fully drained: {outcome.drained}")
    print(f"semantic facts now: {semantic_rows(vector)}")

    print()
    print("A transient failure is retried, and the retry is safe because")
    print("consolidate upserts by a content-derived id -- never a duplicate:")
    provider.write(
        SCOPE,
        [MemoryEvent(content="a note about incident response", scope=SCOPE, id="e7")],
    )
    attempts = {"n": 0}
    real_aconsolidate = provider.aconsolidate

    async def flaky(scope: Scope, tiers=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("simulated transient backend error")
        return await real_aconsolidate(scope, tiers)

    provider.aconsolidate = flaky
    retried = (await worker.run_once())[0]
    print(f"attempts made: {attempts['n']}, recovered: {retried.error is None}")
    print(f"semantic facts now: {semantic_rows(vector)}")

    print()
    print("Re-running the same tick again finds nothing new -- the cursor")
    print("already sits past every episode written so far:")
    idle = (await worker.run_once())[0]
    print(
        f"passes this tick: {len(idle.reports)}, episodes scanned: {idle.reports[0].episodic_scanned}"
    )


if __name__ == "__main__":
    asyncio.run(main())
