"""Reciprocal Rank Fusion, opt-in (SM-H2 #132).

`36_hybrid_retrieval_orchestration.py` shows the provider's default: `score_sum` sums the vector
and graph legs' scores and leaves the lexical (BM25) leg out of ranking entirely, because ADR 0003
section A.6 holds the 3-leg fusion choice open pending a real graph retriever to measure against.
This file shows the alternative SM-H2 adds to make that safe to opt into: RRF is rank-based, so it
never needs the legs' scores to share a scale, and folds all three legs in --
`config.fusion={"method": "rrf", "k": ...}` selects it. It does **not** become the default; that
default switch is SM-H2/H3's own to make, once SM-G9's (#107) real graph retriever lets it be
measured (ADR 0003 A.6).

Same "cat dog" scenario `36` uses (its two substrate stand-ins, trimmed to the read-path methods
this demo needs), so the two files show the same query landing differently depending on which
fusion method is configured -- nothing about the data changes, only the strategy.

Self-contained: no database, no model, no network.
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence
from uuid import uuid4

from egpt.memory import (
    SCOPE_SLOTS,
    MemoryEvent,
    MemoryProviderConfig,
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    Scope,
    Substrate,
    build_memory_provider,
    require_scope,
)
from egpt.memory.graph.episodic import entity_node_id_for, normalized_mentions
from egpt.memory.retrieval.fuse import DEFAULT_RRF_K
from egpt.memory.types import RetrievalCandidates


def word_overlap(query_text: str, content: str) -> float:
    q, c = set(query_text.lower().split()), set(content.lower().split())
    return 0.0 if not q or not c else len(q & c) / len(q | c)


def scoped(record_scope: Scope, call_scope: Scope) -> bool:
    for slot in SCOPE_SLOTS:
        wanted = getattr(call_scope, slot)
        if wanted is not None and getattr(record_scope, slot) != wanted:
            return False
    return True


class InMemoryHybridVector:
    """`36`'s stand-in, unchanged: word overlap for the vector leg, exact substring containment
    for the lexical one."""

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

    def _eligible(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier]
    ) -> List[MemoryRecord]:
        wanted = {tier} if tier else set(query.resolved_tiers())
        return [
            record
            for record in self._bucket(scope).values()
            if record.tier in wanted
            and record.expired_at is None
            and scoped(record.scope, scope)
        ]

    def search(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="search")
        hits = [
            replace(record, score=score)
            for record in self._eligible(scope, query, tier)
            for score in [word_overlap(query.text, record.content)]
            if score
        ]
        ranked = sorted(hits, key=lambda r: -(r.score or 0.0))
        return ranked[: query.limit] if query.limit else []

    async def asearch(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="asearch")
        return self.search(scope, query, tier)

    def search_candidates(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> RetrievalCandidates:
        require_scope(scope, method="search_candidates")
        vector_hits = self.search(scope, query, tier)
        lexical_hits = sorted(
            (
                replace(record, score=1.0)
                for record in self._eligible(scope, query, tier)
                if query.text.lower() in record.content.lower()
            ),
            key=lambda r: r.id,
        )
        lexical_hits = lexical_hits[: query.limit] if query.limit else []
        return RetrievalCandidates(vector=vector_hits, lexical=lexical_hits)

    async def asearch_candidates(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> RetrievalCandidates:
        require_scope(scope, method="asearch_candidates")
        return self.search_candidates(scope, query, tier)

    def scan(
        self,
        scope: Scope,
        tier: MemoryTier,
        limit: Optional[int] = None,
        after: Optional[tuple] = None,
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
        after: Optional[tuple] = None,
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
    """`36`'s stand-in, unchanged: only `traverse` and `bounds_for_ids` (the two `read()` calls)
    have real bodies. The rest exist only so `checked_graph`'s structural, presence-only check
    (`isinstance` against a `runtime_checkable` Protocol, method names only) passes at
    construction -- entity/fact/relation writes and point-in-time invalidation are a different
    demo (`17_entity_extraction_graph.py`, `19_relation_extraction_graph.py`).
    """

    def __init__(self) -> None:
        self.rows: Dict[str, List[MemoryRecord]] = {}

    def _bucket(self, scope: Scope) -> List[MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, [])

    def upsert_episodes(self, scope: Scope, events: Sequence[MemoryEvent]) -> None:
        require_scope(scope, method="upsert_episodes")
        for event in events:
            self._bucket(scope).append(
                MemoryRecord(
                    id=event.id or str(uuid4()),
                    tier=event.tier,
                    content=event.content,
                    scope=scope,
                    substrate=Substrate.GRAPH,
                    t_valid=event.occurred_at,
                    provenance={
                        "entity_ids": [
                            entity_node_id_for(scope, name)
                            for name in normalized_mentions(
                                event.entities, episode_id=event.id or ""
                            )
                        ]
                    },
                )
            )

    async def aupsert_episodes(
        self, scope: Scope, events: Sequence[MemoryEvent]
    ) -> None:
        require_scope(scope, method="aupsert_episodes")
        self.upsert_episodes(scope, events)

    def traverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="traverse")
        wanted = {tier} if tier else set(query.resolved_tiers())
        hits = [
            replace(row, score=score)
            for row in self._bucket(scope)
            if row.tier in wanted
            and row.is_current_belief()
            and scoped(row.scope, scope)
            for score in [word_overlap(query.text, row.content)]
            if score
        ]
        hits.sort(key=lambda r: -(r.score or 0.0))
        return hits[: query.limit] if query.limit else []

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="atraverse")
        return self.traverse(scope, query, tier)

    def bounds_for_ids(self, scope: Scope, ids: Sequence[str]):
        require_scope(scope, method="bounds_for_ids")
        from egpt.memory.types import TemporalBounds

        wanted = set(ids)
        return {
            row.id: TemporalBounds(
                row.t_valid, row.t_invalid, row.expired_at, row.superseded_by
            )
            for row in self._bucket(scope)
            if row.id in wanted
        }

    async def abounds_for_ids(self, scope: Scope, ids: Sequence[str]):
        require_scope(scope, method="abounds_for_ids")
        return self.bounds_for_ids(scope, ids)

    # -- unused by this demo, present only so `isinstance(_, GraphSubstrate)` holds ---------------
    def upsert_facts(self, scope, facts):
        raise NotImplementedError("not exercised by this demo")

    async def aupsert_facts(self, scope, facts):
        raise NotImplementedError("not exercised by this demo")

    def upsert_entities(self, scope, entities):
        raise NotImplementedError("not exercised by this demo")

    async def aupsert_entities(self, scope, entities):
        raise NotImplementedError("not exercised by this demo")

    def match_entities(self, scope, candidate, limit=5, min_score=0.92):
        raise NotImplementedError("not exercised by this demo")

    async def amatch_entities(self, scope, candidate, limit=5, min_score=0.92):
        raise NotImplementedError("not exercised by this demo")

    def merge_entities(self, scope, merges):
        raise NotImplementedError("not exercised by this demo")

    async def amerge_entities(self, scope, merges):
        raise NotImplementedError("not exercised by this demo")

    def recall_episodes(self, scope, entities=None, since=None, until=None, limit=None):
        raise NotImplementedError("not exercised by this demo")

    async def arecall_episodes(
        self, scope, entities=None, since=None, until=None, limit=None
    ):
        raise NotImplementedError("not exercised by this demo")

    def recall_facts(
        self, scope, entities=None, relation=None, since=None, until=None, limit=None
    ):
        raise NotImplementedError("not exercised by this demo")

    async def arecall_facts(
        self, scope, entities=None, relation=None, since=None, until=None, limit=None
    ):
        raise NotImplementedError("not exercised by this demo")

    def invalidate(self, scope, edge_id, t_invalid, at=None):
        raise NotImplementedError("not exercised by this demo")

    async def ainvalidate(self, scope, edge_id, t_invalid, at=None):
        raise NotImplementedError("not exercised by this demo")


def _read_with(fusion: dict, scope: Scope) -> None:
    provider = build_memory_provider(
        MemoryProviderConfig(
            provider="co_located",
            fusion=fusion,
            options={
                "vector_substrate": InMemoryHybridVector(),
                "graph_substrate": InMemoryGraph(),
            },
        )
    )
    provider.write(
        scope,
        [
            MemoryEvent(id="higher_overlap", content="dog extra cat", scope=scope),
            MemoryEvent(
                id="has_phrase", content="cat dog nowhere near the rest", scope=scope
            ),
        ],
    )
    slice_ = provider.read(scope, MemoryQuery(text="cat dog", limit=5))
    print(f"  fusion method: {slice_.fusion['method']}")
    print(f"  lexical_hits: {slice_.fusion['lexical_hits']}")
    print(f"  records: {[(r.id, round(r.score, 4)) for r in slice_.records]}")


if __name__ == "__main__":
    demo_scope = Scope(tenant_id="acme", user_id="ada")

    print("Default (score_sum) -- lexical leg counted, not ranked on:")
    _read_with({}, demo_scope)

    print(
        f"\nRRF, k={DEFAULT_RRF_K} -- lexical leg's rank-1 hit now moves the ranking:"
    )
    _read_with({"method": "rrf"}, demo_scope)
    print(
        "  -> 'has_phrase' overtakes 'higher_overlap': it is the only record the lexical leg "
        "matched (the literal phrase 'cat dog'), and under RRF that rank-1 credit stacks with "
        "its vector/graph ranks rather than being left out of the sum."
    )

    print("\nRRF, k=1 -- a small k weighs every leg's top rank far more heavily:")
    _read_with({"method": "rrf", "k": 1}, demo_scope)
