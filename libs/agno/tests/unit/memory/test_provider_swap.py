"""The config-only swap proof (SM-C1, phase 6).

The seam's whole claim is that changing a backend, or moving a tenant between Mode A and Mode B,
is a config edit. This file is the evidence, and the proof is **structural** rather than a list of
assertions: one function, :func:`consumer_workload`, is run **verbatim** against providers built
from configs that differ only in ``provider`` and ``mode``. It touches nothing but the abstract
``MemoryProvider`` and never branches on which one it got. If the seam leaked, one function could
not drive them all.

The providers are deliberately not variations of one class:

* ``co_located`` -- composing both substrate ports, and each port alone.
* ``seed`` -- an entirely different internal shape: no substrates at all, delegating to
  ``MemoryManager`` and the ``learn`` stores.

**What this does not prove**, stated here so it is not read as more than it is: no provider here
crosses a network, loads an SDK, or incurs real latency, and there is no Mode B adapter -- writing
one against substrates that do not exist would prove only that a class can be subclassed. ADR 0003
section 5 asks SM-C1 to re-prove config-only swap against a **real** external backend; no such
backend exists yet (SM-V1 #95, SM-G1 #99), so both the adapter and that re-proof are
**SM-X2's (#138)**. This establishes seam shape, and no more.
"""

from typing import Any, Dict, List

import pytest

from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError, build_memory_provider
from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import PERSISTENT_TIERS, MemoryTier
from egpt.memory.types import (
    CONSOLIDATION_KEYS,
    FUSION_KEYS,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    PartialWriteError,
    Scope,
    Substrate,
)
from egpt.providers.model_provider import ProviderMode
from tests.unit.memory._fakes import FakeGraph, FakeVector
from tests.unit.memory.test_provider_seed_bridge import FakeMachine, FakeManager

ACME = Scope(tenant_id="acme", user_id="u1")
GLOBEX = Scope(tenant_id="globex", user_id="u1")


def consumer_workload(provider: MemoryProvider, scope: Scope) -> Dict[str, Any]:
    """What a consumer does with memory. Run verbatim against every provider.

    Deliberately typed against the ABC and free of any ``isinstance`` check, provider key, or mode
    branch. Semantic-tier only, because that is the tier every provider serves -- the seed has no
    episodic store, and pretending otherwise would make this a test of the fakes rather than of
    the seam.
    """
    receipt = provider.write_tier(
        scope,
        MemoryTier.SEMANTIC,
        [
            MemoryEvent(content="the customer prefers email over phone", scope=scope),
            MemoryEvent(content="the customer is on the enterprise plan", scope=scope),
        ],
    )
    report = provider.consolidate(scope)
    # `episodic_scanned` is 0 for every provider here and that is correct, not inert: the seed has
    # no episodic store at all, and the workload writes SEMANTIC because that is the only tier all
    # three serve. The episodic consolidation path is covered directly in
    # test_provider_co_located.py, where episodes actually exist.
    slice_ = provider.read(scope, MemoryQuery(text="the customer", tiers=[MemoryTier.SEMANTIC]))

    return {
        "accepted": receipt.accepted,
        "context": slice_.as_context(),
        "tiers_written": [t.value for t in receipt.tiers_written],
        "substrates": {s.value: wired for s, wired in provider.substrates().items()},
        "consolidated": report.scope.tenant_id,
    }


async def aconsumer_workload(provider: MemoryProvider, scope: Scope) -> Dict[str, Any]:
    """The async half of the same contract, also run verbatim against every provider."""
    receipt = await provider.awrite_tier(
        scope,
        MemoryTier.SEMANTIC,
        [MemoryEvent(content="the customer prefers email over phone", scope=scope)],
    )
    await provider.aconsolidate(scope)
    slice_ = await provider.aread(scope, MemoryQuery(text="the customer", tiers=[MemoryTier.SEMANTIC]))

    return {"accepted": receipt.accepted, "context": slice_.as_context()}


def mode_a_config() -> MemoryProviderConfig:
    """Mode A: co-located substrates, both wired."""
    return MemoryProviderConfig(
        provider="co_located",
        mode=ProviderMode.LOCAL,
        options={"vector_substrate": FakeVector(), "graph_substrate": FakeGraph()},
    )


