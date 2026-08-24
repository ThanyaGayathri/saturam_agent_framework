"""Feed-forward retriever orchestration (SM-H1 #131).

Fans a read out across the vector, BM25 (lexical) and graph legs and returns them unfused, tier-
and scope-scoped, as :class:`RetrievalLegs`. Fusing the legs into one ranked slice is not this
module's job -- it stays a fan-out, so nothing here may pick a strategy or drop a leg's ranking --
but is decided now, by ``CoLocatedMemoryProvider`` (SM-H2 #132, ``_fusion_method``/``_merged``):
the default ``score_sum`` still keeps the lexical leg diagnostics-only, since ADR 0003 A.6 holds
the 3-leg RRF-vs-weighted *default* open pending SM-X7's (#143) measurement, but the opt-in
``rrf`` strategy does fold it in -- rank-based fusion never needed the legs' scores to agree in
the first place, which is what made that safe to wire ahead of the measurement.

**That promise binds every caller, not only this module.** ``CoLocatedMemoryProvider``'s default
still folds only the vector and graph legs into its score-sum (see its own module docstring); only
its opt-in ``rrf`` strategy also folds in the lexical leg. The lexical leg reaches
``MemorySlice`` as a diagnostic count in every case, ranked on or not.

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
from egpt.memory.tiers import routes_to
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
        """The lists keyed by leg name, so a consumer iterates legs rather than field names.

        **Not an endorsement of feeding all three into every ranking merge.** Whether the lexical
        leg enters ranking is ``CoLocatedMemoryProvider._merged``'s call (SM-H2 #132, ADR 0003
        A.6): its default ``score_sum`` still takes only ``.vector`` and ``.graph`` directly, not
        this method's full iteration, and its opt-in ``rrf`` strategy is what actually calls
        ``.legs()`` to fold the lexical leg in -- rank-based fusion never needed the legs' scores
        to agree in the first place, which is what made that safe ahead of SM-X7's (#143)
        weighted-vs-RRF measurement. See the module docstring for the fuller picture.
        """
        return {"vector": self.vector, "lexical": self.lexical, "graph": self.graph}


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
) -> RetrievalLegs:
    """Run the vector+BM25 leg and the graph leg, sequentially, and collect the candidates.

    Each leg independently enforces tenant/tier scope (every substrate method takes ``scope`` and
    is required to apply it); this function only decides **whether** a leg runs, via :func:`_wants`,
    mirroring :meth:`MemoryQuery.resolved_tiers`'s routing exactly so neither leg silently diverges
    from the other about which rows are eligible (ADR 0003 finding F3).

    There is deliberately no per-tier ``tier`` parameter narrowing a single substrate call: routing
    (whether a leg runs) is decided from ``query.resolved_tiers()``, the query's *full* tier set,
    and a parameter narrowing one substrate's *contents* to a single tier without also narrowing
    routing would silently diverge from it -- reserved, unsettled semantics belonging to whatever
    ticket first needs a per-tier orchestration call.
    """
    require_scope(scope, method="orchestrate")
    vector_hits: List[MemoryRecord] = []
    lexical_hits: List[MemoryRecord] = []
    graph_hits: List[MemoryRecord] = []
    if vector is not None and _wants(query, Substrate.VECTOR):
        if isinstance(vector, HybridSearchSubstrate):
            candidates = vector.search_candidates(scope, query)
            vector_hits, lexical_hits = candidates.vector, candidates.lexical
        else:
            vector_hits = list(vector.search(scope, query))
    if graph is not None and _wants(query, Substrate.GRAPH):
        graph_hits = list(graph.traverse(scope, query))
    return RetrievalLegs(vector=vector_hits, lexical=lexical_hits, graph=graph_hits)


async def aorchestrate(
    scope: Scope,
    query: MemoryQuery,
    *,
    vector: Optional[VectorSubstrate],
    graph: Optional[GraphSubstrate],
) -> RetrievalLegs:
    """Async variant of :func:`orchestrate`. The vector-store call and the graph-store call are
    independent by construction, so they run concurrently under ``asyncio.gather``; BM25 is not a
    third call (see module docstring).
    """
    require_scope(scope, method="aorchestrate")

    async def vector_leg() -> Tuple[List[MemoryRecord], List[MemoryRecord]]:
        if vector is None or not _wants(query, Substrate.VECTOR):
            return [], []
        if isinstance(vector, HybridSearchSubstrate):
            candidates = await vector.asearch_candidates(scope, query)
            return candidates.vector, candidates.lexical
        return list(await vector.asearch(scope, query)), []

    async def graph_leg() -> List[MemoryRecord]:
        if graph is None or not _wants(query, Substrate.GRAPH):
            return []
        return list(await graph.atraverse(scope, query))

    # Bare gather is intentional: a leg failure aborts the read, propagating immediately rather
    # than settling both first. Contrast `_gather_writes` in `adapters.co_located`, which settles
    # every write and reports a partial result deliberately -- a caller retries a write with the
    # same stamped ids and it is idempotent by construction. A read has no such retry contract: a
    # partial `RetrievalLegs` (one leg's candidates, one leg silently empty) reads as "the other
    # leg matched nothing" rather than "the other leg failed", which is a wrong answer, not a
    # smaller one. Graceful degradation -- `return_exceptions=True` with the caller distinguishing
    # a failed leg from an empty one -- is a deliberate policy change, not a default, and belongs
    # to whichever ticket first needs a partial read.
    (vector_hits, lexical_hits), graph_hits = await asyncio.gather(vector_leg(), graph_leg())
    return RetrievalLegs(vector=vector_hits, lexical=lexical_hits, graph=graph_hits)
