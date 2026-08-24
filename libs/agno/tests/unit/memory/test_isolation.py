"""Adversarial cross-tenant isolation at the memory data layer (SM-X6 #142).

SM-C4 (#92) built the guard; ``test_tenant_scoping.py`` proves it is structurally present on
every adapter and every substrate double. What that file explicitly does not prove, by its own
account, is that a *live* provider actually denies a tenant that tries: "Isolation across two live
tenants on a real backend is not provable here, and this file does not claim it. ... SM-X6's
(#142) adversarial suite" is where that claim gets made.

This file seeds tenant A's memory -- vector and graph both -- then has tenant B attempt every read,
write, and traverse the seam and its substrates expose, on both the sync and async halves, using
tenant A's own ids and content. The assertion is not "the call raised": a call that refuses loudly
and a call that quietly returns nothing are both acceptable, and a call that raises *after*
writing is not. What must hold is that tenant A's stored rows are unreadable and unmodifiable from
tenant B, byte for byte, before and after the whole attempt sweep -- mirroring
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

from typing import Any, Awaitable, Callable, List

import pytest

from egpt.memory.config import build_memory_provider
from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    EntityMerge,
    EntityWrite,
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    Scope,
    Substrate,
)
from tests.unit.memory.test_provider_swap import ALL_CONFIGS, GLOBEX, WRITABLE_CONFIGS

# `user_id` differs too, not just `tenant_id`: a sweep that only varied the tenant could pass
# against a backend that scopes on tenant and ignores every other slot.
ACME = Scope(tenant_id="acme", user_id="ada")

SECRET_ID = "acme-secret-episode"
SECRET_CONTENT = "the acme board approved a confidential acquisition of globex"
SECRET_ENTITY = "Acme Board"
SECRET_FACT_ID = "acme-secret-fact"
#: `Northwind`, not `Acme`: `traverse`'s lexical overlap lowercases and tokenises `.content`, and
#: `SECRET_CONTENT` already contains the token `acme` -- a subject literally named `Acme` would
#: make this fact match every `SECRET_CONTENT` query by token overlap, not by any leak.
FACT_SUBJECT, FACT_PREDICATE, FACT_OBJECT = "Northwind", "owns", "SecretProject"
#: Disjoint wording from `SECRET_CONTENT`/`SEMANTIC_CONTENT`, for the same reason those two are
#: disjoint from each other -- `recall_facts` doesn't rank on text, but nothing stops a future
#: query from being built against `.content` too.
FACT_CONTENT = "Northwind owns SecretProject"
#: Written for every wiring, including ``seed`` (which cannot serve EPISODIC) -- the one string
#: guaranteed present in a genuine snapshot of tenant A's data, whatever the provider's shape.
SEMANTIC_CONTENT = "quarterly earnings figures remain under wraps until Friday"


def build(name: str) -> MemoryProvider:
    return build_memory_provider(WRITABLE_CONFIGS[name]())


def graph_provider(name: str) -> MemoryProvider:
    """A provider from ``ALL_CONFIGS`` rather than ``WRITABLE_CONFIGS``: the graph-specific
    attacks below need every graph-wired config, including ``graph_only`` -- excluded from
    ``WRITABLE_CONFIGS`` for a documented, unrelated reason (``consolidate`` needs a vector leg to
    enumerate episodes) that has nothing to do with whether its graph leg can be attacked."""
    return build_memory_provider(ALL_CONFIGS[name]())


#: Wirings with a graph substrate wired, for the graph-specific attacks below. ``vector_only`` (in
#: ``WRITABLE_CONFIGS``) has no graph leg to attack; ``graph_only`` (excluded from
#: ``WRITABLE_CONFIGS``, see ``graph_provider``) does. Both belong here.
GRAPH_CONFIGS = ("graph_only", "mode_a")


def graph_of(provider: MemoryProvider) -> Any:
    """``provider.graph`` behind a check mypy accepts against a ``MemoryProvider``-typed
    reference, matching ``test_tenant_scoping.py``'s ``getattr(provider, "graph", None)``
    convention rather than a direct attribute access the ABC does not declare."""
    graph = getattr(provider, "graph", None)
    assert graph is not None, "this call requires a graph-backed provider"
    return graph


def vector_of(provider: MemoryProvider) -> Any:
    vector = getattr(provider, "vector", None)
    assert vector is not None, "this call requires a vector-backed provider"
    return vector


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
    provider.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content=SEMANTIC_CONTENT, scope=ACME)])
    graph = getattr(provider, "graph", None)
    if provider.substrates().get(Substrate.GRAPH) and graph is not None:
        graph.upsert_entities(ACME, [EntityWrite(name=SECRET_ENTITY, entity_type="group", scope=ACME)])
        graph.upsert_facts(
            ACME,
            [
                FactWrite(
                    record=MemoryRecord(id=SECRET_FACT_ID, tier=MemoryTier.SEMANTIC, content=FACT_CONTENT, scope=ACME),
                    subject=FACT_SUBJECT,
                    predicate=FACT_PREDICATE,
                    object=FACT_OBJECT,
                )
            ],
        )


def tenant_a_state(provider: MemoryProvider) -> str:
    """Everything tenant A's own rows look like, as one comparable string.

    Filtered to ``record.scope == ACME`` rather than the whole backing store: tenant B
    legitimately creating its own rows under its own tenant partition (the id-collision attempt
    below does exactly that) is not an isolation violation, and a whole-store comparison would
    flag it as one. What must not change is tenant A's own rows -- a refusal that has already
    written there is not fail-closed, and a result-shaped assertion cannot see the difference.

    Scans **every** bucket and filters by scope identity, rather than indexing
    ``vector.rows[ACME.tenant_id]`` directly -- a bucket-collapse bug would make that index return
    an empty dict both before and after an attack, which is vacuously "unchanged" and the
    strongest possible false pass for a test named ``byte_for_byte_intact``. Scanning and
    filtering, like ``_matching`` does in ``_fakes.py``, sees the collapse instead of hiding
    behind it.
    """
    parts: List[str] = []
    vector = getattr(provider, "vector", None)
    if vector is not None:
        mine = sorted(
            (record.id, record.content)
            for bucket in vector.rows.values()
            for record in bucket.values()
            if record.scope == ACME
        )
        parts.append(repr(mine))
    graph = getattr(provider, "graph", None)
    if graph is not None:
        mine_rows = sorted(record.id for rows in graph.rows.values() for record in rows if record.scope == ACME)
        parts.append(repr(mine_rows))
        parts.append(repr(sorted((k, v) for k, v in graph.entities.items() if k[0] == ACME.key())))
    manager = getattr(provider, "manager", None)
    if manager is not None:
        # The seed adapter is pinned single-tenant at construction (SM-C3/SM-C4), so every row it
        # can hold already belongs to tenant A -- there is no foreign-tenant subset to exclude.
        parts.append(repr([m.memory for m in manager.memories]))
    assert parts, "no backing store found on this provider; the snapshot would be vacuously equal"
    state = "|".join(parts)
    # Non-triviality, independent of the scan-vs-index fix above: a silently-failed seed write
    # would also produce a well-formed, empty-of-content snapshot that compares equal before and
    # after. `SEMANTIC_CONTENT` is written by every wiring in `seed_tenant_a`, so its absence means
    # the snapshot captured nothing real, not that nothing changed.
    assert SEMANTIC_CONTENT in state, f"tenant A's own seeded content is missing from its own snapshot: {state!r}"
    return state


# ---------------------------------------------------------------------------
# Attack helpers: every seam verb and every substrate verb, both halves
# ---------------------------------------------------------------------------


def attempt_every_seam_op_as(provider: MemoryProvider, scope: Scope) -> None:
    """Tenant B tries every sync seam-level verb against tenant A's known id and content.

    Exceptions are swallowed: an attacker tries everything and does not care what comes back. The
    caller asserts on what was returned and on the state left behind.
    """
    query = MemoryQuery(text=SECRET_CONTENT, limit=10)
    attempts: List[Callable[[], Any]] = [
        lambda: provider.read(scope, query),
        lambda: provider.read_tier(scope, MemoryTier.EPISODIC, query),
        lambda: provider.read_tier(scope, MemoryTier.SEMANTIC, query),
        # Id collision: B writes its own content under A's exact id, hoping to overwrite it.
        # Both the untiered verb and the tiered one -- they route differently inside every adapter.
        lambda: provider.write(scope, [MemoryEvent(id=SECRET_ID, content="hijacked", scope=scope)]),
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


async def attempt_every_seam_op_as_async(provider: MemoryProvider, scope: Scope) -> None:
    """The async twin: every ``a``-prefixed seam verb, driven for real (awaited), not merely
    constructed. A coroutine built and never awaited runs none of its body, so this is the only
    way to actually exercise the async guard rather than assume it mirrors the sync one."""
    query = MemoryQuery(text=SECRET_CONTENT, limit=10)
    attempts: List[Callable[[], Awaitable[Any]]] = [
        lambda: provider.aread(scope, query),
        lambda: provider.aread_tier(scope, MemoryTier.EPISODIC, query),
        lambda: provider.aread_tier(scope, MemoryTier.SEMANTIC, query),
        lambda: provider.awrite(scope, [MemoryEvent(id=SECRET_ID, content="hijacked-async", scope=scope)]),
        lambda: provider.awrite_tier(
            scope, MemoryTier.EPISODIC, [MemoryEvent(id=SECRET_ID, content="hijacked-async", scope=scope)]
        ),
        lambda: provider.aconsolidate(scope),
    ]
    for attempt in attempts:
        try:
            await attempt()
        except Exception:
            pass


def attempt_every_vector_op_as(provider: MemoryProvider, scope: Scope) -> None:
    """Tenant B tries every substrate-level vector verb directly -- `provider.vector` is public,
    the same reasoning `attempt_every_graph_op_as` documents for the graph leg. A no-op on a
    provider with no vector substrate wired."""
    vector = getattr(provider, "vector", None)
    if vector is None:
        return
    query = MemoryQuery(text=SECRET_CONTENT, limit=10)
    attempts: List[Callable[[], Any]] = [
        lambda: vector.search(scope, query),
        lambda: vector.scan(scope, MemoryTier.EPISODIC),
        lambda: vector.upsert(
            scope, [MemoryRecord(id=SECRET_ID, tier=MemoryTier.EPISODIC, content="hijacked-vector", scope=scope)]
        ),
    ]
    for attempt in attempts:
        try:
            attempt()
        except Exception:
            pass


async def attempt_every_vector_op_as_async(provider: MemoryProvider, scope: Scope) -> None:
    vector = getattr(provider, "vector", None)
    if vector is None:
        return
    query = MemoryQuery(text=SECRET_CONTENT, limit=10)
    attempts: List[Callable[[], Awaitable[Any]]] = [
        lambda: vector.asearch(scope, query),
        lambda: vector.ascan(scope, MemoryTier.EPISODIC),
        lambda: vector.aupsert(
            scope,
            [MemoryRecord(id=SECRET_ID, tier=MemoryTier.EPISODIC, content="hijacked-vector-async", scope=scope)],
        ),
    ]
    for attempt in attempts:
        try:
            await attempt()
        except Exception:
            pass


def attempt_every_graph_op_as(provider: MemoryProvider, scope: Scope) -> None:
    """Tenant B tries every substrate-level graph verb directly -- `provider.graph` is public, so
    the seam's own guard is not the last line an attacker has to clear. A no-op on a provider with
    no graph substrate wired."""
    graph = getattr(provider, "graph", None)
    if graph is None:
        return
    query = MemoryQuery(text=SECRET_CONTENT, limit=10)
    attempts: List[Callable[[], Any]] = [
        lambda: graph.traverse(scope, query),
        lambda: graph.recall_episodes(scope, entities=[SECRET_ENTITY], limit=10),
        lambda: graph.recall_facts(scope, entities=[FACT_SUBJECT], limit=10),
        lambda: graph.bounds_for_ids(scope, [SECRET_ID]),
        lambda: graph.invalidate(scope, SECRET_ID, t_invalid=1),
        lambda: graph.match_entities(
            scope, EntityWrite(name="Board", entity_type="group", scope=scope, embedding=[0.1, 0.2]), limit=5
        ),
        # Id collision on every upsert verb: B writes its own content under A's exact episode,
        # fact, and entity identities, hoping to overwrite them -- the write-side counterpart of
        # `attempt_every_seam_op_as`'s id-collision attempt, one layer down.
        lambda: graph.upsert_episodes(scope, [MemoryEvent(id=SECRET_ID, content="hijacked-episode", scope=scope)]),
        lambda: graph.upsert_facts(
            scope,
            [
                FactWrite(
                    record=MemoryRecord(
                        id=SECRET_FACT_ID, tier=MemoryTier.SEMANTIC, content="hijacked-fact", scope=scope
                    ),
                    subject=FACT_SUBJECT,
                    predicate=FACT_PREDICATE,
                    object=FACT_OBJECT,
                )
            ],
        ),
        lambda: graph.upsert_entities(scope, [EntityWrite(name=SECRET_ENTITY, entity_type="hijacked", scope=scope)]),
        # Kept despite being structurally guaranteed to miss: `FakeGraph.entities` is keyed on
        # `(scope.key(), name)`, so B's merge can only ever target `(GLOBEX.key(), name)` and never
        # collides with A's `(ACME.key(), name)` entry -- this is a regression check on that key
        # shape rather than a live probe of any runtime guard. Left in so a future change that
        # widened the match (e.g. matching on `name` alone) would have this catch it.
        lambda: graph.merge_entities(
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


async def attempt_every_graph_op_as_async(provider: MemoryProvider, scope: Scope) -> None:
    graph = getattr(provider, "graph", None)
    if graph is None:
        return
    query = MemoryQuery(text=SECRET_CONTENT, limit=10)
    attempts: List[Callable[[], Awaitable[Any]]] = [
        lambda: graph.atraverse(scope, query),
        lambda: graph.arecall_episodes(scope, entities=[SECRET_ENTITY], limit=10),
        lambda: graph.arecall_facts(scope, entities=[FACT_SUBJECT], limit=10),
        lambda: graph.abounds_for_ids(scope, [SECRET_ID]),
        lambda: graph.ainvalidate(scope, SECRET_ID, t_invalid=1),
        lambda: graph.amatch_entities(
            scope, EntityWrite(name="Board", entity_type="group", scope=scope, embedding=[0.1, 0.2]), limit=5
        ),
        lambda: graph.aupsert_episodes(
            scope, [MemoryEvent(id=SECRET_ID, content="hijacked-episode-async", scope=scope)]
        ),
        lambda: graph.aupsert_facts(
            scope,
            [
                FactWrite(
                    record=MemoryRecord(
                        id=SECRET_FACT_ID, tier=MemoryTier.SEMANTIC, content="hijacked-fact-async", scope=scope
                    ),
                    subject=FACT_SUBJECT,
                    predicate=FACT_PREDICATE,
                    object=FACT_OBJECT,
                )
            ],
        ),
        lambda: graph.aupsert_entities(scope, [EntityWrite(name=SECRET_ENTITY, entity_type="hijacked", scope=scope)]),
        lambda: graph.amerge_entities(
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
            await attempt()
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
async def test_tenant_b_cannot_aread_tenant_as_content_through_the_seam(name):
    """The async twin: `provider.read`'s guard and `provider.aread`'s are two different code
    paths in every adapter, so a sync-only version of this test cannot see an async-only leak."""
    provider = build(name)
    seed_tenant_a(provider)

    query = MemoryQuery(text=SECRET_CONTENT, limit=10)
    slice_ = await provider.aread(GLOBEX, query)
    tier_slice = await provider.aread_tier(GLOBEX, MemoryTier.EPISODIC, query)

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


@pytest.mark.parametrize("name", GRAPH_CONFIGS)
def test_tenant_b_cannot_traverse_or_recall_tenant_as_graph(name):
    provider = graph_provider(name)
    seed_tenant_a(provider)
    graph = graph_of(provider)

    hits = graph.traverse(GLOBEX, MemoryQuery(text=SECRET_CONTENT, limit=10))
    recalled = graph.recall_episodes(GLOBEX, entities=[SECRET_ENTITY], limit=10)
    recalled_facts = graph.recall_facts(GLOBEX, entities=[FACT_SUBJECT], limit=10)

    assert hits == []
    assert recalled == []
    assert recalled_facts == []


def test_tenant_b_cannot_search_or_scan_tenant_as_vector_rows():
    provider = graph_provider("mode_a")
    seed_tenant_a(provider)
    vector = vector_of(provider)

    hits = vector.search(GLOBEX, MemoryQuery(text=SECRET_CONTENT, limit=10))
    scanned = vector.scan(GLOBEX, MemoryTier.EPISODIC)

    assert hits == []
    assert all(record.id != SECRET_ID for record in scanned)


@pytest.mark.parametrize("name", GRAPH_CONFIGS)
def test_tenant_b_cannot_match_tenant_as_entity(name):
    provider = graph_provider(name)
    seed_tenant_a(provider)
    graph = graph_of(provider)

    matches = graph.match_entities(
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


@pytest.mark.parametrize("name", GRAPH_CONFIGS)
def test_tenant_b_invalidating_tenant_as_edge_is_a_no_op(name):
    provider = graph_provider(name)
    seed_tenant_a(provider)
    graph = graph_of(provider)

    graph.invalidate(GLOBEX, SECRET_ID, t_invalid=1)

    still_current = graph.traverse(ACME, MemoryQuery(text=SECRET_CONTENT, limit=10))
    assert [r.id for r in still_current] == [SECRET_ID]
    assert still_current[0].expired_at is None


@pytest.mark.parametrize("name", GRAPH_CONFIGS)
def test_tenant_b_cannot_merge_tenant_as_entity_into_its_own(name):
    provider = graph_provider(name)
    seed_tenant_a(provider)
    graph = graph_of(provider)

    graph.merge_entities(
        GLOBEX,
        [
            EntityMerge(
                canonical=EntityWrite(name="Board", entity_type="group", scope=GLOBEX),
                duplicate_names=[SECRET_ENTITY],
            )
        ],
    )

    # A's entity survives under A's scope, untouched by a merge issued under B's. As
    # `attempt_every_graph_op_as` notes, this is a regression check on the entities dict's key
    # shape (`(scope.key(), name)`) rather than a live probe: B's merge cannot address A's key.
    assert (ACME.key(), SECRET_ENTITY) in graph.entities
    assert (GLOBEX.key(), SECRET_ENTITY) not in graph.entities


# ---------------------------------------------------------------------------
# The full sweep: tenant A's store, byte for byte, before and after
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", MULTI_TENANT_CONFIGS)
def test_the_full_adversarial_sweep_leaves_tenant_a_byte_for_byte_intact(name):
    """``MULTI_TENANT_CONFIGS`` here, not ``WRITABLE_CONFIGS``: ``seed``'s ``ACME``/``GLOBEX``
    attempt differs in ``user_id`` as well as ``tenant_id``, and the seed adapter's own docstring
    says its storage partitions on ``user_id`` alone. Including it here would make the sweep's pass
    for that wiring look like proof of the tenant guard when it is at least as plausibly delivered
    by the incidental ``user_id`` difference underneath -- the tenant guard itself is proven
    directly, and without that confound, by ``test_the_seed_provider_refuses_a_foreign_tenant_
    outright`` below."""
    provider = build(name)
    seed_tenant_a(provider)
    before = tenant_a_state(provider)

    attempt_every_seam_op_as(provider, GLOBEX)
    attempt_every_vector_op_as(provider, GLOBEX)
    attempt_every_graph_op_as(provider, GLOBEX)

    assert tenant_a_state(provider) == before


@pytest.mark.parametrize("name", MULTI_TENANT_CONFIGS)
async def test_the_full_adversarial_sweep_leaves_tenant_a_byte_for_byte_intact_on_the_async_half(name):
    """The async twin of the sweep above, over the ``a``-prefixed seam verbs and substrate
    methods. Not "an async twin exists" -- ``_fakes.py``'s own docstring records a prior sync/async
    divergence in this exact seam that went unnoticed for six review rounds, which is what makes
    driving the async half here more than a formality."""
    provider = build(name)
    seed_tenant_a(provider)
    before = tenant_a_state(provider)

    await attempt_every_seam_op_as_async(provider, GLOBEX)
    await attempt_every_vector_op_as_async(provider, GLOBEX)
    await attempt_every_graph_op_as_async(provider, GLOBEX)

    assert tenant_a_state(provider) == before


@pytest.mark.parametrize("name", MULTI_TENANT_CONFIGS)
def test_tenant_a_can_still_operate_normally_after_the_sweep(name):
    """Isolation must not have been achieved by breaking the provider for its rightful tenant."""
    provider = build(name)
    seed_tenant_a(provider)

    attempt_every_seam_op_as(provider, GLOBEX)
    attempt_every_vector_op_as(provider, GLOBEX)
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
    call it as ``GLOBEX``. This is the guard proven in isolation, unconfounded by ``user_id``: the
    refusal below fires on ``tenant_id`` alone, before any storage-layer logic runs, which is what
    lets ``test_the_full_adversarial_sweep...`` above exclude ``seed`` without losing coverage of
    this property."""
    provider = build("seed")

    with pytest.raises(MemoryOperationError) as excinfo:
        provider.read(GLOBEX, MemoryQuery(text="anything", limit=5))
    assert excinfo.value.field_name == "tenant_id"
    assert GLOBEX.tenant_id in str(excinfo.value)

    with pytest.raises(MemoryOperationError) as excinfo:
        provider.write_tier(GLOBEX, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=GLOBEX)])
    assert excinfo.value.field_name == "tenant_id"