def seed_config() -> MemoryProviderConfig:
    """A structurally different provider: no substrates, wrapping the seed's own memory."""
    return MemoryProviderConfig(
        provider="seed",
        mode=ProviderMode.LOCAL,
        # The seed cannot separate tenants, so it refuses to build without both the claim and the
        # pin -- an acknowledgement with no pin is an unprotected provider.
        options={
            "memory_manager": FakeManager(),
            "learning_machine": FakeMachine(),
            "single_tenant": True,
            "tenant_id": ACME.tenant_id,
        },
    )


def vector_only_config() -> MemoryProviderConfig:
    """A single-substrate co-located provider.

    `ALL_CONFIGS` had none, which is why `test_every_provider_treats_an_unavailable_substrate_the
    _same_way` stated the rule correctly and still missed a violation: with both substrates wired
    there is no unwired substrate to name. Five of one round's twelve majors were an invariant
    fixed on one path and not the adjacent one, and a configuration matrix with a hole in it is
    how that class survives review.
    """
    return MemoryProviderConfig(
        provider="co_located", mode=ProviderMode.LOCAL, options={"vector_substrate": FakeVector()}
    )


def graph_only_config() -> MemoryProviderConfig:
    return MemoryProviderConfig(
        provider="co_located", mode=ProviderMode.LOCAL, options={"graph_substrate": FakeGraph()}
    )


ALL_CONFIGS = {
    "mode_a": mode_a_config,
    "seed": seed_config,
    "vector_only": vector_only_config,
    "graph_only": graph_only_config,
}

#: Providers that can serve the SEMANTIC tier, which is what the shared workload writes.
#: `graph_only` cannot: SUBSTRATES_BY_TIER routes procedural to vector, and consolidate needs a
#: vector leg to enumerate episodes. It still participates in every *refusal* property below.
WRITABLE_CONFIGS = {k: v for k, v in ALL_CONFIGS.items() if k != "graph_only"}


# ---------------------------------------------------------------------------
# The proof
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
def test_one_consumer_function_drives_every_provider_unchanged(name):
    """The structural claim: no call-site edit, whatever the config says."""
    result = consumer_workload(build_memory_provider(WRITABLE_CONFIGS[name]()), ACME)

    assert result["accepted"] == 2
    assert result["tiers_written"] == ["semantic"]
    assert "the customer" in result["context"]
    assert result["consolidated"] == "acme"


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
async def test_the_async_contract_is_swappable_too(name):
    result = await aconsumer_workload(build_memory_provider(WRITABLE_CONFIGS[name]()), ACME)

    assert result["accepted"] == 1
    assert "the customer" in result["context"]


def test_the_seed_provider_is_a_genuinely_different_shape():
    """No substrates at all, yet the same consumer function drives it.

    This is what keeps the swap proof from reducing to "one class equals itself": the two providers
    share no read path, no storage, and no internal shape.
    """
    seed = build_memory_provider(seed_config())

    assert not any(seed.substrates().values())
    assert all(build_memory_provider(mode_a_config()).substrates().values())


def test_the_mode_is_carried_but_selects_no_adapter_yet():
    """``FRONTIER`` is accepted and reaches the adapter; nothing branches on it.

    Mode B is SM-X2's (#138). Recording that here keeps a reader from concluding the mode field is
    dead: it is the seam SM-C5 (#93) resolves per tenant, wired and unused rather than absent.
    """
    frontier = build_memory_provider(
        MemoryProviderConfig(
            provider="co_located",
            mode=ProviderMode.FRONTIER,
            options={"vector_substrate": FakeVector(), "graph_substrate": FakeGraph()},
        )
    )

    assert frontier.config.mode is ProviderMode.FRONTIER
    assert consumer_workload(frontier, ACME) == consumer_workload(build_memory_provider(mode_a_config()), ACME)


# ---------------------------------------------------------------------------
# Determinism -- a swap proof that varies run to run proves nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
def test_the_workload_is_deterministic(name):
    runs: List[Dict[str, Any]] = [
        consumer_workload(build_memory_provider(WRITABLE_CONFIGS[name]()), ACME) for _ in range(3)
    ]

    assert runs[0] == runs[1] == runs[2]


def test_every_in_tree_adapter_is_reachable_by_config():
    """Registration is the swap mechanism; an unregistered adapter is unreachable from config."""
    from egpt.memory.config import registered_providers

    assert {"co_located", "seed"} <= set(registered_providers())


