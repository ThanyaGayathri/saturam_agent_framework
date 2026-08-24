"""Adversarial cross-tenant isolation at the memory data layer (SM-X6 #142).

SM-C4 (#92) built the guard; ``test_tenant_scoping.py`` proves it is structurally present on
every adapter and every substrate double. What that file explicitly does not prove, by its own
account, is that a *live* provider actually denies a tenant that tries: "Isolation across two live
tenants on a real backend is not provable here, and this file does not claim it. ... SM-X6's
(#142) adversarial suite" is where that claim gets made.

This file seeds tenant A's memory -- vector and graph both -- then has tenant B attempt every read,
write, and traverse the seam and its substrates expose, using tenant A's own ids and content. The
assertion is not "the call raised": a call that refuses loudly and a call that quietly returns
nothing are both acceptable, and a call that raises *after* writing is not. What must hold is that
tenant A's stored rows are unreadable and unmodifiable from tenant B, byte for byte, before and
after the whole attempt sweep -- mirroring
``tests/integration/os/tenancy_postgres/test_adversarial_tenant_isolation.py``'s sweep-and-snapshot
shape for EGPT-2.7, one layer down.

**Runs as a unit test, deliberately.** ``FakeVector``/``FakeGraph`` are the doubles the whole
memory seam is tested against, and ``_fakes.py`` documents why they are trustworthy for exactly
this property: tenant scoping is real (bucketed by ``scope.tenant_id``, filtered by every set
slot), not mocked away. Isolation on the two *real* backends already has its own adversarial
coverage -- ``tests/integration/memory/pgvector/test_vector_substrate.py``
(``test_a_read_cannot_reach_another_tenant`` and neighbours) and
``tests/integration/memory/neo4j/test_graph_substrate.py`` (``test_a_scope_that_did_not_write_a_
row_cannot_read_it`` and neighbours) -- but both suites are gated on a live database and excluded
from CI's default run (``-m "not integration and not system"``), so neither satisfies this ticket's
"runs in CI" criterion. This file does, at the layer that composes both substrates through one
seam, which the per-substrate suites cannot exercise.

**What this deliberately does not build:** a live-backend rerun of the same sweep is SM-X2's
(#138), which owns proving config-only swap (and by extension this suite) against a real Mode B
backend; no such backend exists yet.
"""

from typing import Any, Callable, List

import pytest

from egpt.memory.config import build_memory_provider
from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    EntityMerge,
    EntityWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    Scope,
    Substrate,
)
from tests.unit.memory.test_provider_swap import GLOBEX, WRITABLE_CONFIGS, mode_a_config

# `user_id` differs too, not just `tenant_id`: a sweep that only varied the tenant could pass
# against a backend that scopes on tenant and ignores every other slot.
ACME = Scope(tenant_id="acme", user_id="ada")

SECRET_ID = "acme-secret-episode"
SECRET_CONTENT = "the acme board approved a confidential acquisition of globex"
SECRET_ENTITY = "Acme Board"


def build(name: str) -> MemoryProvider:
    return build_memory_provider(WRITABLE_CONFIGS[name]())


def graph_backed_provider() -> MemoryProvider:
    """Mode A: the one wiring with both substrates live, for the graph-only attacks below."""
    return build_memory_provider(mode_a_config())


def seed_tenant_a(provider: MemoryProvider) -> None:
    """Tenant A writes one semantic fact everywhere, plus an episode where a provider serves the
    tier. Deliberately disjoint wording between the two: a query built from ``SECRET_CONTENT``
    must not incidentally match the semantic fact and inflate a read that is really testing the
    episode."""
    if provider.tiers()[MemoryTier.EPISODIC]:
        provider.write_tier(
            ACME,
            MemoryTier.EPISODIC,
            [MemoryEvent(id=SECRET_ID, content=SECRET_CONTENT, scope=ACME, entities=[SECRET_ENTITY])],
        )
    provider.write_tier(
        ACME,
        MemoryTier.SEMANTIC,
        [MemoryEvent(content="quarterly earnings figures remain under wraps until Friday", scope=ACME)],
    )
    if provider.substrates().get(Substrate.GRAPH):
        provider.graph.upsert_entities(ACME, [EntityWrite(name=SECRET_ENTITY, entity_type="group", scope=ACME)])


