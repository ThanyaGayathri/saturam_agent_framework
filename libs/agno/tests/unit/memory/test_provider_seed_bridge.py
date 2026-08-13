"""The seed bridge: existing memory behind the new seam (SM-C1, phase 5).

ADR 0003 §2 decided WRAP, never replace, so the load-bearing assertions here are about what the
bridge does *not* do: it does not mutate the seed, it does not invent an episodic store, and it
does not claim substrates it has no access to.
"""

from typing import Any, Dict, List, Optional

import pytest

from egpt.db.schemas import UserMemory
from egpt.memory.adapters.seed import SeedMemoryProvider
from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError, build_memory_provider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryOperationError, MemoryQuery, Scope, Substrate

ACME = Scope(tenant_id="acme", user_id="u1")
GLOBEX = Scope(tenant_id="globex", user_id="u1")

#: A scope narrowed below ``user_id``. The manager path cannot *filter* on these slots, only record
#: them, so reads under this scope are refused while writes carry it through.
ACME_AGENT = Scope(tenant_id="acme", user_id="u1", agent_id="a1")


def seed_config(**options) -> MemoryProviderConfig:
    """A **safe** seed configuration: single-tenant acknowledged *and* the tenant pinned.

    Both keys are here deliberately. An earlier version of this helper supplied only
    `single_tenant`, which meant the unpinned -- and unprotected -- configuration was the one the
    whole suite exercised, while the pinned path had a single test. A shared helper that builds the
    unsafe shape is how a tenancy regression stays green: the tests document the safe path and
    run the unsafe one.
    """
    return MemoryProviderConfig(
        provider="seed", options={"single_tenant": True, "tenant_id": ACME.tenant_id, **options}
    )


class FakeManager:
    """Stands in for MemoryManager: the same surface, none of the db or model."""

    def __init__(self, memories: Optional[List[UserMemory]] = None, owner: str = "u1") -> None:
        # Seeded rows belong to `owner`. The real manager partitions on user_id, so a double whose
        # rows belong to nobody cannot show a per-user leak -- and the "default" pool leak lived in
        # exactly that blind spot.
        self.memories = [m for m in (memories or [])]
        for memory in self.memories:
            memory.user_id = memory.user_id or owner
        self.added: List[UserMemory] = []
        self.search_calls: List[Dict[str, Any]] = []

    def search_user_memories(self, query=None, limit=None, retrieval_method=None, user_id=None):
        self.search_calls.append(
            {"query": query, "limit": limit, "retrieval_method": retrieval_method, "user_id": user_id}
        )
        # Filtered on user_id, like the real manager -- which also substitutes "default" for None.
        # Ignoring it made the shared-"default"-pool leak unobservable by any test.
        pool = user_id or "default"
        matching = [m for m in self.memories if (m.user_id or "default") == pool]
        return matching[:limit] if limit else matching

    def add_user_memory(self, memory: UserMemory, user_id=None):
        memory.memory_id = memory.memory_id or f"m{len(self.added)}"
        memory.user_id = user_id or "default"  # the real manager's substitution
        self.added.append(memory)
        # Also readable: the real manager persists, so a double where a write is invisible to the
        # next read would let a round-trip bug pass.
        self.memories.append(memory)
        return memory.memory_id


class FakeStore:
    """Stands in for a LearningStore. Records what it was asked, so wrapping can be verified."""

    def __init__(self, learning_type: str = "user_profile", recalled: Any = "profile data") -> None:
        self._type = learning_type
        self._recalled = recalled
        self.processed: List[List[Dict[str, str]]] = []
        self.recall_kwargs: List[Dict[str, Any]] = []

    @property
    def learning_type(self) -> str:
        return self._type

    @property
    def schema(self) -> Any:
        return dict

    @property
    def was_updated(self) -> bool:
        return bool(self.processed)

    def recall(self, **kwargs):
        self.recall_kwargs.append(kwargs)
        return self._recalled

    async def arecall(self, **kwargs):
        return self.recall(**kwargs)

    def process(self, messages, **kwargs):
        self.processed.append(list(messages))

    async def aprocess(self, messages, **kwargs):
        self.process(messages, **kwargs)

    def build_context(self, data):
        return "" if data is None else f"[{self._type}] {data}"

    def get_tools(self, **kwargs):
        return []

    async def aget_tools(self, **kwargs):
        return []


class FakeCurator:
    def __init__(self) -> None:
        self.calls: List[str] = []

    def deduplicate(self, user_id: str) -> int:
        self.calls.append(user_id)
        return 3


class FakeMachine:
    def __init__(self) -> None:
        self._curator = FakeCurator()

    @property
    def curator(self) -> FakeCurator:
        return self._curator


# ---------------------------------------------------------------------------
# The doubles must match the real seed, or none of the rest proves anything
# ---------------------------------------------------------------------------


def test_the_store_double_satisfies_the_real_learning_store_protocol():
    from egpt.learn.stores.protocol import LearningStore

    assert isinstance(FakeStore(), LearningStore)