# ---------------------------------------------------------------------------
# Cross-adapter conformance matrix
# ---------------------------------------------------------------------------
#
# One property per test, every registered provider, every wiring. This exists because hand-review
# kept finding the same defect shape: an invariant fixed in one adapter and not the adjacent one --
# five of twelve majors in a single round. A matrix closes that class by construction, where
# noticing it does not scale.
#
# Adding a provider or a wiring to ALL_CONFIGS automatically subjects it to every property here.


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
@pytest.mark.parametrize("limit", [0, 1, 3])
def test_no_provider_returns_more_records_than_the_limit(name, limit):
    provider = build_memory_provider(WRITABLE_CONFIGS[name]())
    provider.write_tier(
        ACME,
        MemoryTier.SEMANTIC,
        [MemoryEvent(content=f"the customer note {i}", scope=ACME, tier=MemoryTier.SEMANTIC) for i in range(5)],
    )
    query = MemoryQuery(text="the customer", tiers=[MemoryTier.SEMANTIC], limit=limit)

    # Both bounds. `<=` alone is satisfied by a provider that returns nothing, so it cannot detect
    # the failure it is named for -- five matching records exist, so a non-zero limit must fill.
    expected = min(limit, 5)
    assert len(provider.read(ACME, query).records) == expected
    assert len(provider.read_tier(ACME, MemoryTier.SEMANTIC, query)) == expected


@pytest.mark.parametrize("name", sorted(ALL_CONFIGS))
@pytest.mark.parametrize("substrate", [Substrate.VECTOR, Substrate.GRAPH])
def test_every_provider_serves_or_refuses_a_named_substrate_never_silently_empties(name, substrate):
    """Either serve the query or refuse it. Quietly ignoring the filter is the ruled-out option.

    Three behaviours used to exist for one situation: a vector-only co-located provider returned
    an empty slice for `substrates=[GRAPH]`, the seed refused it, and a graph-wired-but-excluded
    read also refused. The hole was in the fixtures -- `ALL_CONFIGS` had no single-substrate
    provider, so there was no unwired substrate to name.

    Writing this property out also forced the rule to be stated precisely, which it had not been.
    A *wired* substrate can still be refused: excluding the graph when a graph exists is refused
    because the graph is the authority for validity, so answering without it would be wrong rather
    than merely narrow.
    """
    provider = build_memory_provider(ALL_CONFIGS[name]())
    wired = provider.substrates()
    query = MemoryQuery(text="anything", substrates=[substrate])

    names_an_unwired_substrate = not wired[substrate]
    excludes_a_wired_authority = wired[Substrate.GRAPH] and substrate is not Substrate.GRAPH

    if names_an_unwired_substrate or excludes_a_wired_authority:
        with pytest.raises(MemoryOperationError):
            provider.read(ACME, query)
    else:
        provider.read(ACME, query)  # serving it is fine


@pytest.mark.parametrize("name", sorted(ALL_CONFIGS))
def test_every_provider_refuses_an_empty_substrate_set(name):
    """`substrates=[]` is unanswerable, so it must refuse everywhere rather than empty out.

    Left to fall out of the per-substrate checks it produced three behaviours for one input:
    refused with a graph wired, silently empty on a vector-only provider, refused on the seed. The
    matrix above parametrises over single substrates only, which is why `[]` slipped past it.
    """
    provider = build_memory_provider(ALL_CONFIGS[name]())

    with pytest.raises(MemoryOperationError, match="substrate"):
        provider.read(ACME, MemoryQuery(text="anything", substrates=[]))


@pytest.mark.parametrize("name", sorted(ALL_CONFIGS))
def test_every_provider_reports_the_full_fusion_key_contract(name):
    provider = build_memory_provider(ALL_CONFIGS[name]())

    assert set(FUSION_KEYS) <= set(provider.read(ACME, MemoryQuery(text="anything")).fusion)


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
def test_every_provider_reports_the_full_consolidation_key_contract(name):
    """SM-L1's scheduler reads these; disjoint key sets meant a KeyError on the first iteration."""
    provider = build_memory_provider(WRITABLE_CONFIGS[name]())

    assert set(CONSOLIDATION_KEYS) <= set(provider.consolidate(ACME).details)


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
def test_every_provider_refuses_duplicate_ids_in_one_batch(name):
    """A duplicate destroys the first event's content behind a receipt claiming both landed."""
    provider = build_memory_provider(WRITABLE_CONFIGS[name]())

    with pytest.raises(MemoryOperationError, match="[Dd]uplicate"):
        provider.write_tier(
            ACME,
            MemoryTier.SEMANTIC,
            [
                MemoryEvent(content="first", scope=ACME, id="dup", tier=MemoryTier.SEMANTIC),
                MemoryEvent(content="second", scope=ACME, id="dup", tier=MemoryTier.SEMANTIC),
            ],
        )


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
def test_every_provider_refuses_an_event_scoped_to_another_tenant(name):
    provider = build_memory_provider(WRITABLE_CONFIGS[name]())

    with pytest.raises(MemoryOperationError, match="does not match the write scope"):
        provider.write_tier(
            ACME,
            MemoryTier.SEMANTIC,
            [MemoryEvent(content="x", scope=GLOBEX, tier=MemoryTier.SEMANTIC)],
        )


