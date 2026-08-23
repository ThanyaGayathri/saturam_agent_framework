"""Feed-forward retriever orchestration (SM-H1 #131).

Fans a read out across the vector, BM25 (lexical) and graph legs and returns them unfused, tier-
and scope-scoped, as :class:`RetrievalLegs`. Fusing the legs into one ranked slice is SM-H2's
(#132) job -- ADR 0003 section A.6 holds the 3-leg RRF-vs-weighted choice open, so nothing here may
pick a strategy or drop a leg's ranking.

**That promise binds every caller, not only this module.** ``CoLocatedMemoryProvider`` folds the
vector and graph legs into its interim score-sum today (a pre-existing, undecided placeholder --
see its own module docstring), but must not fold the lexical leg into that sum: doing so would
have this ticket implicitly pick how BM25 weighs against the other two, which is exactly the
choice A.6 defers. The lexical leg reaches ``MemorySlice`` only as a diagnostic count until SM-H2
decides how it participates in ranking.

BM25 does not cost a separate round trip: a store implementing
:class:`~egpt.memory.substrates.HybridSearchSubstrate` returns its vector and lexical legs from one
``search_candidates`` call (SM-V3 #97), so the real fan-out is two independent backend calls --
vector store and graph store -- run concurrently via ``asyncio.gather`` on the async path. The sync
path calls them in sequence, matching the existing 2-leg convention in
``adapters.co_located.CoLocatedMemoryProvider``.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from egpt.memory.substrates import GraphSubstrate, HybridSearchSubstrate, VectorSubstrate
from egpt.memory.tiers import MemoryTier, routes_to
from egpt.memory.types import MemoryQuery, MemoryRecord, Scope, Substrate, require_scope

__all__ = ["RetrievalLegs", "aorchestrate", "orchestrate"]


@dataclass(frozen=True)
class RetrievalLegs:
    """The three feed-forward legs, unfused, tenant/tier scoped.

    ``vector`` and ``lexical`` come from the same store (:class:`RetrievalCandidates
    <egpt.memory.types.RetrievalCandidates>` deliberately stays 2-leg, since it describes what one
    store returns); ``graph`` comes from a different substrate entirely and is not folded into
    that container. This is where the two are held alongside each other, per that container's own
    docstring.

    A leg that did not run (substrate unwired, or no requested tier routes there) is an **empty
    list, not an absent one** -- a fusion stage must be able to tell "ran and matched nothing" from
    "did not run" the same way each individual substrate port already promises for one leg.
    """

    vector: List[MemoryRecord] = field(default_factory=list)
    lexical: List[MemoryRecord] = field(default_factory=list)
    graph: List[MemoryRecord] = field(default_factory=list)

    def legs(self) -> Dict[str, List[MemoryRecord]]:
        """The lists keyed by leg name, so a consumer iterates legs rather than field names."""
        return {"vector": self.vector, "lexical": self.lexical, "graph": self.graph}

    def as_lists(self) -> List[List[MemoryRecord]]:
        """The lists in a fixed order, for a caller that wants a flat sequence of all legs.

        Not an endorsement of feeding all three into a ranking merge -- see the module docstring
        on why the lexical leg specifically must not enter one yet.
        """
        return [self.vector, self.lexical, self.graph]


def _wants(query: MemoryQuery, substrate: Substrate) -> bool:
    """Whether ``query`` routes to ``substrate`` at all.

    A leg no requested tier routes to is a guaranteed-empty round trip -- a network hop for
    nothing, and on a graph-only provider the difference between a procedural read that reports the
    tier unserved and one that quietly asks the graph anyway.
    """
    if query.substrates is not None and substrate not in query.substrates:
        return False
    return routes_to(substrate, query.resolved_tiers())


def orchestrate(
    scope: Scope,
    query: MemoryQuery,
    *,
    vector: Optional[VectorSubstrate],
    graph: Optional[GraphSubstrate],
    tier: Optional[MemoryTier] = None,
) -> RetrievalLegs:
    """Run the vector+BM25 leg and the graph leg, sequentially, and collect the candidates.

    Each leg independently enforces tenant/tier scope (every substrate method takes ``scope`` and
    ``tier`` and is required to apply both); this function only decides **whether** a leg runs, via
    :func:`_wants`, mirroring :meth:`MemoryQuery.resolved_tiers`'s routing exactly so neither leg
    silently diverges from the other about which rows are eligible (ADR 0003 finding F3).

    **``tier`` narrows execution only, not routing.** It is forwarded to each substrate call
    (narrowing what that call returns to one tier), but ``_wants`` always decides whether a leg
    runs from ``query.resolved_tiers()`` -- the *query's* full tier set, not this parameter. No
    current caller passes a non-``None`` ``tier`` (``CoLocatedMemoryProvider`` always omits it), so
    the two agreeing today is incidental. A future caller passing ``tier=X`` alongside a broader
    ``query.tiers`` gets routing decided by the query's tiers while every substrate narrows its
    *contents* to ``X`` -- reserved, unsettled behaviour that SM-H2's per-tier orchestration (if it
    needs one) should define deliberately rather than inherit by accident.
    """
    require_scope(scope, method="orchestrate")
    vector_hits: List[MemoryRecord] = []
    lexical_hits: List[MemoryRecord] = []
    graph_hits: List[MemoryRecord] = []
    if vector is not None and _wants(query, Substrate.VECTOR):
        if isinstance(vector, HybridSearchSubstrate):
            candidates = vector.search_candidates(scope, query, tier)
            vector_hits, lexical_hits = candidates.vector, candidates.lexical
        else:
            vector_hits = list(vector.search(scope, query, tier))
    if graph is not None and _wants(query, Substrate.GRAPH):
        graph_hits = list(graph.traverse(scope, query, tier))
    return RetrievalLegs(vector=vector_hits, lexical=lexical_hits, graph=graph_hits)


async def aorchestrate(
    scope: Scope,
    query: MemoryQuery,
    *,
    vector: Optional[VectorSubstrate],
    graph: Optional[GraphSubstrate],
    tier: Optional[MemoryTier] = None,
) -> RetrievalLegs:
    """Async variant of :func:`orchestrate`. The vector-store call and the graph-store call are
    independent by construction, so they run concurrently under ``asyncio.gather``; BM25 is not a
    third call (see module docstring).

    ``tier`` carries the same execution-only, routing-excluded caveat as :func:`orchestrate` --
    see its docstring.
    """
    require_scope(scope, method="aorchestrate")

    async def vector_leg() -> Tuple[List[MemoryRecord], List[MemoryRecord]]:
        if vector is None or not _wants(query, Substrate.VECTOR):
            return [], []
        if isinstance(vector, HybridSearchSubstrate):
            candidates = await vector.asearch_candidates(scope, query, tier)
            return candidates.vector, candidates.lexical
        return list(await vector.asearch(scope, query, tier)), []

    async def graph_leg() -> List[MemoryRecord]:
        if graph is None or not _wants(query, Substrate.GRAPH):
            return []
        return list(await graph.atraverse(scope, query, tier))

    (vector_hits, lexical_hits), graph_hits = await asyncio.gather(vector_leg(), graph_leg())
    return RetrievalLegs(vector=vector_hits, lexical=lexical_hits, graph=graph_hits)
