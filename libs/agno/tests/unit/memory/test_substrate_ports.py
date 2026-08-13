"""Substrate ports for the Symbiotic Memory seam (SM-C1, phase 2).

``runtime_checkable`` Protocols check method *presence*, not signatures, so the risk these tests
exist for is a backend that satisfies half a port and fails at call time -- most likely by
implementing the sync half and forgetting the async twin, which is the exact shape CLAUDE.md's
sync+async rule exists to prevent.
"""

from typing import Dict, List, Optional, Sequence

import pytest

from egpt.memory.substrates import GraphSubstrate, VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import FactWrite, MemoryEvent, MemoryQuery, MemoryRecord, Scope, TemporalBounds

SCOPE = Scope(tenant_id="acme", user_id="u1")


class CompleteVector:
    """Structurally satisfies VectorSubstrate without inheriting from it."""

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None: ...

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None: ...

    def search(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]: ...

    async def asearch(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]: ...

    # Signature mirrors the real port exactly. `runtime_checkable` checks method *presence*, so a
    # stale parameter here passes the conformance test while misleading anyone who copies this as
    # reference -- and `since` was removed from `scan` when the consolidation cursor went to SM-L1.
    def scan(self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None) -> List[MemoryRecord]: ...

    async def ascan(self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None) -> List[MemoryRecord]: ...


class CompleteGraph:
    """Structurally satisfies GraphSubstrate without inheriting from it."""

    def upsert_episodes(self, scope: Scope, events: Sequence[MemoryEvent]) -> None: ...

    async def aupsert_episodes(self, scope: Scope, events: Sequence[MemoryEvent]) -> None: ...

    def upsert_facts(self, scope: Scope, facts: Sequence[FactWrite]) -> None: ...

    async def aupsert_facts(self, scope: Scope, facts: Sequence[FactWrite]) -> None: ...

    def traverse(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]: ...

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]: ...

    def bounds_for_ids(self, scope: Scope, ids: Sequence[str]) -> Dict[str, TemporalBounds]: ...

    async def abounds_for_ids(self, scope: Scope, ids: Sequence[str]) -> Dict[str, TemporalBounds]: ...

    def invalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None: ...

    async def ainvalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None: ...


# ---------------------------------------------------------------------------
# Structural conformance
# ---------------------------------------------------------------------------


def without(complete, missing):
    """A class identical to ``complete`` but with one method removed.

    Built by copying the functions rather than subclassing, because a subclass would inherit the
    very method the test needs absent.
    """
    body = {name: fn for name, fn in vars(complete).items() if callable(fn) and name != missing}
    return type("Partial" + complete.__name__, (), body)


def test_a_complete_backend_satisfies_the_port_without_inheriting():
    """Protocols, not ABCs, so an existing VectorDb can be adapted without a base class."""
    assert VectorSubstrate not in CompleteVector.__mro__  # nominally unrelated ...
    assert isinstance(CompleteVector(), VectorSubstrate)  # ... but structurally conformant
    assert isinstance(CompleteGraph(), GraphSubstrate)


@pytest.mark.parametrize("missing", ["upsert", "aupsert", "search", "asearch", "scan", "ascan"])
def test_a_vector_backend_missing_any_method_fails_the_port(missing):
    assert not isinstance(without(CompleteVector, missing)(), VectorSubstrate)


@pytest.mark.parametrize(
    "missing",
    [
        "upsert_episodes",
        "aupsert_episodes",
        "upsert_facts",
        "aupsert_facts",
        "traverse",
        "atraverse",
        "bounds_for_ids",
        "abounds_for_ids",
        "invalidate",
        "ainvalidate",
    ],
)
def test_a_graph_backend_missing_any_method_fails_the_port(missing):
    """Every async twin is load-bearing: forgetting one must fail here, not at call time."""
    assert not isinstance(without(CompleteGraph, missing)(), GraphSubstrate)


def test_the_two_ports_are_not_interchangeable():
    """A graph backend must not accidentally pass as a vector one, or fusion loses a leg."""
    assert not isinstance(CompleteGraph(), VectorSubstrate)
    assert not isinstance(CompleteVector(), GraphSubstrate)


# ---------------------------------------------------------------------------
# FactWrite: the shape that makes a fact writable as something other than an episode
# ---------------------------------------------------------------------------