@pytest.mark.parametrize(
    "method,params",
    [
        ("search_user_memories", {"query", "limit", "retrieval_method", "user_id"}),
        ("add_user_memory", {"memory", "user_id"}),
    ],
)
def test_the_bridge_calls_memory_manager_with_parameters_it_actually_has(method, params):
    """Pins the real signatures the bridge depends on, so seed drift fails here, not in production."""
    import inspect

    from egpt.memory.manager import MemoryManager

    actual = set(inspect.signature(getattr(MemoryManager, method)).parameters) - {"self"}

    assert params <= actual, f"MemoryManager.{method} no longer accepts {params - actual}"


@pytest.mark.parametrize("sync_only", ["search_user_memories", "add_user_memory"])
def test_the_sync_only_manager_methods_are_still_sync_only(sync_only):
    """The reason those async paths hop a thread.

    If this fails, the seed grew a real async variant -- which is good news, and the bridge should
    switch to awaiting it instead of paying for a thread hop.
    """
    from egpt.memory.manager import MemoryManager

    assert not hasattr(MemoryManager, "a" + sync_only)


def test_the_curator_is_still_narrower_than_the_adr_implies():
    """ADR §2.1 says "REUSE Curator inside consolidate"; its own docstring scopes it to one store.

    Wired as a dedup hook rather than a general engine because of this. If Curator grows async
    variants or broader coverage, revisit -- SM-L4 (#125) owns that.
    """
    from egpt.learn.curate import Curator

    assert "user_profile store only" in (Curator.__doc__ or "")
    assert not hasattr(Curator, "adeduplicate")


# ---------------------------------------------------------------------------
# Construction and honesty about capability
# ---------------------------------------------------------------------------


def test_a_bridge_with_nothing_to_wrap_is_a_misconfiguration():
    with pytest.raises(MemoryProviderConfigError, match="needs a MemoryManager"):
        SeedMemoryProvider()


def test_the_bridge_reports_no_substrates_because_the_seed_has_none():
    """Not a defect: the seed has no vector-search-over-memory and no temporal graph.

    A caller must be able to tell "there is no graph" from "the graph found nothing".
    """
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    assert bridge.substrates() == {Substrate.VECTOR: False, Substrate.GRAPH: False}


def test_the_bridge_self_registers():
    built = build_memory_provider(seed_config(memory_manager=FakeManager()))

    assert isinstance(built, SeedMemoryProvider)


# ---------------------------------------------------------------------------
# MemoryManager wrapping
# ---------------------------------------------------------------------------


def test_semantic_reads_go_through_the_manager():
    manager = FakeManager([UserMemory(memory="prefers dark mode", memory_id="m1", topics=["ui"])])
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    records = bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="preferences"))

    assert [r.content for r in records] == ["prefers dark mode"]
    assert records[0].tier is MemoryTier.SEMANTIC
    assert records[0].topics == ["ui"]


def test_retrieval_is_recency_and_not_configurable():
    """`last_n` is hardcoded, and that is the point.

    The manager has no embedder, so there is nothing to rank by and no second strategy worth
    choosing. `agentic` -- the only other option MemoryManager offers -- runs an LLM round trip,
    which `MemoryProvider.read` promises not to do. A knob here would be a config surface whose
    only other setting is one the seam forbids.
    """
    manager = FakeManager([UserMemory(memory="x", memory_id="m1")])
    bridge = SeedMemoryProvider(manager=manager, config=seed_config(retrieval_method="agentic"))

    bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q"))

    assert manager.search_calls[0]["retrieval_method"] == "last_n"


def test_the_agentic_method_cannot_be_reached_even_by_configuring_it():
    """Mutation guard: if `retrieval_method` ever becomes readable again, this fails."""
    manager = FakeManager([UserMemory(memory="x", memory_id="m1")])
    SeedMemoryProvider(manager=manager, config=seed_config(retrieval_method="agentic")).read(
        ACME, MemoryQuery(text="q")
    )

    assert all(call["retrieval_method"] == "last_n" for call in manager.search_calls)


def test_the_scope_user_reaches_the_manager():
    manager = FakeManager()
    SeedMemoryProvider(manager=manager, config=seed_config()).read_tier(
        ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q")
    )

    assert manager.search_calls[0]["user_id"] == "u1"


def test_writes_land_as_user_memories_carrying_scope_identity():
    """A write records every slot UserMemory has a column for, including the unfilterable ones."""
    manager = FakeManager()
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    receipt = bridge.write_tier(
        ACME_AGENT,
        MemoryTier.SEMANTIC,
        [MemoryEvent(content="likes concise answers", scope=ACME_AGENT, topics=["style"])],
    )

    assert receipt.accepted == 1
    assert receipt.tiers_written == [MemoryTier.SEMANTIC]
    written = manager.added[0]
    assert written.memory == "likes concise answers"
    assert written.topics == ["style"]
    assert written.user_id == "u1"
    assert written.agent_id == "a1"


def test_a_user_memory_projects_with_bi_temporal_fields_unset():
    """UserMemory has no temporal columns until SM-C3; None reads as 'current, always valid'."""
    manager = FakeManager([UserMemory(memory="x", memory_id="m1")])
    record = SeedMemoryProvider(manager=manager, config=seed_config()).read_tier(
        ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q")
    )[0]

    assert (record.t_valid, record.t_invalid, record.expired_at) == (None, None, None)
    assert record.is_current_belief() is True


# ---------------------------------------------------------------------------
# LearningStore wrapping
# ---------------------------------------------------------------------------


