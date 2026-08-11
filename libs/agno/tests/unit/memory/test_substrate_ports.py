"""Substrate ports for the Symbiotic Memory seam (SM-C1, phase 2).

``runtime_checkable`` Protocols check method *presence*, not signatures, so the risk these tests
exist for is a backend that satisfies half a port and fails at call time -- most likely by
implementing the sync half and forgetting the async twin, which is the exact shape CLAUDE.md's
sync+async rule exists to prevent.
"""

from typing import List, Optional, Sequence

import pytest

from egpt.memory.substrates import GraphSubstrate, VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import FactWrite, MemoryEvent, MemoryQuery, MemoryRecord, Scope

SCOPE = Scope(tenant_id="acme", user_id="u1")


class CompleteVector:
    """Structurally satisfies VectorSubstrate without inheriting from it."""

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None: ...

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None: ...

    def search(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]: ...

    async def asearch(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]: ...

    def scan(
        self, scope: Scope, tier: MemoryTier, since: Optional[int] = None, limit: Optional[int] = None
    ) -> List[MemoryRecord]: ...

    async def ascan(
        self, scope: Scope, tier: MemoryTier, since: Optional[int] = None, limit: Optional[int] = None
    ) -> List[MemoryRecord]: ...


class CompleteGraph:
    """Structurally satisfies GraphSubstrate without inheriting from it."""

    def upsert_episode(self, scope: Scope, event: MemoryEvent) -> None: ...

    async def aupsert_episode(self, scope: Scope, event: MemoryEvent) -> None: ...

    def upsert_fact(self, scope: Scope, fact: FactWrite) -> None: ...

    async def aupsert_fact(self, scope: Scope, fact: FactWrite) -> None: ...

    def traverse(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]: ...

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]: ...

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
        "upsert_episode",
        "aupsert_episode",
        "upsert_fact",
        "aupsert_fact",
        "traverse",
        "atraverse",
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
