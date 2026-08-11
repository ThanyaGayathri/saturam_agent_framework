"""The config-only swap proof (SM-C1, phase 6).

The seam's whole claim is that changing a backend, or moving a tenant between Mode A and Mode B,
is a config edit. This file is the evidence, and the proof is **structural** rather than a list of
assertions: one function, :func:`consumer_workload`, is run **verbatim** against providers built
from configs that differ only in ``provider`` and ``mode``. It touches nothing but the abstract
``MemoryProvider`` and never branches on which one it got. If the seam leaked, one function could
not drive them all.

The providers are deliberately not variations of one class:

* ``co_located`` -- Mode A, composing both substrate ports.
* ``external`` -- Mode B, same composition, substrates notionally over a network.
* ``seed`` -- an entirely different internal shape: no substrates at all, delegating to
  ``MemoryManager`` and the ``learn`` stores.

**What this does not prove**, stated here so it is not read as more than it is: no provider here
crosses a network, loads an SDK, or incurs real latency. ADR 0003 section 5 makes exactly this
point about the SM-R11 stub and asks SM-C1 to re-prove config-only swap against a **real** external
backend. No such backend exists yet (SM-V1 #95, SM-G1 #99), so that re-proof is carried to
**SM-X2 (#138)**. This establishes seam shape, and no more.
"""

from typing import Any, Dict, List

import pytest

from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope
from egpt.providers.model_provider import ProviderMode
from tests.unit.memory._fakes import FakeGraph, FakeVector
from tests.unit.memory.test_provider_seed_bridge import FakeMachine, FakeManager

ACME = Scope(tenant_id="acme", user_id="u1")


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


def mode_b_config() -> MemoryProviderConfig:
    """Mode B: the same contract, different substrate instances and a different adapter key."""
    return MemoryProviderConfig(
        provider="external",
        mode=ProviderMode.FRONTIER,
        options={"vector_substrate": FakeVector(), "graph_substrate": FakeGraph()},
    )


def seed_config() -> MemoryProviderConfig:
    """A structurally different provider: no substrates, wrapping the seed's own memory."""
    return MemoryProviderConfig(
        provider="seed",
        mode=ProviderMode.LOCAL,
        # The seed cannot separate tenants, so it refuses to build without this claim.
        options={"memory_manager": FakeManager(), "learning_machine": FakeMachine(), "single_tenant": True},
    )


ALL_CONFIGS = {"mode_a": mode_a_config, "mode_b": mode_b_config, "seed": seed_config}


# ---------------------------------------------------------------------------
# The proof
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(ALL_CONFIGS))
def test_one_consumer_function_drives_every_provider_unchanged(name):
    """The structural claim: no call-site edit, whatever the config says."""
    result = consumer_workload(build_memory_provider(ALL_CONFIGS[name]()), ACME)

    assert result["accepted"] == 2
    assert result["tiers_written"] == ["semantic"]
    assert "the customer" in result["context"]
    assert result["consolidated"] == "acme"


@pytest.mark.parametrize("name", sorted(ALL_CONFIGS))
async def test_the_async_contract_is_swappable_too(name):
    result = await aconsumer_workload(build_memory_provider(ALL_CONFIGS[name]()), ACME)

    assert result["accepted"] == 1
    assert "the customer" in result["context"]


def test_switching_mode_changes_nothing_the_consumer_can_see():
    """Mode A and Mode B produce the same observable result for the same workload."""
    mode_a = consumer_workload(build_memory_provider(mode_a_config()), ACME)
    mode_b = consumer_workload(build_memory_provider(mode_b_config()), ACME)

    assert mode_a["context"] == mode_b["context"]
    assert mode_a["accepted"] == mode_b["accepted"]
    assert mode_a["substrates"] == mode_b["substrates"]


def test_the_two_modes_are_genuinely_different_objects():
    """Otherwise the swap above would be proving that one class equals itself."""
    mode_a = build_memory_provider(mode_a_config())
    mode_b = build_memory_provider(mode_b_config())

    assert type(mode_a) is not type(mode_b)
    assert mode_a.provider_key == "co_located"
    assert mode_b.provider_key == "external"


def test_the_seed_provider_is_a_genuinely_different_shape():
    """No substrates at all, yet the same consumer function drives it."""
    seed = build_memory_provider(seed_config())

    assert not any(seed.substrates().values())
    assert all(build_memory_provider(mode_a_config()).substrates().values())


def test_mode_b_is_traceable_as_a_remote_read():
    """A trace that cannot tell a local scan from a network round trip cannot explain latency."""
    mode_a = build_memory_provider(mode_a_config())
    mode_b = build_memory_provider(mode_b_config())
    query = MemoryQuery(text="anything")

    assert "deployment" not in mode_a.read(ACME, query).fusion
    assert mode_b.read(ACME, query).fusion["deployment"] == "external"


def test_mode_b_inherits_every_seam_property_rather_than_reimplementing_them():
    """Mode B must not be a second implementation that drifts from Mode A's guarantees."""
    mode_b = build_memory_provider(mode_b_config())
    mode_b.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="ada works at acme", scope=ACME, id="f1")])

    mode_b.graph.invalidate(ACME, "f1", t_invalid=300, at=310)

    # Same SCD-2 read semantics as Mode A, because it is the same code.
    assert mode_b.read(ACME, MemoryQuery(text="ada acme", as_of=400)).records == []


# ---------------------------------------------------------------------------
# Determinism -- a swap proof that varies run to run proves nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(ALL_CONFIGS))
def test_the_workload_is_deterministic(name):
    runs: List[Dict[str, Any]] = [consumer_workload(build_memory_provider(ALL_CONFIGS[name]()), ACME) for _ in range(3)]

    assert runs[0] == runs[1] == runs[2]


def test_every_in_tree_adapter_is_reachable_by_config():
    """Registration is the swap mechanism; an unregistered adapter is unreachable from config."""
    from egpt.memory.config import registered_providers

    assert {"co_located", "external", "seed"} <= set(registered_providers())