def test_a_bound_store_serves_its_tier_through_recall_and_build_context():
    """ADR §2.1: recall -> read_tier, build_context -> the record's content."""
    store = FakeStore(learning_type="user_profile", recalled="works at acme")
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: store}, config=seed_config())

    records = bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="who is this"))

    assert [r.content for r in records] == ["[user_profile] works at acme"]
    assert records[0].provenance == {"learning_type": "user_profile"}


def test_a_store_recall_receives_the_scope_and_the_query():
    """The bridge *passes* all four slots; what a store does with them is the store's business.

    Not "a store takes all four slots, so this scope is fine" -- that restated a claim which is
    false for every store in the tree (see `_reject_unfilterable_scope`). The bridge's obligation
    is to hand the slots over; honouring them is the store's, and which stores honour which is
    SM-C2's (#90) to pin down.
    """
    store = FakeStore()
    SeedMemoryProvider(stores={MemoryTier.SEMANTIC: store}, config=seed_config()).read_tier(
        ACME_AGENT, MemoryTier.SEMANTIC, MemoryQuery(text="hello")
    )

    kwargs = store.recall_kwargs[0]
    assert kwargs["user_id"] == "u1"
    assert kwargs["agent_id"] == "a1"
    assert kwargs["message"] == "hello" and kwargs["query"] == "hello"


def test_an_empty_context_contributes_no_record():
    """A store with nothing to say must not inject an empty line into the prompt."""
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: FakeStore(recalled=None)}, config=seed_config())

    assert bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q")) == []


def test_writes_to_a_bound_store_go_through_process():
    """ADR §2.1: process -> write_tier."""
    store = FakeStore(learning_type="decision_log")
    bridge = SeedMemoryProvider(stores={MemoryTier.PROCEDURAL: store}, config=seed_config())

    bridge.write_tier(ACME, MemoryTier.PROCEDURAL, [MemoryEvent(content="always confirm", scope=ACME, role="user")])

    assert store.processed == [[{"role": "user", "content": "always confirm"}]]


def test_a_bound_store_takes_precedence_over_the_manager():
    manager, store = FakeManager([UserMemory(memory="from manager", memory_id="m1")]), FakeStore()
    bridge = SeedMemoryProvider(manager=manager, stores={MemoryTier.SEMANTIC: store}, config=seed_config())

    records = bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q"))

    assert records[0].content.startswith("[user_profile]")
    assert manager.search_calls == []


# ---------------------------------------------------------------------------
# The episodic gap, stated rather than papered over
# ---------------------------------------------------------------------------


def test_writing_an_episode_is_refused_not_redirected():
    """Silently writing it to the semantic tier would destroy the raw record consolidation needs."""
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="no episodic store"):
        bridge.write_tier(ACME, MemoryTier.EPISODIC, [MemoryEvent(content="something happened", scope=ACME)])


def test_reading_the_episodic_tier_is_empty_rather_than_an_error():
    """A read of an absent tier is a legitimate 'nothing here', unlike a write."""
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    assert bridge.read_tier(ACME, MemoryTier.EPISODIC, MemoryQuery(text="q")) == []


def test_a_full_read_only_touches_served_tiers():
    manager = FakeManager([UserMemory(memory="semantic thing", memory_id="m1")])
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    result = bridge.read(ACME, MemoryQuery(text="thing"))

    assert set(result.by_tier) == {MemoryTier.SEMANTIC}
    assert result.fusion["method"] is None  # nothing to fuse: no substrates


def test_writing_a_tier_nothing_is_bound_to_is_refused():
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="No seed component is bound"):
        bridge.write_tier(ACME, MemoryTier.PROCEDURAL, [MemoryEvent(content="x", scope=ACME)])


# ---------------------------------------------------------------------------
# Consolidation: what the seed can actually do
# ---------------------------------------------------------------------------


def test_consolidation_runs_the_curator_as_the_dedup_lane():
    machine = FakeMachine()
    bridge = SeedMemoryProvider(manager=FakeManager(), machine=machine, config=seed_config())

    report = bridge.consolidate(ACME)

    assert report.duplicates_merged == 3
    assert machine.curator.calls == ["u1"]


def test_consolidation_reports_zero_for_what_the_seed_cannot_do():
    """No episodic store to scan and no extraction, so claiming otherwise would be a lie."""
    report = SeedMemoryProvider(manager=FakeManager(), machine=FakeMachine(), config=seed_config()).consolidate(ACME)

    assert report.episodic_scanned == 0
    assert report.semantic_written == 0
    assert report.contradictions_resolved == 0
    assert report.details["derived_by"] == "seed.curator_dedup"


def test_consolidation_without_a_machine_is_a_no_op_not_a_crash():
    report = SeedMemoryProvider(manager=FakeManager(), config=seed_config()).consolidate(ACME)

    assert report.duplicates_merged == 0


# ---------------------------------------------------------------------------
# Async paths
# ---------------------------------------------------------------------------


async def test_async_store_reads_use_the_stores_own_async_variant():
    """LearningStore declares arecall, so no thread hop is needed for the store path."""
    store = FakeStore(recalled="async data")
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: store}, config=seed_config())

    records = await bridge.aread_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q"))

    assert records[0].content == "[user_profile] async data"