@pytest.mark.parametrize("name", sorted(ALL_CONFIGS))
def test_an_empty_batch_does_not_buy_a_tier_the_provider_would_refuse(name):
    """`write_tier(WORKING, [])` returned a success receipt while one event was refused.

    Reachable as the degenerate case of a caller looping over possibly-empty per-tier groups.
    """
    provider = build_memory_provider(ALL_CONFIGS[name]())

    with pytest.raises(MemoryOperationError):
        provider.write_tier(ACME, MemoryTier.WORKING, [])


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
def test_every_provider_refuses_a_tier_set_consolidate_would_ignore(name):
    provider = build_memory_provider(WRITABLE_CONFIGS[name]())

    with pytest.raises(MemoryOperationError):
        provider.consolidate(ACME, tiers=[MemoryTier.PROCEDURAL])


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
def test_every_provider_returns_records_scoped_to_the_caller(name):
    """The post-condition every adapter must hold, whatever its storage does underneath."""
    provider = build_memory_provider(WRITABLE_CONFIGS[name]())
    provider.write_tier(
        ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="a customer note", scope=ACME, tier=MemoryTier.SEMANTIC)]
    )

    records = provider.read(ACME, MemoryQuery(text="a customer note")).records

    assert all(r.scope.tenant_id == ACME.tenant_id for r in records)


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
def test_every_provider_raises_partial_write_error_when_a_backend_fails_mid_write_tier(name):
    """`write_tier` is the one verb the matrix never drove to failure, which is why it diverged.

    `seed.write_tier` let the backend's raw exception through while `co_located.write_tier` wrapped
    it, so the same failure reached a caller as two different types depending on the verb.
    """
    provider = build_memory_provider(WRITABLE_CONFIGS[name]())

    def explode(*args, **kwargs):
        raise RuntimeError("backend down")

    for target, attribute in (("vector", "upsert"), ("graph", "upsert_facts"), ("manager", "add_user_memory")):
        backend = getattr(provider, target, None)
        if backend is not None:
            setattr(backend, attribute, explode)

    with pytest.raises(PartialWriteError):
        provider.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=ACME)])


