"""
Near-Duplicate Fact Dedup
==========================

This example shows the SM-L4 dedup stage: folding facts that are the *same
fact* once normalized, even when `_distil`'s Jaccard grouping treats them as
two.

Key concepts:
- `_distil` (SM-C1) groups episodes by raw `text.lower().split()` token
  overlap before a fact is ever built. Punctuation defeats it: "the deploy
  failed!" and "the deploy failed" share only 2 of 4 tokens (jaccard 0.5,
  below the 0.8 threshold), so they survive `_distil` as two separate facts.
- `consolidation.dedup.merge_near_duplicate_facts` runs immediately after
  `_distil`, in both `consolidate` and `aconsolidate`, and folds facts whose
  content is identical *after* normalization -- the same normalization
  `Curator._normalize` (`learn/curate.py`) applies, reused rather than
  reimplemented (`egpt.memory.types.normalize_entity_name`).
- Folding keeps every source episode's provenance, widens the bi-temporal
  window to whichever member's evidence is broader, and unions topics --
  never just keeps the first fact's own bounds.
- `ConsolidationReport.near_duplicate_facts_folded` counts what this stage
  folded, kept separate from `duplicates_merged` (`_distil`'s own count):
  the two are different units -- facts folded here, episodes absorbed there.

The vector substrate below is the same tiny in-memory stand-in
`23_consolidation_worker.py` uses, kept vector-only so this file needs no
database, no API key, and no network.
"""

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

SCOPE = Scope(tenant_id="acme", user_id="ada")


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
        return []

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
        rows = [replace(r) for r in self._bucket(scope).values() if r.tier is tier]
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
        return self._bucket(scope).get(id)

    async def aget_record(self, scope: Scope, id: str) -> Optional[MemoryRecord]:
        require_scope(scope, method="aget_record")
        return self.get_record(scope, id)


def semantic_facts(vector: InMemoryVector) -> List[MemoryRecord]:
    return [
        r
        for r in vector.rows.get(SCOPE.tenant_id, {}).values()
        if r.tier is MemoryTier.SEMANTIC
    ]


def main() -> None:
    vector = InMemoryVector()
    provider = CoLocatedMemoryProvider(
        vector=vector, config=MemoryProviderConfig(provider="co_located")
    )

    print("Two episodes, one fact once normalized -- but not by Jaccard alone")
    print("=" * 70)
    provider.write(
        SCOPE,
        [
            MemoryEvent(
                content="the deploy failed!",
                scope=SCOPE,
                id="e1",
                occurred_at=100,
                topics=["deploys"],
            ),
            MemoryEvent(
                content="the deploy failed",
                scope=SCOPE,
                id="e2",
                occurred_at=50,
                topics=["incidents"],
            ),
        ],
    )

    report = provider.consolidate(SCOPE)

    print(f"episodes scanned: {report.episodic_scanned}")
    print(f"semantic facts written: {report.semantic_written}")
    print(
        f"duplicates_merged (episodes, _distil's own Jaccard grouping): {report.duplicates_merged}"
    )
    print(
        f"near_duplicate_facts_folded (facts, this stage): {report.near_duplicate_facts_folded}"
    )

    (fact,) = semantic_facts(vector)
    print()
    print("The surviving fact keeps every source episode's provenance,")
    print("widens to the earlier valid-time bound, and unions topics:")
    print(f"  content: {fact.content!r}")
    print(f"  source_episode_ids: {sorted(fact.provenance['source_episode_ids'])}")
    print(f"  t_valid: {fact.t_valid}  (the earlier of the two episodes' 100 and 50)")
    print(f"  topics: {fact.topics}")

    print()
    print("A third, genuinely distinct episode does not fold into it:")
    provider.write(
        SCOPE,
        [MemoryEvent(content="customer asked about invoicing", scope=SCOPE, id="e3")],
    )
    second_pass = provider.consolidate(SCOPE)
    print(f"semantic facts written this pass: {second_pass.semantic_written}")
    print(
        f"near_duplicate_facts_folded this pass: {second_pass.near_duplicate_facts_folded}"
    )
    print(f"total semantic facts now: {len(semantic_facts(vector))}")


if __name__ == "__main__":
    main()