async def test_async_manager_reads_agree_with_sync():
    """search_user_memories is sync-only, so this path hops a thread rather than blocking."""
    manager = FakeManager([UserMemory(memory="x", memory_id="m1")])
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())
    query = MemoryQuery(text="q")

    sync = bridge.read_tier(ACME, MemoryTier.SEMANTIC, query)
    asynchronous = await bridge.aread_tier(ACME, MemoryTier.SEMANTIC, query)

    assert [r.content for r in sync] == [r.content for r in asynchronous]


async def test_async_writes_reach_the_manager():
    manager = FakeManager()
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    receipt = await bridge.awrite_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="async memory", scope=ACME)])

    assert receipt.accepted == 1
    assert manager.added[0].memory == "async memory"


async def test_async_store_writes_use_aprocess():
    store = FakeStore()
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: store}, config=seed_config())

    await bridge.awrite_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="note", scope=ACME)])

    assert store.processed == [[{"role": "user", "content": "note"}]]


async def test_async_consolidation_agrees_with_sync():
    bridge = SeedMemoryProvider(manager=FakeManager(), machine=FakeMachine(), config=seed_config())

    assert (await bridge.aconsolidate(ACME)).duplicates_merged == 3


# ---------------------------------------------------------------------------
# Regressions from the PR #60 review
# ---------------------------------------------------------------------------


def test_the_bridge_refuses_to_build_without_a_single_tenant_claim():
    """Critical 1. The seed cannot honour Scope.tenant_id, so it must not pretend to.

    UserMemory has no tenant column and MemoryManager partitions on user_id alone. Serving the
    call anyway leaked across tenants *and* relabelled the row with the reader's tenant, so the
    leak was invisible to every check above this layer.
    """
    with pytest.raises(MemoryProviderConfigError, match="cannot enforce tenant isolation"):
        SeedMemoryProvider(manager=FakeManager())


def test_the_seed_bridge_never_crosses_tenants():
    """Critical 1, the repro from the review, now refused rather than served.

    Same user_id, two tenants. Previously tenant B read tenant A's memory back, stamped with
    tenant B's scope.
    """
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config(tenant_id="acme"))
    bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="acme Q4 revenue is 12M", scope=ACME)])

    with pytest.raises(MemoryOperationError, match="single_tenant"):
        bridge.read_tier(GLOBEX, MemoryTier.SEMANTIC, MemoryQuery(text="revenue"))


@pytest.mark.parametrize("slot", ["agent_id", "team_id", "session_id"])
def test_a_manager_read_refuses_a_scope_it_cannot_filter_on(slot):
    """The tenant leak's shape, one level down: `search_user_memories` takes only `user_id`.

    Without this the read returns every agent's memories for the user and the projection stamps the
    requesting agent onto each one, so the over-broad answer looks correctly narrow -- invisible to
    every check above this layer, exactly as the cross-tenant leak was.
    """
    manager = FakeManager([UserMemory(memory="another agent's note", memory_id="m1")])
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())
    narrow = Scope(tenant_id="acme", user_id="u1", **{slot: "x1"})

    with pytest.raises(MemoryOperationError, match="filters on user_id alone") as exc:
        bridge.read_tier(narrow, MemoryTier.SEMANTIC, MemoryQuery(text="q"))

    assert exc.value.field_name == slot
    assert manager.search_calls == [], "refused before the manager was ever asked"


def test_a_narrow_scope_is_still_recorded_on_write():
    """Refusing the *read* must not silently start dropping columns on the write."""
    manager = FakeManager()
    SeedMemoryProvider(manager=manager, config=seed_config()).write_tier(
        ACME_AGENT, MemoryTier.SEMANTIC, [MemoryEvent(content="note", scope=ACME_AGENT)]
    )

    assert manager.added[0].agent_id == "a1"


def test_a_default_tier_write_is_refused_rather_than_redirected():
    """Critical 2. MemoryEvent defaults to EPISODIC and write() used to force the batch to SEMANTIC.

    The test named for this refusal previously exercised only the explicit write_tier path, so it
    passed green while the default path -- the one every caller reaches for -- did the redirect.
    """
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())
    event = MemoryEvent(content="something happened", scope=ACME)
    assert event.tier is MemoryTier.EPISODIC

    with pytest.raises(MemoryOperationError, match="no episodic store"):
        bridge.write(ACME, [event])


async def test_a_default_tier_async_write_is_refused_too():
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="no episodic store"):
        await bridge.awrite(ACME, [MemoryEvent(content="something happened", scope=ACME)])


def test_a_mixed_batch_routes_each_event_to_its_own_tier():
    """write() groups by tier, so a semantic event still lands even though it shared a batch."""
    manager = FakeManager()
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    receipt = bridge.write(ACME, [MemoryEvent(content="a preference", scope=ACME, tier=MemoryTier.SEMANTIC)])

    assert receipt.tiers_written == [MemoryTier.SEMANTIC]
    assert manager.added[0].memory == "a preference"


def test_a_point_in_time_read_is_refused_rather_than_answered_from_present_day_memory():
    """as_of was accepted and ignored: UserMemory has no valid-time columns."""
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="point-in-time"):
        bridge.read(ACME, MemoryQuery(text="q", as_of=100))