@pytest.mark.parametrize("name", sorted(ALL_CONFIGS))
@pytest.mark.parametrize("tier", list(MemoryTier))
def test_reads_of_an_unserved_tier_are_empty_on_every_provider(name, tier):
    """One rule, both entry points: reads report absence, only writes refuse it.

    The same logical request used to get three answers depending on provider and entry point, so
    no caller could write one handler.
    """
    provider = build_memory_provider(ALL_CONFIGS[name]())

    # A **tier** question, not a substrate one. Asking `substrates()[VECTOR]` made `served` true
    # for WORKING on every co-located wiring, so the two cells this rule was written for -- the
    # ones where `co_located.read_tier(WORKING)` used to refuse while `seed` returned `[]` -- were
    # the exact cells that skipped. All 16 now assert.
    served = tier in getattr(provider, "_served_tiers", lambda: list(PERSISTENT_TIERS))()
    if served:
        return
    assert provider.read_tier(ACME, tier, MemoryQuery(text="q")) == []
    assert provider.read(ACME, MemoryQuery(text="q", tiers=[tier])).records == []


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
def test_no_provider_claims_a_supersede_it_does_not_perform(name):
    """SM-C1 consolidates; it does not retire. Every provider must report that identically.

    The alternative -- one adapter silently leaving duplicates while another reports a supersede
    it never applied -- is the divergence that produced two rounds of findings. Retirement is
    SM-L3's (#124), and when it lands this assertion is the thing that says so.
    """
    provider = build_memory_provider(WRITABLE_CONFIGS[name]())

    details = provider.consolidate(ACME).details

    assert "facts_superseded" not in details, "a provider is claiming retirement SM-C1 does not do"


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
async def test_the_two_write_halves_persist_the_same_state_when_a_backend_fails(name):
    """Asserts the **committed set**, not just the exception type.

    `gather` without `return_exceptions` propagates the first error without cancelling siblings, so
    the async half committed rows the sync half never attempted -- one verb, two halves, different
    durable state, and nothing in the receipt to tell them apart. Type-only assertions are why that
    survived six rounds, so this compares what actually landed.
    """

    def wire(provider):
        """Fail on the *second* item, so both halves have something committed and something not."""
        state = {"items": 0}

        def tick():
            state["items"] += 1
            if state["items"] == 2:
                raise RuntimeError("backend down")

        graph = getattr(provider, "graph", None)
        if graph is not None:
            original_facts = graph.upsert_facts

            def failing_facts(scope, facts, _original=original_facts):
                # Item at a time, because `upsert_facts` is now one call: failing the batch as a
                # whole commits nothing, and "something committed" -- the state this compares --
                # becomes unreachable. The port allows exactly this partial shape.
                for fact in facts:
                    tick()
                    _original(scope, [fact])

            graph.upsert_facts = failing_facts

        manager = getattr(provider, "manager", None)
        if manager is not None:
            original_add = manager.add_user_memory

            def failing_add(*args, _original=original_add, **kwargs):
                tick()
                return _original(*args, **kwargs)

            manager.add_user_memory = failing_add
        return provider

    def committed(provider):
        vector = getattr(provider, "vector", None)
        graph = getattr(provider, "graph", None)
        manager = getattr(provider, "manager", None)
        return (
            sorted(vector.rows.get("acme", {})) if vector else None,
            sorted(r.id for r in graph.rows.get("acme", [])) if graph else None,
            sorted(m.memory_id for m in manager.added) if manager else None,
        )

    def batch():
        return [MemoryEvent(content=f"note {i}", scope=ACME, id=f"n{i}") for i in range(4)]

    sync_provider = wire(build_memory_provider(WRITABLE_CONFIGS[name]()))
    async_provider = wire(build_memory_provider(WRITABLE_CONFIGS[name]()))

    sync_error = async_error = None
    try:
        sync_provider.write_tier(ACME, MemoryTier.SEMANTIC, batch())
    except Exception as exc:
        sync_error = type(exc)
    try:
        await async_provider.awrite_tier(ACME, MemoryTier.SEMANTIC, batch())
    except Exception as exc:
        async_error = type(exc)

    assert sync_error is async_error
    assert committed(sync_provider) == committed(async_provider)


@pytest.mark.parametrize("name", sorted(WRITABLE_CONFIGS))
async def test_every_provider_agrees_between_its_sync_and_async_halves(name):
    provider = build_memory_provider(WRITABLE_CONFIGS[name]())
    provider.write_tier(
        ACME,
        MemoryTier.SEMANTIC,
        [MemoryEvent(content=f"note {i}", scope=ACME, tier=MemoryTier.SEMANTIC) for i in range(3)],
    )
    query = MemoryQuery(text="note")

    assert [r.id for r in provider.read(ACME, query).records] == [
        r.id for r in (await provider.aread(ACME, query)).records
    ]


def test_a_backend_that_fails_to_construct_surfaces_as_a_config_error():
    """`build_memory_provider`'s contract must not depend on which backend the config named.

    A real graph backend opens a connection during construction, so an unreachable host or a
    rejected password arrives here as a driver exception. Left raw, a caller catching
    `MemoryProviderConfigError` -- the type every other construction failure in this module uses --
    would miss it entirely.
    """
    from egpt.registry import discovery

    class _Unreachable:
        def __init__(self, **_kwargs):
            raise RuntimeError("no route to host")

    discovery.register("graphdb", "unreachable-probe", obj=_Unreachable, override=True)
    try:
        config = MemoryProviderConfig(provider="co_located", graph={"backend": "unreachable-probe"})
        with pytest.raises(MemoryProviderConfigError, match="Could not construct"):
            build_memory_provider(config)
    finally:
        discovery.unregister("graphdb", "unreachable-probe")