def a_record(**overrides):
    defaults = {
        "id": "f1",
        "tier": MemoryTier.SEMANTIC,
        "content": "Ada works at Acme",
        "scope": SCOPE,
    }
    defaults.update(overrides)
    return MemoryRecord(**defaults)


def test_a_full_triple_is_recognized_as_an_edge():
    fact = FactWrite(record=a_record(), subject="Ada", predicate="works_at", object="Acme")

    assert fact.is_triple() is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"subject": "Ada"},
        {"subject": "Ada", "predicate": "works_at"},
        {"predicate": "works_at", "object": "Acme"},
        {"subject": "", "predicate": "works_at", "object": "Acme"},
    ],
)
def test_a_partial_triple_is_storable_but_not_an_edge(kwargs):
    """SPO is optional structure over the statement -- an untripled fact is not an error."""
    fact = FactWrite(record=a_record(), **kwargs)

    assert fact.is_triple() is False
    assert fact.record.content == "Ada works at Acme"


def test_source_episode_ids_is_first_class_and_defaults_empty():
    """Provenance is unioned forward across consolidation passes, so it cannot be a dict key."""
    fact = FactWrite(record=a_record(), source_episode_ids=["e1", "e2"])

    assert fact.source_episode_ids == ["e1", "e2"]
    assert FactWrite(record=a_record()).source_episode_ids == []


def test_fact_defaults_to_full_confidence_and_no_derivation():
    fact = FactWrite(record=a_record())

    assert fact.confidence == 1.0
    assert fact.derived_by is None


# ---------------------------------------------------------------------------
# Double conformance -- these exercise `_fakes.py`, not the provider
# ---------------------------------------------------------------------------
#
# The port methods below are Protocol declarations with `...` bodies, so there is no production
# code under test here: deleting the provider entirely leaves these green. They are named for what
# they are. Their value is that the doubles every *provider* test depends on genuinely model the
# port -- a permissive double cannot catch a provider bug. The provider-level assertions for the
# same invariants live in test_provider_co_located.py.


def test_the_double_honours_upsert_episodes_by_id():
    """`upsert_episodes` is upsert **by id**, which PartialWriteError's retry advice depends on."""
    from tests.unit.memory._fakes import FakeGraph

    graph = FakeGraph()
    event = MemoryEvent(content="first", scope=SCOPE, id="dup")

    graph.upsert_episodes(SCOPE, [event])
    graph.upsert_episodes(SCOPE, [MemoryEvent(content="second", scope=SCOPE, id="dup")])

    assert [r.content for r in graph.rows[SCOPE.tenant_id]] == ["second"]


def test_the_double_keeps_the_edge_id_on_the_bounded_copy():
    """A surrogate key on the successor would break fusion's dedup-by-id.

    That dedup is the only thing carrying the graph's bound onto the vector leg's projection, so a
    fresh id means the invalidated fact returns as current on an ordinary read.
    """
    from tests.unit.memory._fakes import FakeGraph

    graph = FakeGraph()
    graph.upsert_episodes(SCOPE, [MemoryEvent(content="ada works at acme", scope=SCOPE, id="f1", occurred_at=100)])

    graph.invalidate(SCOPE, "f1", t_invalid=300, at=310)

    rows = graph.rows[SCOPE.tenant_id]
    # A *set* of one id cannot tell expire-and-reinsert from a no-op, which is the whole property.
    assert len(rows) == 2
    assert {r.id for r in rows} == {"f1"}
    assert sorted((r.t_invalid or 0) for r in rows) == [0, 300]
    assert sum(1 for r in rows if r.is_current_belief()) == 1


def test_the_double_scopes_on_every_slot_not_only_the_tenant():
    """`user_id` is "the identity namespace that survives sessions"; ignoring it leaks per-user."""
    from tests.unit.memory._fakes import FakeVector

    alice = Scope(tenant_id="acme", user_id="alice")
    bob = Scope(tenant_id="acme", user_id="bob")
    vector = FakeVector()
    vector.upsert(alice, [MemoryRecord(id="a1", tier=MemoryTier.SEMANTIC, content="alice salary", scope=alice)])

    assert vector.search(bob, MemoryQuery(text="alice salary")) == []
    assert len(vector.search(alice, MemoryQuery(text="alice salary"))) == 1