def test_the_fusion_payload_carries_every_contract_key():
    """Major 13. A provider-agnostic consumer KeyError'd on `seed` because keys diverged."""
    from egpt.memory.types import FUSION_KEYS

    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    fusion = bridge.read(ACME, MemoryQuery(text="q")).fusion

    assert set(FUSION_KEYS) <= set(fusion)
    assert fusion["vector_hits"] == 0 and fusion["graph_hits"] == 0  # honest: no legs, not unknown


def test_a_store_write_reports_no_event_ids_because_process_mints_none():
    """`accepted` used to claim every event landed and invent a tier; process() returns None."""
    store = FakeStore()
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: store}, config=seed_config())

    receipt = bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="note", scope=ACME)])

    assert receipt.event_ids == []
    assert receipt.accepted == 1  # the store reported was_updated


def test_event_metadata_and_role_survive_a_store_write():
    """Both were dropped while the receipt reported the event accepted."""
    store = FakeStore()
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: store}, config=seed_config())

    bridge.write_tier(
        ACME,
        MemoryTier.SEMANTIC,
        [MemoryEvent(content="note", scope=ACME, role="assistant", metadata={"source": "email"}, occurred_at=100)],
    )

    message = store.processed[0][0]
    assert message["role"] == "assistant"
    assert message["metadata"] == {"source": "email"}
    assert message["occurred_at"] == 100


def test_event_metadata_survives_a_manager_write():
    manager = FakeManager()
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    bridge.write_tier(
        ACME,
        MemoryTier.SEMANTIC,
        [MemoryEvent(content="note", scope=ACME, role="assistant", metadata={"source": "email"})],
    )

    assert "role=assistant" in (manager.added[0].input or "")
    assert "source" in (manager.added[0].input or "")


def test_a_multi_tier_read_does_not_let_the_first_tier_crowd_out_the_rest():
    """Each tier was asked for `limit` records and then the slice capped at `limit`."""
    manager = FakeManager([UserMemory(memory=f"semantic {i}", memory_id=f"m{i}") for i in range(5)])
    bridge = SeedMemoryProvider(
        manager=manager, stores={MemoryTier.PROCEDURAL: FakeStore("decision_log")}, config=seed_config()
    )

    result = bridge.read(ACME, MemoryQuery(text="q", limit=3))

    assert MemoryTier.PROCEDURAL in result.by_tier


def test_consolidation_says_whether_the_curator_actually_ran():
    """duplicates_merged=0 could not distinguish a clean pass from a no-op."""
    ran = SeedMemoryProvider(manager=FakeManager(), machine=FakeMachine(), config=seed_config()).consolidate(ACME)
    skipped = SeedMemoryProvider(manager=FakeManager(), config=seed_config()).consolidate(ACME)

    assert ran.details["ran"] is True
    assert skipped.details["ran"] is False
    assert skipped.details["skipped_because"] == "no LearningMachine wired"


def test_consolidate_refuses_a_tier_set_it_would_ignore():
    """Major 6. The parameter was accepted and never read, in every adapter."""
    bridge = SeedMemoryProvider(manager=FakeManager(), machine=FakeMachine(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="consolidate"):
        bridge.consolidate(ACME, tiers=[MemoryTier.PROCEDURAL])


# ---------------------------------------------------------------------------
# Round-2 review regressions
# ---------------------------------------------------------------------------


def test_the_tenant_pin_comes_from_config_not_from_whoever_calls_first():
    """A per-instance lazy pin let two workers each pin their own tenant and serve it happily.

    `build_memory_provider` runs per process, so that is the ordinary deployment shape rather than
    a corner. Declaring the tenant makes the pin a deployment fact, identical in every worker.
    """
    first = SeedMemoryProvider(manager=FakeManager(), config=seed_config(tenant_id="acme"))
    second = SeedMemoryProvider(manager=FakeManager(), config=seed_config(tenant_id="acme"))

    for worker in (first, second):
        with pytest.raises(MemoryOperationError, match="single_tenant"):
            worker.read_tier(GLOBEX, MemoryTier.SEMANTIC, MemoryQuery(text="q"))


def test_a_declared_tenant_must_be_a_non_empty_string():
    with pytest.raises(MemoryProviderConfigError, match="tenant_id"):
        SeedMemoryProvider(manager=FakeManager(), config=seed_config(tenant_id="  "))


def test_a_mixed_batch_commits_nothing_when_any_group_is_unservable():
    """Blocking 4. write_tier validated its own group *after* earlier groups had committed.

    So a batch's atomicity depended on the caller's list order, and the committed row's id was
    never returned.
    """
    manager = FakeManager()
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())
    batch = [
        MemoryEvent(content="a preference", scope=ACME, tier=MemoryTier.SEMANTIC),
        MemoryEvent(content="something happened", scope=ACME),  # EPISODIC, unservable
    ]

    with pytest.raises(MemoryOperationError, match="no episodic store"):
        bridge.write(ACME, batch)

    assert manager.added == [], "the semantic half must not have committed"


def test_the_reversed_batch_behaves_identically():
    """Order-dependent atomicity was the sharpest form of the bug."""
    manager = FakeManager()
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())
    batch = [
        MemoryEvent(content="something happened", scope=ACME),
        MemoryEvent(content="a preference", scope=ACME, tier=MemoryTier.SEMANTIC),
    ]

    with pytest.raises(MemoryOperationError):
        bridge.write(ACME, batch)

    assert manager.added == []


