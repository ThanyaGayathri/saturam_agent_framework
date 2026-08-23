"""The feed-forward orchestrator: vector, BM25 and graph legs, fanned out and left unfused
(SM-H1 #131).

``CoLocatedMemoryProvider``'s own read-path tests (``test_provider_co_located.py``) cover the
merge, bi-temporal reconciliation and slicing that consume ``RetrievalLegs`` -- this file is only
about what the orchestrator itself decides: which legs run, how BM25 is detected, and that the
vector-store call and the graph-store call are genuinely concurrent on the async path.
"""

import asyncio

import pytest

from egpt.memory.retrieval.orchestrator import RetrievalLegs, aorchestrate, orchestrate
from egpt.memory.substrates import HybridSearchSubstrate, VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, MemoryRecord, Scope, Substrate
from tests.unit.memory._fakes import FakeGraph, FakeHybridVector, FakeVector

ACME = Scope(tenant_id="acme", user_id="u1")


def record(id_="r1", content="alpha beta", tier=MemoryTier.EPISODIC, scope=ACME, **kwargs):
    return MemoryRecord(id=id_, content=content, tier=tier, scope=scope, **kwargs)


def query(text="alpha", **kwargs):
    return MemoryQuery(text=text, **kwargs)


def seeded_vector(cls=FakeVector, rows=("alpha beta",)):
    vector = cls()
    for i, content in enumerate(rows):
        vector.upsert(ACME, [record(id_=f"v{i}", content=content)])
    vector.calls.clear()  # the seeding writes are not part of what a test asserts on
    return vector


def seeded_graph(cls=FakeGraph, rows=("alpha gamma",)):
    graph = cls()
    for i, content in enumerate(rows):
        graph.upsert_episodes(ACME, [MemoryEvent(id=f"g{i}", content=content, scope=ACME)])
    graph.calls.clear()
    return graph


# -- wiring: which legs run at all -----------------------------------------------------------


def test_both_legs_run_when_both_substrates_are_wired():
    vector, graph = seeded_vector(), seeded_graph()
    legs = orchestrate(ACME, query(), vector=vector, graph=graph)
    assert [r.id for r in legs.vector] == ["v0"]
    assert [r.id for r in legs.graph] == ["g0"]
    assert legs.lexical == []


def test_a_none_substrate_produces_an_empty_leg_rather_than_an_error():
    legs = orchestrate(ACME, query(), vector=None, graph=seeded_graph())
    assert legs.vector == [] and legs.lexical == []
    assert [r.id for r in legs.graph] == ["g0"]


def test_a_query_naming_only_graph_never_touches_the_vector_store():
    vector, graph = seeded_vector(), seeded_graph()
    legs = orchestrate(ACME, query(substrates=[Substrate.GRAPH]), vector=vector, graph=graph)
    assert legs.vector == [] and legs.lexical == []
    assert vector.calls == []
    assert [r.id for r in legs.graph] == ["g0"]


def test_a_query_naming_only_graph_skips_the_hybrid_stores_round_trip_too():
    """A hybrid store has two entrypoints (`search` and `search_candidates`); a query excluding
    `Substrate.VECTOR` must call neither, not only the one a plain `FakeVector` would have used."""
    vector, graph = seeded_vector(FakeHybridVector), seeded_graph()
    legs = orchestrate(ACME, query(substrates=[Substrate.GRAPH]), vector=vector, graph=graph)
    assert legs.vector == [] and legs.lexical == []
    assert vector.calls == []
    assert [r.id for r in legs.graph] == ["g0"]


def test_a_tier_no_wired_substrate_serves_skips_that_legs_round_trip():
    """`MemoryTier.PROCEDURAL` does not route to the graph (`TIER_POLICY`), so a query scoped to
    it must not call `traverse` at all -- not call it and discard an empty result."""
    vector, graph = seeded_vector(), seeded_graph()
    legs = orchestrate(ACME, query(tiers=[MemoryTier.PROCEDURAL]), vector=vector, graph=graph)
    assert legs.graph == []
    assert graph.calls == []


# -- BM25 feature detection --------------------------------------------------------------------


def test_a_plain_vector_substrate_has_no_lexical_leg_and_is_called_once_via_search():
    vector = seeded_vector(FakeVector)
    legs = orchestrate(ACME, query(), vector=vector, graph=None)
    assert legs.lexical == []
    assert vector.calls == ["search"]