def tenant_a_state(provider: MemoryProvider) -> str:
    """Everything tenant A's own rows look like, as one comparable string.

    Scoped to ``tenant_id == "acme"`` rather than the whole backing store: tenant B legitimately
    creating its own rows under its own tenant partition (the id-collision attempt below does
    exactly that) is not an isolation violation, and a whole-store comparison would flag it as one.
    What must not change is tenant A's own partition -- a refusal that has already written there
    is not fail-closed, and a result-shaped assertion cannot see the difference.
    """
    parts: List[str] = []
    vector = getattr(provider, "vector", None)
    if vector is not None:
        parts.append(repr(sorted(vector.rows.get(ACME.tenant_id, {}).items())))
    graph = getattr(provider, "graph", None)
    if graph is not None:
        parts.append(repr(sorted(r.id for r in graph.rows.get(ACME.tenant_id, []))))
        parts.append(repr(sorted((k, v) for k, v in graph.entities.items() if k[0] == ACME.key())))
    manager = getattr(provider, "manager", None)
    if manager is not None:
        # The seed adapter is pinned single-tenant at construction (SM-C3/SM-C4), so every row it
        # can hold already belongs to tenant A -- there is no foreign-tenant subset to exclude.
        parts.append(repr([m.memory for m in manager.memories]))
    assert parts, "no backing store found on this provider; the snapshot would be vacuously equal"
    return "|".join(parts)


def attempt_every_seam_op_as(provider: MemoryProvider, scope: Scope) -> None:
    """Tenant B tries every seam-level verb against tenant A's known id and content.

    Exceptions are swallowed: an attacker tries everything and does not care what comes back. The
    caller asserts on what was returned and on the state left behind.
    """
    query = MemoryQuery(text=SECRET_CONTENT, limit=10)
    attempts: List[Callable[[], Any]] = [
        lambda: provider.read(scope, query),
        lambda: provider.read_tier(scope, MemoryTier.EPISODIC, query),
        lambda: provider.read_tier(scope, MemoryTier.SEMANTIC, query),
        # Id collision: B writes its own content under A's exact id, hoping to overwrite it.
        lambda: provider.write_tier(
            scope, MemoryTier.EPISODIC, [MemoryEvent(id=SECRET_ID, content="hijacked", scope=scope)]
        ),
        lambda: provider.consolidate(scope),
    ]
    for attempt in attempts:
        try:
            attempt()
        except Exception:
            pass


def attempt_every_graph_op_as(provider: MemoryProvider, scope: Scope) -> None:
    """Tenant B tries every substrate-level graph verb directly -- `provider.graph` is public, so
    the seam's own guard is not the last line an attacker has to clear."""
    query = MemoryQuery(text=SECRET_CONTENT, limit=10)
    attempts: List[Callable[[], Any]] = [
        lambda: provider.graph.traverse(scope, query),
        lambda: provider.graph.recall_episodes(scope, entities=[SECRET_ENTITY], limit=10),
        lambda: provider.graph.bounds_for_ids(scope, [SECRET_ID]),
        lambda: provider.graph.invalidate(scope, SECRET_ID, t_invalid=1),
        lambda: provider.graph.match_entities(
            scope, EntityWrite(name="Board", entity_type="group", scope=scope, embedding=[0.1, 0.2]), limit=5
        ),
        lambda: provider.graph.merge_entities(
            scope,
            [
                EntityMerge(
                    canonical=EntityWrite(name="Board", entity_type="group", scope=scope),
                    duplicate_names=[SECRET_ENTITY],
                )
            ],
        ),
    ]
    for attempt in attempts:
        try:
            attempt()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Reads: tenant B sees nothing of tenant A's content, on every wiring