async def test_the_async_mixed_batch_also_commits_nothing():
    manager = FakeManager()
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    with pytest.raises(MemoryOperationError):
        await bridge.awrite(
            ACME,
            [
                MemoryEvent(content="a preference", scope=ACME, tier=MemoryTier.SEMANTIC),
                MemoryEvent(content="something happened", scope=ACME),
            ],
        )

    assert manager.added == []


@pytest.mark.parametrize("substrates", [[Substrate.GRAPH], [Substrate.VECTOR], []])
def test_naming_a_substrate_the_seed_does_not_have_is_refused(substrates):
    """Major 6. Ignoring `substrates` made the same query mean two things on two providers."""
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="no substrates"):
        bridge.read(ACME, MemoryQuery(text="q", substrates=substrates))


def test_read_tier_honours_limit_including_zero():
    """Major 7. MemoryManager truncates only when limit > 0, so limit=0 returned the whole table."""
    manager = FakeManager([UserMemory(memory=f"m{i}", memory_id=f"m{i}") for i in range(25)])
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    assert bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q", limit=0)) == []
    assert len(bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q", limit=3))) == 3


def test_a_read_of_an_unserved_tier_still_runs_the_guards():
    """The guards lived only in read_tier, so a read naming no served tier skipped them."""
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="point-in-time"):
        bridge.read(ACME, MemoryQuery(text="q", tiers=[MemoryTier.PROCEDURAL], as_of=999))


def test_consolidate_refuses_the_episodic_tier_the_seed_cannot_serve():
    """The guard was copy-adapted from co-located, where the polarity is the opposite."""
    bridge = SeedMemoryProvider(manager=FakeManager(), machine=FakeMachine(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="episodic"):
        bridge.consolidate(ACME, tiers=[MemoryTier.EPISODIC])


# ---------------------------------------------------------------------------
# Round-3 review regressions
# ---------------------------------------------------------------------------


def test_the_bridge_refuses_to_build_without_a_declared_tenant():
    """Critical (r3). Making the acknowledgement mandatory and the pin optional was a regression.

    With no pin the guard's None branch admitted every scope, so an unpinned provider was weaker
    in its default configuration than the lazy pin round 2 replaced.
    """
    unpinned = MemoryProviderConfig(provider="seed", options={"single_tenant": True})

    with pytest.raises(MemoryProviderConfigError, match="requires options\\['tenant_id'\\]"):
        SeedMemoryProvider(manager=FakeManager(), config=unpinned)


def test_an_unpinned_bridge_cannot_be_built_through_the_registry_either():
    from egpt.memory.config import build_memory_provider

    with pytest.raises(MemoryProviderConfigError, match="tenant_id"):
        build_memory_provider(
            MemoryProviderConfig(provider="seed", options={"single_tenant": True, "memory_manager": FakeManager()})
        )


def test_an_empty_batch_still_runs_the_tenant_guard():
    """No groups meant no per-group guards, so a refused scope got an empty receipt instead."""
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="single_tenant"):
        bridge.write(GLOBEX, [])


def test_an_event_whose_scope_differs_from_the_write_scope_is_refused():
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="does not match the write scope"):
        bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=GLOBEX, tier=MemoryTier.SEMANTIC)])


def test_a_runtime_failure_mid_batch_raises_with_the_partial_receipt():
    """Both adapters must fail the same way, or a caller behind the seam has no portable retry."""
    from egpt.memory.types import PartialWriteError

    manager = FakeManager()
    store = FakeStore("decision_log")
    bridge = SeedMemoryProvider(manager=manager, stores={MemoryTier.PROCEDURAL: store}, config=seed_config())

    def explode(messages, **kwargs):
        raise RuntimeError("store down")

    store.process = explode  # type: ignore[method-assign]

    with pytest.raises(PartialWriteError) as exc:
        bridge.write(
            ACME,
            [
                MemoryEvent(content="a preference", scope=ACME, tier=MemoryTier.SEMANTIC),
                MemoryEvent(content="a rule", scope=ACME, tier=MemoryTier.PROCEDURAL),
            ],
        )

    # The semantic group committed; the receipt says which ids, so a retry can be idempotent.
    assert exc.value.receipt.event_ids == [m.memory_id for m in manager.added]


# ---------------------------------------------------------------------------
# Round-6 review
# ---------------------------------------------------------------------------


def test_a_manager_write_failing_midway_reports_the_rows_it_committed():
    """Critical. `add_user_memory` runs per event, so a failure leaves earlier rows persisted.

    The ids were accumulating in a local that the `return` never reached, so the receipt said
    `accepted=0, event_ids=[]` for rows that are in the database -- and a caller following
    `PartialWriteError`'s documented retry re-sends them with no id, minting fresh uuid4 rows and
    duplicating every one.
    """
    from egpt.memory.types import PartialWriteError

    manager = FakeManager()
    original = manager.add_user_memory

    def fail_on_third(memory, user_id=None):
        if len(manager.added) >= 2:
            raise RuntimeError("manager down")
        return original(memory, user_id=user_id)

    manager.add_user_memory = fail_on_third  # type: ignore[method-assign]
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    with pytest.raises(PartialWriteError) as exc:
        bridge.write(
            ACME,
            [MemoryEvent(content=f"note {i}", scope=ACME, tier=MemoryTier.SEMANTIC) for i in range(4)],
        )

    committed = [m.memory_id for m in manager.added]
    assert committed, "the fixture must actually commit some rows or this proves nothing"
    assert exc.value.receipt.event_ids == committed
    assert exc.value.receipt.accepted == len(committed)


