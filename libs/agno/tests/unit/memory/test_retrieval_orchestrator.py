"""The feed-forward orchestrator: vector, BM25 and graph legs, fanned out and left unfused
(SM-H1 #131).

``CoLocatedMemoryProvider``'s own read-path tests (``test_provider_co_located.py``) cover the
merge, bi-temporal reconciliation and slicing that consume ``RetrievalLegs`` -- this file is only
about what the orchestrator itself decides: which legs run, how BM25 is detected, and that the
vector-store call and the graph-store call are genuinely concurrent on the async path.
"""

import asyncio

import pytest

from egpt.memory.errors import MemoryScopeRequiredError
from egpt.memory.retrieval.orchestrator import RetrievalLegs, aorchestrate, orchestrate
from egpt.memory.substrates import HybridSearchSubstrate, VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, MemoryRecord, Scope, Substrate
from tests.unit.memory._fakes import FakeGraph, FakeHybridVector, FakeVector

#: Generous allowances for the liveness tests below, not expected durations -- the blocking leg
#: releases almost immediately once the signalling leg runs. A concurrent implementation finishes
#: in milliseconds; only a regression to sequential execution would approach these.
_BLOCK_TIMEOUT_S = 1.0
_OVERALL_TIMEOUT_S = 2.0

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


def test_both_substrates_none_returns_empty_legs_without_error():
    legs = orchestrate(ACME, query(), vector=None, graph=None)
    assert legs.vector == [] and legs.lexical == [] and legs.graph == []


async def test_both_substrates_none_returns_empty_legs_without_error_async():
    legs = await aorchestrate(ACME, query(), vector=None, graph=None)
    assert legs.vector == [] and legs.lexical == [] and legs.graph == []


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
    graph = seeded_graph(rows=())
    legs = orchestrate(ACME, query(text="nothing matches this"), vector=vector, graph=graph)
    assert legs.vector == [] and legs.lexical == [] and legs.graph == []
    assert vector.calls == ["search_candidates"]
    assert graph.calls == ["traverse"]  # ran, matched nothing -- not "did not run"


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
            await asyncio.wait_for(started.wait(), timeout=_BLOCK_TIMEOUT_S)
            return self.search(scope, query, tier)

    class SignallingGraph(FakeGraph):
        async def atraverse(self, scope, query, tier=None):
            started.set()
            return self.traverse(scope, query, tier)

    vector = seeded_vector(BlockingVector)
    graph = seeded_graph(SignallingGraph)

    legs = await asyncio.wait_for(aorchestrate(ACME, query(), vector=vector, graph=graph), timeout=_OVERALL_TIMEOUT_S)
    assert [r.id for r in legs.vector] == ["v0"]
    assert [r.id for r in legs.graph] == ["g0"]


async def test_the_hybrid_stores_call_and_the_graph_call_run_concurrently():
    """The structural argument (one `asyncio.gather` drives both branches of `vector_leg`, hybrid
    or plain) is not itself proof for the hybrid branch specifically -- lock it the same way as
    the plain-vector case above, with `asearch_candidates` as the blocking leg this time."""
    started = asyncio.Event()

    class BlockingHybridVector(FakeHybridVector):
        # Scope check omitted deliberately: this override proves liveness only, not scope
        # discipline (that is `test_a_hybrid_vectors_asearch_candidates_checks_scope_before_it_
        # ever_suspends`'s job, against the un-overridden `FakeHybridVector.asearch_candidates`).
        # Not a template for a compliant protocol implementation.
        async def asearch_candidates(self, scope, query, tier=None):
            await asyncio.wait_for(started.wait(), timeout=_BLOCK_TIMEOUT_S)
            return self.search_candidates(scope, query, tier)

    class SignallingGraph(FakeGraph):
        async def atraverse(self, scope, query, tier=None):
            started.set()
            return self.traverse(scope, query, tier)

    vector = seeded_vector(BlockingHybridVector)
    graph = seeded_graph(SignallingGraph)

    legs = await asyncio.wait_for(aorchestrate(ACME, query(), vector=vector, graph=graph), timeout=_OVERALL_TIMEOUT_S)
    assert [r.id for r in legs.vector] == ["v0"]
    assert [r.id for r in legs.graph] == ["g0"]


async def test_a_hybrid_vector_uses_asearch_candidates_never_asearch():
    vector = seeded_vector(FakeHybridVector)
    legs = await aorchestrate(ACME, query(), vector=vector, graph=None)
    assert [r.id for r in legs.lexical] == ["v0"]
    # Exact, not membership: `FakeHybridVector.asearch_candidates` no longer delegates through
    # `search_candidates`, so there is exactly one log entry, not a double-count.
    assert vector.calls == ["asearch_candidates"]


def test_a_hybrid_vectors_asearch_candidates_checks_scope_before_it_ever_suspends():
    """A raise-eventually assertion (`with pytest.raises(...): await ...`) cannot distinguish
    `require_scope` running before `await _yield()` from running after it -- both orderings raise
    before the coroutine returns to the caller either way. Driving the coroutine with `.send(None)`
    instead can: it advances execution to the *first* suspension point (or to the first raise,
    whichever comes first) and no further, so the assertion only passes if the scope check runs
    before any `await`."""
    vector = FakeHybridVector()
    coro = vector.asearch_candidates(None, MemoryQuery(text="x"))
    with pytest.raises(MemoryScopeRequiredError):
        coro.send(None)


def test_orchestrate_rejects_none_scope():
    """The orchestrator's own guard, not only the substrate fakes' -- a refactor that moved
    `require_scope` inside the substrate-dispatch branches (e.g. only reached once a substrate is
    wired) would pass every existing test here, since all of them wire at least one substrate."""
    with pytest.raises(MemoryScopeRequiredError):
        orchestrate(None, query(), vector=None, graph=None)


def test_aorchestrate_rejects_none_scope_before_suspending():
    """Async twin, same `.send(None)` technique as the hybrid-vector test above: proves
    `aorchestrate`'s own `require_scope` is its first statement, not merely reachable eventually."""
    coro = aorchestrate(None, query(), vector=None, graph=None)
    with pytest.raises(MemoryScopeRequiredError):
        coro.send(None)


@pytest.mark.parametrize("substrates", [None, [Substrate.VECTOR], [Substrate.GRAPH]])
async def test_async_and_sync_orchestration_agree_on_which_legs_run(substrates):
    vector, graph = seeded_vector(FakeHybridVector), seeded_graph()
    sync_legs = orchestrate(ACME, query(substrates=substrates), vector=vector, graph=graph)

    vector2, graph2 = seeded_vector(FakeHybridVector), seeded_graph()
    async_legs = await aorchestrate(ACME, query(substrates=substrates), vector=vector2, graph=graph2)

    assert [r.id for r in sync_legs.vector] == [r.id for r in async_legs.vector]
    assert [r.id for r in sync_legs.lexical] == [r.id for r in async_legs.lexical]
    assert [r.id for r in sync_legs.graph] == [r.id for r in async_legs.graph]