def test_a_hybrid_vector_substrate_contributes_a_lexical_leg_in_one_call():
    vector = seeded_vector(FakeHybridVector, rows=("alpha beta",))
    legs = orchestrate(ACME, query(), vector=vector, graph=None)
    assert [r.id for r in legs.vector] == ["v0"]
    assert [r.id for r in legs.lexical] == ["v0"]
    # One round trip, not two: `search_candidates` alone, never a bare `search`.
    assert vector.calls == ["search_candidates"]


def test_a_hybrid_substrate_that_satisfies_the_protocol_structurally_is_detected():
    assert isinstance(FakeHybridVector(), HybridSearchSubstrate)
    assert isinstance(FakeVector(), VectorSubstrate)
    assert not isinstance(FakeVector(), HybridSearchSubstrate)


# -- RetrievalLegs shape ------------------------------------------------------------------------


def test_a_leg_that_ran_and_matched_nothing_is_an_empty_list_not_a_missing_one():
    vector = FakeHybridVector()  # nothing upserted
    legs = orchestrate(ACME, query(text="nothing matches this"), vector=vector, graph=seeded_graph(rows=()))
    assert legs.vector == [] and legs.lexical == [] and legs.graph == []
    assert vector.calls == ["search_candidates"]


def test_as_lists_preserves_field_order_for_score_sum_merge():
    legs = RetrievalLegs(vector=[record("v")], lexical=[record("l")], graph=[record("g")])
    assert [ids[0].id for ids in legs.as_lists()] == ["v", "l", "g"]


def test_legs_keys_by_name():
    legs = RetrievalLegs(vector=[record("v")], lexical=[record("l")], graph=[record("g")])
    assert {name: [r.id for r in hits] for name, hits in legs.legs().items()} == {
        "vector": ["v"],
        "lexical": ["l"],
        "graph": ["g"],
    }


# -- async: real concurrency, and parity with the sync path ------------------------------------


async def test_the_vector_and_graph_calls_run_concurrently():
    """A blocking vector leg and a signalling graph leg deadlock under `timeout` unless the two
    coroutines actually interleave -- the same liveness technique
    `test_the_two_read_legs_actually_run_concurrently` uses on the provider itself."""
    started = asyncio.Event()

    class BlockingVector(FakeVector):
        async def asearch(self, scope, query, tier=None):
            await asyncio.wait_for(started.wait(), timeout=1.0)
            return self.search(scope, query, tier)

    class SignallingGraph(FakeGraph):
        async def atraverse(self, scope, query, tier=None):
            started.set()
            return self.traverse(scope, query, tier)

    vector = seeded_vector(BlockingVector)
    graph = seeded_graph(SignallingGraph)

    legs = await asyncio.wait_for(aorchestrate(ACME, query(), vector=vector, graph=graph), timeout=2.0)
    assert [r.id for r in legs.vector] == ["v0"]
    assert [r.id for r in legs.graph] == ["g0"]


async def test_a_hybrid_vector_uses_asearch_candidates_never_asearch():
    vector = seeded_vector(FakeHybridVector)
    legs = await aorchestrate(ACME, query(), vector=vector, graph=None)
    assert [r.id for r in legs.lexical] == ["v0"]
    assert "asearch_candidates" in vector.calls
    assert "asearch" not in vector.calls and "search" not in vector.calls


@pytest.mark.parametrize("substrates", [None, [Substrate.VECTOR], [Substrate.GRAPH]])
async def test_async_and_sync_orchestration_agree_on_which_legs_run(substrates):
    vector, graph = seeded_vector(FakeHybridVector), seeded_graph()
    sync_legs = orchestrate(ACME, query(substrates=substrates), vector=vector, graph=graph)

    vector2, graph2 = seeded_vector(FakeHybridVector), seeded_graph()
    async_legs = await aorchestrate(ACME, query(substrates=substrates), vector=vector2, graph=graph2)

    assert [r.id for r in sync_legs.vector] == [r.id for r in async_legs.vector]
    assert [r.id for r in sync_legs.lexical] == [r.id for r in async_legs.lexical]
    assert [r.id for r in sync_legs.graph] == [r.id for r in async_legs.graph]