# ---------------------------------------------------------------------------

#: Wirings that can actually be addressed as two different tenants. ``seed`` cannot: it is pinned
#: single-tenant at construction and refuses ``GLOBEX`` outright before a read or write is
#: attempted (``test_the_seed_provider_refuses_a_foreign_tenant_outright`` below covers that
#: refusal directly), so a test built to check what tenant B *sees* has nothing to observe there.
MULTI_TENANT_CONFIGS = tuple(name for name in sorted(WRITABLE_CONFIGS) if name != "seed")


@pytest.mark.parametrize("name", MULTI_TENANT_CONFIGS)
def test_tenant_b_cannot_read_tenant_as_content_through_the_seam(name):
    provider = build(name)
    seed_tenant_a(provider)

    query = MemoryQuery(text=SECRET_CONTENT, limit=10)
    slice_ = provider.read(GLOBEX, query)
    tier_slice = provider.read_tier(GLOBEX, MemoryTier.EPISODIC, query)

    assert SECRET_CONTENT not in slice_.as_context()
    assert tier_slice == []
    assert all(record.scope.tenant_id == GLOBEX.tenant_id for record in slice_.records)


@pytest.mark.parametrize("name", MULTI_TENANT_CONFIGS)
def test_tenant_b_gets_a_genuinely_empty_read_not_an_error_masking_a_leak(name):
    """A caller that only checks "did it raise" would miss a backend that answered with tenant A's
    row and no exception. The content itself must be absent from what came back."""
    provider = build(name)
    seed_tenant_a(provider)

    slice_ = provider.read(GLOBEX, MemoryQuery(text=SECRET_CONTENT, limit=10))
    for record in slice_.records:
        assert SECRET_CONTENT not in record.content
        assert SECRET_ID != record.id


def test_tenant_b_cannot_traverse_or_recall_tenant_as_graph():
    provider = graph_backed_provider()
    seed_tenant_a(provider)

    hits = provider.graph.traverse(GLOBEX, MemoryQuery(text=SECRET_CONTENT, limit=10))
    recalled = provider.graph.recall_episodes(GLOBEX, entities=[SECRET_ENTITY], limit=10)

    assert hits == []
    assert recalled == []


def test_tenant_b_cannot_match_tenant_as_entity():
    provider = graph_backed_provider()
    seed_tenant_a(provider)

    matches = provider.graph.match_entities(
        GLOBEX,
        EntityWrite(name=SECRET_ENTITY, entity_type="group", scope=GLOBEX, embedding=[0.1, 0.2]),
        limit=5,
        min_score=0.0,
    )

    assert matches == []


# ---------------------------------------------------------------------------
# Writes: tenant B cannot create, overwrite, or fold tenant A's rows
# ---------------------------------------------------------------------------


#: Wirings that serve the EPISODIC tier at all. ``seed`` does not (SM-C2's stores have no episodic
#: leg), so it is excluded here rather than skipped inside the test -- a config that later grows
#: episodic support is picked up by ``provider.tiers()`` reporting it, not by editing this tuple.
EPISODIC_CONFIGS = tuple(
    name
    for name in sorted(WRITABLE_CONFIGS)
    if build_memory_provider(WRITABLE_CONFIGS[name]()).tiers()[MemoryTier.EPISODIC]
)


