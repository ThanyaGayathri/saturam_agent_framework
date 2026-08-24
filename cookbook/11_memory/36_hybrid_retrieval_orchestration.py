"""Feed-forward retriever orchestration: vector, BM25 and graph, fanned out (SM-H1 #131).

`14_hybrid_candidates_pgvector.py` reaches the vector store directly, because at the time it was
written `provider.read` only ever called `vector.search` -- one leg, no BM25, and no orchestrator
to fan out through. This file shows what changed: `read` now runs the vector+BM25 leg and the graph
leg through `egpt.memory.retrieval.orchestrate`/`aorchestrate`, and BM25 participates whenever the
wired vector store implements `HybridSearchSubstrate` -- no code at this call site knows or cares.

**What this does not show, on purpose.** The lexical leg does not affect which records `read`
returns or how they rank. ADR 0003 section A.6 holds the three-leg (vector + BM25 + graph)
RRF-vs-weighted fusion choice open, pending a real graph retriever (SM-G9) to measure against -- so
folding BM25 into the interim vector+graph score-sum here would have this ticket pick that choice by
construction rather than by measurement. Until SM-H2 (#132) decides, the lexical leg reaches you
only as `MemorySlice.fusion["lexical_hits"]`, a count -- present, and visibly not influencing the
ranking below.

Self-contained: the two substrates are tiny in-memory stand-ins, the same pattern
`09_memory_provider_seam.py` uses for a seam-level demo, extended here so the vector one also
satisfies `HybridSearchSubstrate`. No database, no model, no network.
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence
from uuid import uuid4

from egpt.memory import (
    SCOPE_SLOTS,
    EntityWrite,
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
    require_scope,
)
from egpt.memory.graph.episodic import entity_node_id_for, normalized_mentions
from egpt.memory.substrates import HybridSearchSubstrate
from egpt.memory.types import EntityMerge, RetrievalCandidates


def word_overlap(query_text: str, content: str) -> float:
    """Jaccard word overlap, standing in for embedding similarity -- deliberately dilution-
    sensitive (unlike `09`'s raw intersection count), because the point below needs a record with
    more *of the query's words* but also more *other* words to score lower than one with fewer
    extra words diluting the match."""
    q, c = set(query_text.lower().split()), set(content.lower().split())
    return 0.0 if not q or not c else len(q & c) / len(q | c)


def scoped(record_scope: Scope, call_scope: Scope) -> bool:
    """Every slot the call scope sets must match -- not only the tenant (see `09`'s copy)."""
    for slot in SCOPE_SLOTS:
        wanted = getattr(call_scope, slot)
        if wanted is not None and getattr(record_scope, slot) != wanted:
            return False
    return True


class InMemoryHybridVector:
    """`09`'s `InMemoryVector`, extended with the second, lexical leg (SM-V3's shape).

    Word overlap stands in for embedding similarity, as `09` does; exact substring containment
    stands in for BM25 -- crude, but the two disagree often enough to show the point, which is that
    they are kept apart rather than blended.
    """

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
        hits = []
        for record in self._eligible(scope, query, tier):
            score = word_overlap(query.text, record.content)
            if score:
                hits.append(replace(record, score=score))
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
        after: Optional[tuple] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        return self.scan(scope, tier, limit, after)


class InMemoryGraph:
    """`09`'s `InMemoryGraph`, trimmed to the read-path methods this demo needs."""

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
        hits = []
        for row in self._bucket(scope):
            if (
                row.tier not in wanted
                or not row.is_current_belief()
                or not scoped(row.scope, scope)
            ):
                continue
            score = word_overlap(query.text, row.content)
            if score:
                hits.append(replace(row, score=score))
        hits.sort(key=lambda r: -(r.score or 0.0))
        return hits[: query.limit] if query.limit else []

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="atraverse")
        return self.traverse(scope, query, tier)

    # -- unused by this demo, present only so `isinstance(_, GraphSubstrate)` holds ---------------
    # `checked_graph` is a structural, presence-only check (every port method must exist and be
    # callable): a stand-in narrower than the full protocol fails construction before the demo
    # reaches the read path this file is actually about. `write`/`read` on the episodic tier never
    # call any of these -- entity/fact/relation writes and point-in-time invalidation are a
    # different demo (`17_entity_extraction_graph.py`, `19_relation_extraction_graph.py`,
    # `09_memory_provider_seam.py`'s bi-temporal section).
    def upsert_facts(self, scope: Scope, facts: Sequence[FactWrite]) -> None:
        raise NotImplementedError("not exercised by this demo")

    async def aupsert_facts(self, scope: Scope, facts: Sequence[FactWrite]) -> None:
        raise NotImplementedError("not exercised by this demo")

    def upsert_entities(self, scope: Scope, entities: Sequence[EntityWrite]) -> None:
        raise NotImplementedError("not exercised by this demo")

    async def aupsert_entities(
        self, scope: Scope, entities: Sequence[EntityWrite]
    ) -> None:
        raise NotImplementedError("not exercised by this demo")

    def match_entities(
        self,
        scope: Scope,
        candidate: EntityWrite,
        limit: int = 5,
        min_score: float = 0.92,
    ) -> List[tuple]:
        raise NotImplementedError("not exercised by this demo")

    async def amatch_entities(
        self,
        scope: Scope,
        candidate: EntityWrite,
        limit: int = 5,
        min_score: float = 0.92,
    ) -> List[tuple]:
        raise NotImplementedError("not exercised by this demo")

    def merge_entities(self, scope: Scope, merges: Sequence[EntityMerge]) -> None:
        raise NotImplementedError("not exercised by this demo")

    async def amerge_entities(
        self, scope: Scope, merges: Sequence[EntityMerge]
    ) -> None:
        raise NotImplementedError("not exercised by this demo")

    def recall_episodes(
        self,
        scope: Scope,
        entities: Optional[Sequence[str]] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        raise NotImplementedError("not exercised by this demo")

    async def arecall_episodes(
        self,
        scope: Scope,
        entities: Optional[Sequence[str]] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        raise NotImplementedError("not exercised by this demo")

    def recall_facts(
        self,
        scope: Scope,
        entities: Optional[Sequence[str]] = None,
        relation: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        raise NotImplementedError("not exercised by this demo")

    async def arecall_facts(
        self,
        scope: Scope,
        entities: Optional[Sequence[str]] = None,
        relation: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        raise NotImplementedError("not exercised by this demo")

    def invalidate(
        self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None
    ) -> None:
        raise NotImplementedError("not exercised by this demo")

    async def ainvalidate(
        self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None
    ) -> None:
        raise NotImplementedError("not exercised by this demo")

    def bounds_for_ids(
        self, scope: Scope, ids: Sequence[str]
    ) -> Dict[str, TemporalBounds]:
        require_scope(scope, method="bounds_for_ids")
        wanted = set(ids)
        return {
            row.id: TemporalBounds(
                row.t_valid, row.t_invalid, row.expired_at, row.superseded_by
            )
            for row in self._bucket(scope)
            if row.id in wanted
        }

    async def abounds_for_ids(
        self, scope: Scope, ids: Sequence[str]
    ) -> Dict[str, TemporalBounds]:
        require_scope(scope, method="abounds_for_ids")
        return self.bounds_for_ids(scope, ids)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    scope = Scope(tenant_id="acme", user_id="ada")
    vector = InMemoryHybridVector()
    print("Vector store offers a BM25 leg:", isinstance(vector, HybridSearchSubstrate))

    provider = build_memory_provider(
        MemoryProviderConfig(
            provider="co_located",
            options={"vector_substrate": vector, "graph_substrate": InMemoryGraph()},
        )
    )

    # Word overlap (vector, and graph -- both use it here) and exact substring containment
    # (lexical) disagree about which record the query "cat dog" favours:
    #   * `higher_overlap` -- "dog extra cat" -- has more of the query's words (2 of 3 tokens
    #     match `{cat, dog, extra}`) but never contains the literal phrase "cat dog".
    #   * `has_phrase` -- "cat dog nowhere near the rest" -- contains the literal phrase, so the
    #     lexical leg matches it, but its word overlap is diluted by five extra words and scores
    #     lower on both the vector and the graph leg.
    # If BM25 fed into the score-sum, `has_phrase`'s +1.0 lexical bonus would outrank
    # `higher_overlap`. It does not, and the ranking below proves it: `higher_overlap` stays first.
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

    print("\nRead result:")
    print("  fusion:", slice_.fusion)
    print(
        "  records (vector+graph score-sum only):",
        [(r.id, r.score) for r in slice_.records],
    )
    print(
        f"  -> 'higher_overlap' ranks first despite 'has_phrase' being the only record the "
        f"lexical leg matched (lexical_hits={slice_.fusion['lexical_hits']}) -- BM25 ran, and "
        f"did not move the ranking. That is SM-H2's call to make, not this read's.",
    )