def test_write_tier_fails_with_the_same_type_the_other_adapter_uses():
    """`write_tier` used to let the backend's raw exception through while `write` wrapped it."""
    from egpt.memory.types import PartialWriteError

    manager = FakeManager()

    def explode(memory, user_id=None):
        raise RuntimeError("manager down")

    manager.add_user_memory = explode  # type: ignore[method-assign]
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    with pytest.raises(PartialWriteError):
        bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=ACME)])


def test_consolidate_refuses_a_scope_the_curator_cannot_filter_on():
    """`deduplicate` filters on user_id alone, so a narrower scope was accepted and ignored.

    A write-side accept-and-ignore, worse than the read-side one `_reject_unfilterable_scope` was
    written for, in the module whose whole posture is refuse-rather-than-ignore.
    """
    machine = FakeMachine()
    bridge = SeedMemoryProvider(manager=FakeManager(), machine=machine, config=seed_config())

    with pytest.raises(MemoryOperationError, match="filters on user_id alone"):
        bridge.consolidate(ACME_AGENT)

    assert machine.curator.calls == [], "refused before the curator ever ran"


def test_a_session_scoped_write_is_refused_rather_than_silently_losing_its_session():
    """UserMemory has no session column, and reads already refuse the same scope."""
    session = Scope(tenant_id="acme", user_id="u1", session_id="s1")
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="no session column"):
        bridge.write_tier(session, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=session)])


def test_a_store_write_defaults_to_not_updated_rather_than_assuming_success():
    """`getattr(store, "was_updated", True)` assumed success from the field that reports it."""

    class Flagless(FakeStore):
        @property
        def was_updated(self):
            raise AttributeError("this store does not report the flag")

    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: Flagless()}, config=seed_config())

    receipt = bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="note", scope=ACME)])

    assert receipt.accepted == 0
    assert receipt.tiers_written == []


def test_two_tiers_bound_to_the_same_learning_type_do_not_collide():
    """The record id carried store and scope but not tier, and this path has no dedup."""
    bridge = SeedMemoryProvider(
        stores={MemoryTier.SEMANTIC: FakeStore("profile"), MemoryTier.PROCEDURAL: FakeStore("profile")},
        config=seed_config(),
    )

    records = bridge.read(ACME, MemoryQuery(text="q")).records

    assert len({r.id for r in records}) == len(records) == 2


def test_the_factory_refuses_a_manager_that_is_not_one():
    """`_build_co_located` checks its substrates; this took collaborators on faith."""
    with pytest.raises(MemoryProviderConfigError, match="memory_manager"):
        build_memory_provider(seed_config(memory_manager=object()))


def test_a_user_less_scope_is_refused_rather_than_sharing_the_default_pool():
    """`MemoryManager` substitutes `user_id="default"` for an unset user.

    So within the pinned tenant every user-less scope shares one pool, and the projection then
    stamps the *requesting* scope onto each row -- the answer looks correctly narrow. Same shape as
    the two leaks this module already refuses, on the slot that was left unguarded.
    """
    tenant_only = Scope(tenant_id="acme")
    manager = FakeManager([UserMemory(memory="someone else's note", memory_id="m1")], owner="default")
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    with pytest.raises(MemoryOperationError, match="user_id='default'"):
        bridge.read_tier(tenant_only, MemoryTier.SEMANTIC, MemoryQuery(text="note"))
    with pytest.raises(MemoryOperationError, match="user_id='default'"):
        bridge.write_tier(tenant_only, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=tenant_only)])

    assert manager.search_calls == [], "refused before the manager was ever asked"


def test_the_factory_refuses_a_learning_machine_that_is_not_one():
    """The one collaborator taken on faith while its two siblings were checked.

    The regression only appears on a *user-scoped* consolidate, because the pass short-circuits
    without a `user_id` -- a test written without one would pass vacuously.
    """
    with pytest.raises(MemoryProviderConfigError, match="learning_machine"):
        build_memory_provider(seed_config(memory_manager=FakeManager(), learning_machine="not a machine"))


def test_a_zero_limit_does_not_fetch_before_discarding():
    """`MemoryManager` truncates only when `limit > 0`, so asking it for zero rows materialised
    the user's whole table and then threw it away. Asserted on the *request*, since the result is
    empty either way."""
    manager = FakeManager([UserMemory(memory=f"m{i}", memory_id=f"m{i}") for i in range(25)])
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    assert bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q", limit=0)) == []
    assert manager.search_calls == [], "the manager was asked for a page that was then discarded"