@pytest.mark.parametrize("name", EPISODIC_CONFIGS)
def test_tenant_b_writing_tenant_as_id_does_not_alter_tenant_as_row(name):
    """The id-collision attack: B writes its own content under A's exact episode id."""
    provider = build(name)
    seed_tenant_a(provider)

    provider.write_tier(GLOBEX, MemoryTier.EPISODIC, [MemoryEvent(id=SECRET_ID, content="hijacked", scope=GLOBEX)])

    a_read = provider.read_tier(ACME, MemoryTier.EPISODIC, MemoryQuery(text=SECRET_CONTENT, limit=10))
    assert [r.id for r in a_read] == [SECRET_ID]
    assert a_read[0].content == SECRET_CONTENT

    b_read = provider.read_tier(GLOBEX, MemoryTier.EPISODIC, MemoryQuery(text="hijacked", limit=10))
    assert [r.id for r in b_read] == [SECRET_ID]
    assert b_read[0].content == "hijacked"
    assert b_read[0].scope.tenant_id == GLOBEX.tenant_id


def test_tenant_b_invalidating_tenant_as_edge_is_a_no_op():
    provider = graph_backed_provider()
    seed_tenant_a(provider)

    provider.graph.invalidate(GLOBEX, SECRET_ID, t_invalid=1)

    still_current = provider.graph.traverse(ACME, MemoryQuery(text=SECRET_CONTENT, limit=10))
    assert [r.id for r in still_current] == [SECRET_ID]
    assert still_current[0].expired_at is None


def test_tenant_b_cannot_merge_tenant_as_entity_into_its_own():
    provider = graph_backed_provider()
    seed_tenant_a(provider)

    provider.graph.merge_entities(
        GLOBEX,
        [
            EntityMerge(
                canonical=EntityWrite(name="Board", entity_type="group", scope=GLOBEX),
                duplicate_names=[SECRET_ENTITY],
            )
        ],
    )

    # A's entity survives under A's scope, untouched by a merge issued under B's.
    assert (ACME.key(), SECRET_ENTITY) in provider.graph.entities
    assert (GLOBEX.key(), SECRET_ENTITY) not in provider.graph.entities


# ---------------------------------------------------------------------------
# The full sweep: tenant A's store, byte for byte, before and after
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
def test_the_full_adversarial_sweep_leaves_tenant_a_byte_for_byte_intact(name):
    provider = build(name)
    seed_tenant_a(provider)
    before = tenant_a_state(provider)

    attempt_every_seam_op_as(provider, GLOBEX)
    if getattr(provider, "graph", None) is not None:
        attempt_every_graph_op_as(provider, GLOBEX)

    assert tenant_a_state(provider) == before


def test_tenant_a_can_still_operate_normally_after_the_sweep():
    """Isolation must not have been achieved by breaking the provider for its rightful tenant."""
    provider = graph_backed_provider()
    seed_tenant_a(provider)

    attempt_every_seam_op_as(provider, GLOBEX)
    attempt_every_graph_op_as(provider, GLOBEX)

    receipt = provider.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="a brand new acme fact", scope=ACME)])
    slice_ = provider.read(ACME, MemoryQuery(text=SECRET_CONTENT, limit=10))

    assert receipt.accepted == 1
    assert SECRET_CONTENT in slice_.as_context()


# ---------------------------------------------------------------------------
# The seed adapter: a structurally different refusal (no tenant predicate to bypass)
# ---------------------------------------------------------------------------


def test_the_seed_provider_refuses_a_foreign_tenant_outright():
    """The seed adapter cannot partition storage by tenant at all (its own docstring says so), so
    it is pinned single-tenant and refuses any other tenant at the boundary -- a different
    isolation strategy from the other three wirings, and one this file has to cover separately
    since ``WRITABLE_CONFIGS`` sweeps above build a fresh ``ACME``-scoped seed each time and never
    call it as ``GLOBEX``."""
    provider = build("seed")

    with pytest.raises(MemoryOperationError) as excinfo:
        provider.read(GLOBEX, MemoryQuery(text="anything", limit=5))
    assert excinfo.value.field_name == "tenant_id"
    assert GLOBEX.tenant_id in str(excinfo.value)

    with pytest.raises(MemoryOperationError) as excinfo:
        provider.write_tier(GLOBEX, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=GLOBEX)])
    assert excinfo.value.field_name == "tenant_id"