def test_the_seed_caps_a_manager_that_ignores_the_limit():
    """Defence in depth: `MemoryManager` truncates, but the provider must not depend on it.

    The cap survived mutation because `FakeManager` truncates faithfully, so nothing could observe
    a non-conforming backend -- which is exactly what the cap is for.
    """

    class OverReturningManager(FakeManager):
        def search_user_memories(self, query=None, limit=None, retrieval_method=None, user_id=None):
            super().search_user_memories(query, limit, retrieval_method, user_id)
            return list(self.memories)  # ignores `limit`, as a bad query would

    manager = OverReturningManager([UserMemory(memory=f"m{i}", memory_id=f"m{i}") for i in range(10)])
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    assert len(bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q", limit=3))) == 3


# ---------------------------------------------------------------------------
# Self-review before round 9: the sibling of the round-8 critical, in this adapter
# ---------------------------------------------------------------------------


def _refusing_store_bridge():
    manager, store = FakeManager(), FakeStore("decision_log")

    def refuse(messages, **kwargs):
        raise MemoryOperationError("this store refuses", field_name="tier")

    async def arefuse(messages, **kwargs):
        raise MemoryOperationError("this store refuses", field_name="tier")

    store.process = refuse  # type: ignore[method-assign]
    store.aprocess = arefuse  # type: ignore[method-assign]
    bridge = SeedMemoryProvider(manager=manager, stores={MemoryTier.PROCEDURAL: store}, config=seed_config())
    batch = [
        MemoryEvent(content="a preference", scope=ACME, tier=MemoryTier.SEMANTIC),
        MemoryEvent(content="a rule", scope=ACME, tier=MemoryTier.PROCEDURAL),
    ]
    return manager, bridge, batch


async def test_a_refusal_after_an_earlier_group_committed_is_a_partial_write():
    """The round-8 critical's sibling, in the adapter the review did not flag.

    Tier groups are written in sequence, so a refusal in group two leaves group one persisted.
    Propagating it bare lost those ids, and the retry `PartialWriteError` documents then re-stamps
    fresh `uuid4`s -- finding F1 through the error path, again.
    """
    from egpt.memory.types import PartialWriteError

    for is_async in (False, True):
        manager, bridge, batch = _refusing_store_bridge()

        with pytest.raises(PartialWriteError) as exc:
            if is_async:
                await bridge.awrite(ACME, batch)
            else:
                bridge.write(ACME, batch)

        committed = [m.memory_id for m in manager.added]
        assert committed, "the fixture must commit something first, or this proves nothing"
        assert exc.value.receipt.event_ids == committed
        assert isinstance(exc.value.__cause__, MemoryOperationError)


def test_a_refusal_with_nothing_committed_still_propagates_bare():
    """The distinction the fan-out handler exists to make: no partial state, nothing to reconcile."""
    _, bridge, _ = _refusing_store_bridge()

    with pytest.raises(MemoryOperationError, match="no episodic store"):
        bridge.write(ACME, [MemoryEvent(content="something happened", scope=ACME)])


class NarrowStore:
    """A store that filters on a *subset* of the slots, mirroring `DecisionLogStore.recall`.

    `FakeStore` takes `**kwargs` and returns a constant, so it structurally cannot show what a real
    store does with the slots it is handed. This one can: it filters on `agent_id` / `session_id`
    and has no `user_id` parameter at all, exactly like the shipped decision log.
    """

    learning_type = "decision_log"
    was_updated = False

    def __init__(self, rows):
        self.rows = rows  # (agent_id, user_id, text)
        self.ignored: List[str] = []

    def recall(self, agent_id=None, session_id=None, limit=10, **kwargs):
        self.ignored = sorted(k for k, v in kwargs.items() if v is not None and k not in ("message", "query"))
        matching = [r for r in self.rows if agent_id is None or r[0] == agent_id]
        return "; ".join(f"{user}: {text}" for _, user, text in matching[:limit])

    async def arecall(self, **kwargs):
        return self.recall(**kwargs)

    def build_context(self, data):
        return data or ""

    def process(self, messages, **kwargs): ...
    async def aprocess(self, messages, **kwargs): ...
    def get_tools(self, **kwargs):
        return []

    async def aget_tools(self, **kwargs):
        return []

    @property
    def schema(self):
        return dict


def test_a_bound_store_may_ignore_slots_the_bridge_hands_it():
    """A **known gap**, pinned so it is visible rather than discovered.

    The bridge refuses a scope the *manager* cannot filter, but it cannot check what a bound store
    did with the slots it passed -- `LearningStore.recall` is declared `(self, **kwargs)`, so
    deriving the honoured set by signature yields nothing and would refuse every store read.

    So a narrowed read can come back wider, stamped with the requesting scope. Which slots a store
    honours belongs with the tier-to-store mapping: **SM-C2 (#90)**. This test fails when that
    lands, which is the point.
    """
    store = NarrowStore([("a1", "alice", "approved refund"), ("a1", "bob", "denied refund")])
    alice = Scope(tenant_id="acme", user_id="alice", agent_id="a1")
    bridge = SeedMemoryProvider(stores={MemoryTier.PROCEDURAL: store}, config=seed_config())

    records = bridge.read_tier(alice, MemoryTier.PROCEDURAL, MemoryQuery(text="refund"))

    assert store.ignored == ["user_id"], "the scope sets user_id; this store has no such parameter"
    assert "bob" in records[0].content, "the known gap: bob's row reaches a read scoped to alice"
    assert records[0].scope.user_id == "alice", "...and it is stamped with the requesting scope"
