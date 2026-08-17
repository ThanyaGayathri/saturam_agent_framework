"""The seed bridge: existing memory behind the new seam (SM-C1, phase 5).

ADR 0003 §2 decided WRAP, never replace, so the load-bearing assertions here are about what the
bridge does *not* do: it does not mutate the seed, it does not invent an episodic store, and it
does not claim substrates it has no access to.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional

import pytest

from egpt.db.schemas import UserMemory
from egpt.memory.adapters.seed import SeedMemoryProvider
from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError, build_memory_provider
from egpt.memory.manager import MemoryManager
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryOperationError, MemoryQuery, PartialWriteError, Scope, Substrate

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

    async def asearch_user_memories(self, query=None, limit=None, retrieval_method=None, user_id=None):
        """The async half the real manager grew in SM-C2. It **suspends**, so a gathered read
        actually interleaves rather than being a sync one in disguise."""
        await asyncio.sleep(0)
        return self.search_user_memories(query=query, limit=limit, retrieval_method=retrieval_method, user_id=user_id)

    def add_user_memory(self, memory: UserMemory, user_id=None):
        memory.memory_id = memory.memory_id or f"m{len(self.added)}"
        memory.user_id = user_id or "default"  # the real manager's substitution
        self.added.append(memory)
        # Also readable: the real manager persists, so a double where a write is invisible to the
        # next read would let a round-trip bug pass.
        self.memories.append(memory)
        return memory.memory_id

    async def aadd_user_memory(self, memory: UserMemory, user_id=None):
        await asyncio.sleep(0)
        return self.add_user_memory(memory, user_id=user_id)


class SyncOnlyManager(FakeManager):
    """A manager with only the sync half, like a third-party one predating SM-C2.

    The bridge duck-checks its collaborators rather than requiring `MemoryManager`, so the thread
    hop is still a live path -- and a path with no double is a path with no test.
    """

    asearch_user_memories = None
    aadd_user_memory = None


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

    # `user_id` / `agent_id` / `team_id` are **named**, as every shipped store's `process` names
    # them. A slot arriving through `**kwargs` is one the store cannot record, and the bridge
    # refuses a write scoped to it -- so a double that named nothing would be laxer than any real
    # store and could not be written to at all.
    def process(self, messages, user_id=None, agent_id=None, team_id=None, **kwargs):
        self.processed.append(list(messages))

    async def aprocess(self, messages, user_id=None, agent_id=None, team_id=None, **kwargs):
        self.process(messages, user_id=user_id, agent_id=agent_id, team_id=team_id, **kwargs)

    def build_context(self, data):
        return "" if data is None else f"[{self._type}] {data}"

    def get_tools(self, **kwargs):
        return []

    async def aget_tools(self, **kwargs):
        return []


def raises_from_process(error):
    """A `process` replacement that **names the slots a real store names**, then fails.

    A bare `lambda *a, **k: raise` names none, and the bridge refuses a write scoped to a slot the
    store cannot record -- so such a patch would exercise the guard rather than the failure the
    test is about.
    """

    def process(messages, user_id=None, agent_id=None, team_id=None, **kwargs):
        raise error

    return process


class SlotNamingStore(FakeStore):
    """A double whose `recall` **names** `user_id`, as `user_profile` / `user_memory` /
    `entity_memory` / `learned_knowledge` all do.

    `FakeStore.recall` is `(**kwargs)`, which is the honest shape for "filters on nothing" -- and
    `options['store_slots']` may not declare a slot the store never named, because a slot arriving
    through `**kwargs` is ignored. A test that needs a *legal* declaration needs a double that
    could actually honour it.
    """

    def recall(self, user_id=None, **kwargs):
        return super().recall(user_id=user_id, **kwargs)


class WideStore(FakeStore):
    """A custom store that names every slot -- the one shape for which declaring all four is true."""

    def recall(self, user_id=None, agent_id=None, team_id=None, session_id=None, **kwargs):
        return super().recall(user_id=user_id, agent_id=agent_id, team_id=team_id, session_id=session_id, **kwargs)


class FakeCurator:
    def __init__(self) -> None:
        self.calls: List[str] = []

    def deduplicate(self, user_id: str) -> int:
        self.calls.append(user_id)
        return 3


class FakeMachine:
    """`LearningMachine`'s two surfaces this bridge touches: `curator` and `stores`.

    `stores` is keyed by `learning_type`, as the real machine's is, so the tier mapping is applied
    to the same shape it will meet in production.
    """

    def __init__(self, stores: Optional[Dict[str, Any]] = None) -> None:
        self._curator = FakeCurator()
        self.stores: Dict[str, Any] = dict(stores or {})

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


@pytest.mark.parametrize("verb", ["search_user_memories", "add_user_memory"])
def test_the_manager_methods_this_bridge_calls_have_both_halves(verb):
    """SM-C2 gave these two the async variant SPEC section 11.3 carried forward.

    The thread hop is still reachable -- this bridge duck-checks its collaborators, so a
    third-party manager may have only the sync half -- but it is no longer what the real one gets.
    """
    from egpt.memory.manager import MemoryManager

    assert hasattr(MemoryManager, "a" + verb)


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
    """The bridge hands over all four slots; honouring them is the store's side of the contract.

    Read at a scope the store is declared to honour, because an agent-scoped read of a store that
    filters on `user_id` alone is now refused -- which is `test_a_bound_store_that_would_ignore_a
    _slot_is_refused` next door, and the reason this one has to say which slots it means.
    """
    store = WideStore()
    SeedMemoryProvider(
        stores={MemoryTier.SEMANTIC: store},
        config=seed_config(store_slots={"user_profile": ["user_id", "agent_id", "team_id", "session_id"]}),
    ).read_tier(ACME_AGENT, MemoryTier.SEMANTIC, MemoryQuery(text="hello"))

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


@pytest.mark.parametrize(
    ("bounds", "field"),
    [
        ({"valid_from": 100, "valid_to": 200}, "valid_from"),
        ({"valid_from": 100}, "valid_from"),
        ({"valid_to": 200}, "valid_to"),
    ],
)
def test_a_valid_time_window_is_refused_rather_than_ignored(bounds, field):
    """SM-V4 (#98), for the reason ``as_of`` is refused: no valid-time columns here, so an ignored
    window returns the whole store as if every row fell inside it.

    ``field_name`` must name the bound the caller actually set -- a message about ``as_of``, or
    about ``valid_from`` when only ``valid_to`` was given, sends a reader to the wrong argument.
    """
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="valid-time window") as excinfo:
        bridge.read(ACME, MemoryQuery(text="q", **bounds))
    assert excinfo.value.field_name == field


def test_the_window_refusal_applies_to_a_tier_the_seed_does_not_serve():
    """The guard sits in ``_guard_read``, not inside a tier branch: a read naming an unserved tier
    used to skip every guard and return ``[]`` with no error."""
    bridge = SeedMemoryProvider(manager=FakeManager(), config=seed_config())

    with pytest.raises(MemoryOperationError, match="valid-time window"):
        bridge.read(ACME, MemoryQuery(text="q", tiers=[MemoryTier.PROCEDURAL], valid_from=999))


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
    """Each tier was asked for `limit` records and then the slice capped at `limit`.

    `learned_knowledge` filters on `user_id` only under the `"user"` namespace, so a deployment
    running that namespace declares it -- which is what makes a user-scoped read of it answerable
    at all, and is the one store combination that can span two tiers under one scope.
    """
    manager = FakeManager([UserMemory(memory=f"semantic {i}", memory_id=f"m{i}") for i in range(5)])
    bridge = SeedMemoryProvider(
        manager=manager,
        stores={MemoryTier.PROCEDURAL: SlotNamingStore("learned_knowledge")},
        config=seed_config(store_slots={"learned_knowledge": ["user_id"]}),
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
        stores={MemoryTier.SEMANTIC: FakeStore("user_profile"), MemoryTier.PROCEDURAL: FakeStore("user_profile")},
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

    def process(self, messages, agent_id=None, session_id=None, user_id=None, team_id=None, **kwargs): ...
    async def aprocess(self, messages, agent_id=None, session_id=None, user_id=None, team_id=None, **kwargs): ...
    def get_tools(self, **kwargs):
        return []

    async def aget_tools(self, **kwargs):
        return []

    @property
    def schema(self):
        return dict


def test_a_bound_store_that_would_ignore_a_slot_is_refused():
    """The gap SM-C1 pinned and SM-C2 closes, kept as its own repro.

    `decision_log` has no `user_id` parameter, so a read scoped to alice used to return bob's row
    too, stamped with alice's scope -- wide answer, narrow label, invisible above this layer. The
    refusal comes from `learn_stores.RECALL_SLOTS`, and it lands *before* the store is asked.
    """
    store = NarrowStore([("a1", "alice", "approved refund"), ("a1", "bob", "denied refund")])
    alice = Scope(tenant_id="acme", user_id="alice", agent_id="a1")
    bridge = SeedMemoryProvider(stores={MemoryTier.PROCEDURAL: store}, config=seed_config())

    with pytest.raises(MemoryOperationError, match="does not filter on"):
        bridge.read_tier(alice, MemoryTier.PROCEDURAL, MemoryQuery(text="refund"))

    assert store.ignored == [], "refused before the store was ever asked"


def test_the_same_store_answers_once_the_scope_is_one_it_can_filter():
    """The refusal is about this scope, not about the store: drop `user_id` and it serves."""
    store = NarrowStore([("a1", "alice", "approved refund"), ("a2", "bob", "denied refund")])
    agent_only = Scope(tenant_id="acme", agent_id="a1")
    bridge = SeedMemoryProvider(stores={MemoryTier.PROCEDURAL: store}, config=seed_config())

    records = bridge.read_tier(agent_only, MemoryTier.PROCEDURAL, MemoryQuery(text="refund"))

    assert "bob" not in records[0].content


class SuspendingStore:
    """A store that records `was_updated`, then suspends before returning.

    Any real store doing IO after its own bookkeeping has this shape, and it is the shape that
    opens the window: the flag reports the last operation on the *store*, so a second writer
    reaching `aprocess` while the first is suspended overwrites the first writer's answer.

    The suspension is a plain `sleep(0)` rather than a gate the other writer must release. A gate
    would require the two critical sections to overlap, which is precisely what the fix prevents --
    so the test would deadlock on the fix instead of failing on the defect.
    """

    learning_type = "user_profile"
    schema = dict

    def __init__(self) -> None:
        self.was_updated = False

    def recall(self, **kwargs):
        return None

    async def arecall(self, **kwargs):
        return None

    def process(self, messages, user_id=None, agent_id=None, team_id=None, **kwargs):
        self.was_updated = True

    async def aprocess(self, messages, user_id=None, agent_id=None, team_id=None, **kwargs):
        self.was_updated = "changes" in messages[0]["content"]  # bookkeeping...
        await asyncio.sleep(0)  # ...then IO, where the other writer used to interleave

    def build_context(self, data):
        return data or ""

    def get_tools(self, **kwargs):
        return []

    async def aget_tools(self, **kwargs):
        return []


async def test_concurrent_store_writes_each_get_their_own_answer():
    """`was_updated` reports the last operation on the *store*, not on your call.

    Two concurrent `awrite_tier` calls used to read each other's flag: the writer whose events
    landed reported `accepted=0` with an empty `tiers_written`, and the no-op reported the other's
    success. The write and the flag read are now one critical section per store.

    Serialising them is not only about the count. Stores reset their own progress flags on entry --
    `SessionContextStore` sets `context_updated = False` before extracting -- so two concurrent
    `process` calls on one store were corrupting its state, not just this receipt.
    """
    store = SuspendingStore()
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: store}, config=seed_config())

    landed, no_op = await asyncio.gather(
        bridge.awrite_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="this changes things", scope=ACME)]),
        bridge.awrite_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="no-op", scope=ACME)]),
    )

    assert landed.accepted == 1 and landed.tiers_written == [MemoryTier.SEMANTIC]
    assert no_op.accepted == 0 and no_op.tiers_written == []
    assert landed.details["stores"] == {"user_profile": True}
    assert no_op.details["stores"] == {"user_profile": False}


async def test_two_writers_to_one_store_do_not_overlap():
    """The lock is what makes the flag attributable, so its holding is asserted directly.

    A store that observed a second writer inside its own `aprocess` would be back to reporting
    whichever call finished last.
    """
    depth = {"now": 0, "max": 0}

    class Counting(SuspendingStore):
        async def aprocess(self, messages, user_id=None, agent_id=None, team_id=None, **kwargs):
            depth["now"] += 1
            depth["max"] = max(depth["max"], depth["now"])
            await asyncio.sleep(0)
            depth["now"] -= 1

    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: Counting()}, config=seed_config())

    await asyncio.gather(
        *(bridge.awrite_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content=f"n{i}", scope=ACME)]) for i in range(4))
    )

    assert depth["max"] == 1, "two writers were inside one store's process at the same time"


@pytest.mark.filterwarnings("ignore:The 'entity_memory' store")
async def test_writes_to_different_stores_still_run_concurrently():
    """The lock is per store, not per provider: serialising a tier's independent stores would
    turn a fan-out into a queue for no safety gain."""
    inside = {"now": 0, "max": 0}

    class Counting(SuspendingStore):
        def __init__(self, learning_type):
            super().__init__()
            self.learning_type = learning_type

        async def aprocess(self, messages, user_id=None, agent_id=None, team_id=None, **kwargs):
            inside["now"] += 1
            inside["max"] = max(inside["max"], inside["now"])
            await asyncio.sleep(0)
            inside["now"] -= 1
            self.was_updated = True

    stores = [Counting("user_profile"), Counting("user_memory"), Counting("entity_memory")]
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: stores}, config=seed_config())

    await bridge.awrite_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=ACME)])

    assert inside["max"] == 3, "the three stores in the tier must still be written concurrently"


# ---------------------------------------------------------------------------
# The tier-to-store mapping (SM-C2 #90)
# ---------------------------------------------------------------------------


def test_a_learning_machine_binds_its_stores_by_the_ratified_mapping():
    """ADR 0003 section 2.2 as wiring, not as a comment: enabling stores on the machine is enough.

    Before this the machine reached the bridge for `consolidate` alone and every tier had to be
    bound by hand, so the mapping was data nothing consumed.
    """
    machine = FakeMachine(stores={"user_memory": FakeStore("user_memory"), "decision_log": FakeStore("decision_log")})

    bridge = SeedMemoryProvider(machine=machine, config=seed_config())

    assert bridge.tiers() == {
        MemoryTier.WORKING: False,
        MemoryTier.EPISODIC: False,
        MemoryTier.SEMANTIC: True,
        MemoryTier.PROCEDURAL: True,
    }


@pytest.mark.filterwarnings("ignore:The 'entity_memory' store")
def test_a_tier_holds_every_machine_store_mapped_to_it():
    """Three stores are semantic, so a read of that tier consults all three, in table order."""
    machine = FakeMachine(
        stores={
            "user_profile": SlotNamingStore("user_profile", "works at acme"),
            "user_memory": SlotNamingStore("user_memory", "prefers email"),
            "entity_memory": SlotNamingStore("entity_memory", "acme is a customer"),
        }
    )
    bridge = SeedMemoryProvider(
        machine=machine,
        # entity_memory filters on `user_id` only under the "user" namespace, so a user-scoped
        # read of it is refused unless the deployment says otherwise.
        config=seed_config(store_slots={"entity_memory": ["user_id"]}),
    )

    records = bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q", limit=10))

    assert [r.provenance["learning_type"] for r in records] == ["user_profile", "user_memory", "entity_memory"]


def test_an_explicit_binding_replaces_the_machine_s_stores_for_that_tier():
    """Extending it instead would hand the caller a read they did not ask for."""
    machine = FakeMachine(stores={"user_memory": FakeStore("user_memory"), "decision_log": FakeStore("decision_log")})
    chosen = FakeStore("user_profile", "the only one")

    bridge = SeedMemoryProvider(machine=machine, stores={MemoryTier.SEMANTIC: chosen}, config=seed_config())

    assert bridge.stores[MemoryTier.SEMANTIC] == (chosen,)
    assert [s.learning_type for s in bridge.stores[MemoryTier.PROCEDURAL]] == ["decision_log"]


def test_a_machine_carrying_a_store_no_tier_maps_is_refused_at_construction():
    """Guessing its tier would file its memory as the wrong kind and serve it back as that kind."""
    machine = FakeMachine(stores={"weather_cache": FakeStore("weather_cache")})

    with pytest.raises(MemoryProviderConfigError, match="weather_cache"):
        SeedMemoryProvider(machine=machine, config=seed_config())


def test_a_custom_store_bound_by_hand_may_travel_with_the_machine():
    """Its tier is already decided, so there is nothing left to guess."""
    custom = FakeStore("weather_cache")
    machine = FakeMachine(stores={"user_memory": FakeStore("user_memory"), "weather_cache": custom})

    bridge = SeedMemoryProvider(machine=machine, stores={MemoryTier.PROCEDURAL: custom}, config=seed_config())

    assert bridge.stores[MemoryTier.PROCEDURAL] == (custom,)


def test_a_store_without_a_learning_type_is_refused_at_construction():
    """Two things key off it -- the record id and the slot lookup -- and both fail quietly.

    Ids would read `None:semantic:...` and collide across stores; the slot lookup would miss the
    table and refuse every narrowed read of a store that may well honour it.
    """

    class Nameless(FakeStore):
        learning_type = None  # type: ignore[assignment]

    with pytest.raises(MemoryProviderConfigError, match="learning_type"):
        SeedMemoryProvider(stores={MemoryTier.SEMANTIC: Nameless()}, config=seed_config())


def test_two_stores_of_the_same_type_in_one_tier_are_refused():
    """A store's read-projection id is `learning_type:tier:scope`, so the second would overwrite
    the first inside one slice with nothing to notice it."""
    with pytest.raises(MemoryProviderConfigError, match="learning_type"):
        SeedMemoryProvider(
            stores={MemoryTier.SEMANTIC: [FakeStore("user_memory"), FakeStore("user_memory")]},
            config=seed_config(),
        )


def test_a_tier_with_stores_bound_no_longer_falls_through_to_the_manager():
    """The precedence SM-C1 set, restated for a *group* of stores rather than one."""
    manager = FakeManager([UserMemory(memory="from manager", memory_id="m1")])
    bridge = SeedMemoryProvider(
        manager=manager,
        stores={MemoryTier.SEMANTIC: [FakeStore("user_profile"), FakeStore("user_memory")]},
        config=seed_config(),
    )

    records = bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q", limit=10))

    assert len(records) == 2 and manager.search_calls == []


def test_a_write_to_a_tier_reaches_every_store_bound_to_it():
    """`LearningMachine.process` fans out across its stores; so must a tier-scoped write."""
    profile, memory = FakeStore("user_profile"), FakeStore("user_memory")
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: [profile, memory]}, config=seed_config())

    receipt = bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="likes email", scope=ACME)])

    assert profile.processed and memory.processed
    assert receipt.tiers_written == [MemoryTier.SEMANTIC]


async def test_both_write_halves_reach_every_store_when_one_of_them_fails():
    """A settled fan-out on both sides, compared by which stores were actually written.

    Short-circuiting the sync loop while gathering the async one is the divergence that survived
    six rounds on the co-located adapter; the same shape exists here the moment a tier holds two
    stores.
    """

    def wire():
        good, bad = FakeStore("user_profile"), FakeStore("user_memory")
        bad.process = raises_from_process(RuntimeError("store down"))

        async def aprocess(messages, user_id=None, agent_id=None, team_id=None, **kwargs):
            await asyncio.sleep(0)
            raise RuntimeError("store down")

        bad.aprocess = aprocess
        return SeedMemoryProvider(stores={MemoryTier.SEMANTIC: [bad, good]}, config=seed_config()), good

    sync_bridge, sync_good = wire()
    async_bridge, async_good = wire()
    events = [MemoryEvent(content="likes email", scope=ACME)]

    with pytest.raises(PartialWriteError):
        sync_bridge.write_tier(ACME, MemoryTier.SEMANTIC, events)
    with pytest.raises(PartialWriteError):
        await async_bridge.awrite_tier(ACME, MemoryTier.SEMANTIC, events)

    assert sync_good.processed == async_good.processed, "the healthy sibling must be written by both halves"
    assert sync_good.processed, "...and it must actually have been written, or this compares two empties"


def test_a_refusal_after_a_sibling_store_committed_is_a_partial_write():
    """The other half of the rule, and the half a mutation sweep found untested.

    Stores in a tier are written in sequence, so a refusal on the second leaves the first's write
    in place. Propagating it bare would tell a caller nothing landed while something did -- the
    same defect `_raise_fanout_failure` fixes one level up, at store granularity.
    """
    committed, refusing = FakeStore("user_profile"), FakeStore("user_memory")
    refusing.process = raises_from_process(MemoryOperationError("nope", field_name="content"))
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: [committed, refusing]}, config=seed_config())

    with pytest.raises(PartialWriteError):
        bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=ACME)])

    assert committed.processed, "the sibling did commit, which is what makes this partial"


def test_a_refusal_with_no_sibling_committed_still_propagates_bare():
    """Matching the co-located rule: nothing landed, so there is nothing for a receipt to help
    reconcile, and the caller needs the refusal itself."""
    store = FakeStore("user_profile")
    store.process = raises_from_process(MemoryOperationError("nope", field_name="content"))
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: store}, config=seed_config())

    with pytest.raises(MemoryOperationError) as exc:
        bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=ACME)])

    assert not isinstance(exc.value, PartialWriteError)


# ---------------------------------------------------------------------------
# Scope slots a bound store will not filter on
# ---------------------------------------------------------------------------


def test_the_slot_guard_applies_to_both_read_halves():
    """One rule, both lanes -- a refusal the async path skipped would be a leak on the async path."""
    store = FakeStore("decision_log")
    bridge = SeedMemoryProvider(stores={MemoryTier.PROCEDURAL: store}, config=seed_config())

    with pytest.raises(MemoryOperationError, match="does not filter on"):
        bridge.read_tier(ACME, MemoryTier.PROCEDURAL, MemoryQuery(text="q"))
    with pytest.raises(MemoryOperationError, match="does not filter on"):
        asyncio.run(bridge.aread_tier(ACME, MemoryTier.PROCEDURAL, MemoryQuery(text="q")))

    assert store.recall_kwargs == [], "refused before either half asked the store"


def test_one_unanswerable_store_in_a_tier_refuses_before_its_siblings_are_asked():
    """Guarding inside the loop had the sync half recall the first store and then refuse on the
    second, while the gathered async half recalled them all -- one refusal, two sets of calls."""
    answerable, unanswerable = FakeStore("user_profile"), FakeStore("decision_log")
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: [answerable, unanswerable]}, config=seed_config())

    with pytest.raises(MemoryOperationError, match="does not filter on"):
        bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q"))

    assert answerable.recall_kwargs == []


def test_an_unknown_store_type_is_refused_for_any_narrowed_scope():
    """Fail-closed. `LearningStore.recall` is `(self, **kwargs)`, so there is nothing to derive an
    honoured set from, and assuming the slots apply is the failure this guard exists for."""
    bridge = SeedMemoryProvider(stores={MemoryTier.PROCEDURAL: FakeStore("weather_cache")}, config=seed_config())

    with pytest.raises(MemoryOperationError, match="does not filter on"):
        bridge.read_tier(ACME, MemoryTier.PROCEDURAL, MemoryQuery(text="q"))

    assert bridge.read_tier(Scope(tenant_id="acme"), MemoryTier.PROCEDURAL, MemoryQuery(text="q"))


@pytest.mark.parametrize("slots", [{"user_profile": "user_id"}, {5: ["user_id"]}, ["user_id"]])
def test_a_malformed_slot_declaration_is_refused_at_construction(slots):
    """Shape first: a declaration that is not a mapping of learning_type to a collection of slots."""
    with pytest.raises(MemoryProviderConfigError, match="store_slots"):
        SeedMemoryProvider(stores={MemoryTier.SEMANTIC: FakeStore()}, config=seed_config(store_slots=slots))


def test_a_slot_name_no_scope_has_is_refused_with_the_vocabulary():
    """A misspelled slot silently *widens* what this provider answers, which is the direction that
    matters.

    Matched on this guard's own wording rather than on `store_slots`: the store cross-check next
    door would also refuse `"session"`, so a looser assertion passes with this check deleted.
    """
    with pytest.raises(MemoryProviderConfigError, match="names unknown slot"):
        SeedMemoryProvider(
            stores={MemoryTier.SEMANTIC: FakeStore()},
            config=seed_config(store_slots={"user_profile": ["session"]}),
        )


def test_a_write_to_a_slot_the_store_cannot_record_is_refused():
    """The read/write asymmetry, closed.

    A session-scoped write to `user_profile` -- whose `process` names no `session_id` -- used to be
    accepted, stored without its session, and then refused on the way back out. The manager path
    has always refused exactly this shape (`_reject_unstorable_scope`); the store path now does too.
    """
    session_scoped = Scope(tenant_id="acme", session_id="s1")
    store = FakeStore("user_profile")
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: store}, config=seed_config())

    bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=ACME)])
    with pytest.raises(MemoryOperationError, match="takes no"):
        bridge.write_tier(session_scoped, MemoryTier.SEMANTIC, [MemoryEvent(content="y", scope=session_scoped)])

    assert len(store.processed) == 1, "refused before the store was asked"
    with pytest.raises(MemoryOperationError, match="does not filter on"):
        bridge.read_tier(session_scoped, MemoryTier.SEMANTIC, MemoryQuery(text="y"))


async def test_the_write_slot_guard_applies_to_both_halves():
    """One rule, both lanes, and refused before any store in the tier is written."""
    session_scoped = Scope(tenant_id="acme", session_id="s1")
    store = FakeStore("user_profile")
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: store}, config=seed_config())
    events = [MemoryEvent(content="y", scope=session_scoped)]

    with pytest.raises(MemoryOperationError, match="takes no"):
        bridge.write_tier(session_scoped, MemoryTier.SEMANTIC, events)
    with pytest.raises(MemoryOperationError, match="takes no"):
        await bridge.awrite_tier(session_scoped, MemoryTier.SEMANTIC, events)

    assert store.processed == []


def test_a_store_that_names_the_slot_still_takes_the_write():
    """The refusal is about this store, not about session scoping: `decision_log.process` names
    `session_id`, so the same write lands."""
    session_scoped = Scope(tenant_id="acme", session_id="s1")
    store = NarrowStore([])
    bridge = SeedMemoryProvider(stores={MemoryTier.PROCEDURAL: store}, config=seed_config())

    receipt = bridge.write_tier(session_scoped, MemoryTier.PROCEDURAL, [MemoryEvent(content="y", scope=session_scoped)])

    assert receipt.tiers_written == []  # NarrowStore reports no update; the point is it was allowed


# ---------------------------------------------------------------------------
# The manager's async halves (SPEC section 11.3)
# ---------------------------------------------------------------------------


async def test_the_async_read_awaits_the_manager_rather_than_hopping_a_thread():
    """Asserted on what the manager was *asked*: a result-only check cannot tell the two apart."""
    manager = FakeManager([UserMemory(memory="a note", memory_id="m1")])
    calls = []
    original = manager.asearch_user_memories

    async def recording(**kwargs):
        calls.append(kwargs)
        return await original(**kwargs)

    manager.asearch_user_memories = recording
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    records = await bridge.aread_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="a note"))

    assert [c["user_id"] for c in calls] == ["u1"]
    assert [r.content for r in records] == ["a note"]


async def test_the_async_write_awaits_the_manager_rather_than_hopping_a_thread():
    manager = FakeManager()
    calls = []
    original = manager.aadd_user_memory

    async def recording(memory, user_id=None):
        calls.append(user_id)
        return await original(memory, user_id=user_id)

    manager.aadd_user_memory = recording
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    receipt = await bridge.awrite_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=ACME)])

    assert calls == ["u1"] and receipt.accepted == 1


async def test_a_manager_without_the_async_halves_still_works_through_the_thread_hop():
    """The bridge duck-checks its collaborators, so a third-party manager may have only the sync
    half. Dropping the fallback would turn that into an `AttributeError` at first read."""
    manager = SyncOnlyManager([UserMemory(memory="a note", memory_id="m1")])
    bridge = SeedMemoryProvider(manager=manager, config=seed_config())

    records = await bridge.aread_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="a note"))
    receipt = await bridge.awrite_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=ACME)])

    assert [r.content for r in records] == ["a note"]
    assert receipt.accepted == 1


async def test_both_manager_write_halves_commit_the_same_rows_when_one_event_fails():
    """Sequential and short-circuiting on both sides: gathering the async half would commit rows
    the sync half never attempted, for one identical failure."""

    def wire():
        manager = FakeManager()
        state = {"calls": 0}
        original = manager.add_user_memory

        def failing(memory, user_id=None):
            state["calls"] += 1
            if state["calls"] == 2:
                raise RuntimeError("db down")
            return original(memory, user_id=user_id)

        manager.add_user_memory = failing
        return SeedMemoryProvider(manager=manager, config=seed_config()), manager

    sync_bridge, sync_manager = wire()
    async_bridge, async_manager = wire()
    events = [MemoryEvent(content=f"note {i}", scope=ACME, id=f"n{i}") for i in range(4)]

    with pytest.raises(PartialWriteError) as sync_exc:
        sync_bridge.write_tier(ACME, MemoryTier.SEMANTIC, events)
    with pytest.raises(PartialWriteError) as async_exc:
        await async_bridge.awrite_tier(ACME, MemoryTier.SEMANTIC, events)

    assert [m.memory_id for m in sync_manager.added] == [m.memory_id for m in async_manager.added] == ["n0"]
    assert sync_exc.value.receipt.event_ids == async_exc.value.receipt.event_ids == ["n0"]


# ---------------------------------------------------------------------------
# Review findings (PR #68)
# ---------------------------------------------------------------------------


def test_a_store_bound_to_one_tier_is_not_also_bound_to_its_mapped_one():
    """Major 1. A store rebound by hand, still sitting under its native key on the machine, was
    returned for its mapped tier as well and answered both.

    Non-corrupting -- the two projections carry different ids because the tier is in the id -- but
    the store's content is shelved as two kinds of memory, and a read of either tier serves it.
    """
    store = FakeStore("user_memory")
    machine = FakeMachine(stores={"user_memory": store})

    bridge = SeedMemoryProvider(machine=machine, stores={MemoryTier.PROCEDURAL: store}, config=seed_config())

    assert bridge.stores == {MemoryTier.PROCEDURAL: (store,)}
    assert bridge.tiers()[MemoryTier.SEMANTIC] is False


def test_the_machine_still_supplies_the_tiers_the_caller_did_not_rebind():
    """The exclusion is per store, not per machine: rebinding one must not drop the others."""
    profile, memory = FakeStore("user_profile"), FakeStore("user_memory")
    machine = FakeMachine(stores={"user_profile": profile, "user_memory": memory})

    bridge = SeedMemoryProvider(machine=machine, stores={MemoryTier.PROCEDURAL: memory}, config=seed_config())

    assert bridge.stores == {MemoryTier.PROCEDURAL: (memory,), MemoryTier.SEMANTIC: (profile,)}


class RaisingDb:
    """A db whose writes fail, like a real one with a closed pool or a constraint violation."""

    def __init__(self) -> None:
        self.attempts = 0

    def upsert_user_memory(self, memory):
        self.attempts += 1
        raise RuntimeError("db down")

    def get_user_memories(self, *args, **kwargs):
        return []


def test_a_manager_whose_db_refuses_the_write_does_not_receipt_it_as_accepted():
    """Major 2, against a **real** `MemoryManager` rather than a double.

    `_upsert_db_memory` logs a db failure and returns rather than raising -- its extraction loops
    have to survive one bad row -- so `add_user_memory` used to hand back an id for a row that was
    never written, and this bridge reported `accepted=1`. The manager now reports the outcome and
    a missing id takes the same exit as an exception.
    """
    db = RaisingDb()
    bridge = SeedMemoryProvider(manager=MemoryManager(db=db), config=seed_config())

    with pytest.raises(PartialWriteError) as exc:
        bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="never stored", scope=ACME, id="e1")])

    assert db.attempts == 1, "the write was attempted"
    assert exc.value.receipt.event_ids == [], "...and nothing landed, so there is nothing to retry with"


async def test_both_halves_agree_when_the_db_refuses_partway_through_a_batch():
    """The committed set, not the exception type: the async half awaits `aadd_user_memory`, so a
    fix applied to one lane only would show up here rather than in production."""

    class FailingAfterFirst(RaisingDb):
        def __init__(self):
            super().__init__()
            self.stored = []

        def upsert_user_memory(self, memory):
            self.attempts += 1
            if self.attempts == 2:
                raise RuntimeError("db down")
            self.stored.append(memory.memory_id)

    def wire():
        db = FailingAfterFirst()
        return SeedMemoryProvider(manager=MemoryManager(db=db), config=seed_config()), db

    sync_bridge, sync_db = wire()
    async_bridge, async_db = wire()
    events = [MemoryEvent(content=f"note {i}", scope=ACME, id=f"n{i}") for i in range(3)]

    with pytest.raises(PartialWriteError) as sync_exc:
        sync_bridge.write_tier(ACME, MemoryTier.SEMANTIC, events)
    with pytest.raises(PartialWriteError) as async_exc:
        await async_bridge.awrite_tier(ACME, MemoryTier.SEMANTIC, events)

    assert sync_db.stored == async_db.stored == ["n0"]
    assert sync_exc.value.receipt.event_ids == async_exc.value.receipt.event_ids == ["n0"]


def test_a_manager_with_no_db_refuses_the_write_rather_than_receipting_nothing():
    """The same rule reached by the other route: `add_user_memory` returns `None` with no db, which
    used to leave a silent `accepted=0` receipt on a write that stored nothing."""
    bridge = SeedMemoryProvider(manager=MemoryManager(), config=seed_config())

    with pytest.raises(PartialWriteError):
        bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=ACME)])


# ---------------------------------------------------------------------------
# Review findings (PR #68, round 2)
# ---------------------------------------------------------------------------


def test_an_explicit_empty_binding_disables_a_tier_the_machine_would_have_filled():
    """Round-2 Major 1. `stores={SEMANTIC: []}` means "serve nothing here".

    It used to be discarded: an empty group was skipped rather than recorded, so the key was
    absent when the machine's mapping was folded in, and the tier came back holding exactly the
    stores the caller was removing -- the opposite of the request, with no error and no log.
    """
    machine = FakeMachine(stores={"user_profile": FakeStore("user_profile"), "decision_log": FakeStore("decision_log")})

    bridge = SeedMemoryProvider(machine=machine, stores={MemoryTier.SEMANTIC: []}, config=seed_config())

    assert bridge.stores[MemoryTier.SEMANTIC] == ()
    assert [s.learning_type for s in bridge.stores[MemoryTier.PROCEDURAL]] == ["decision_log"]
    assert bridge.tiers()[MemoryTier.SEMANTIC] is False


def test_a_binding_that_disables_every_tier_and_wraps_nothing_is_a_misconfiguration():
    """The construction guard reads what actually bound, not the truthiness of the argument --
    `{tier: []}` is a non-empty dict that binds nothing."""
    with pytest.raises(MemoryProviderConfigError, match="needs a MemoryManager"):
        SeedMemoryProvider(stores={MemoryTier.SEMANTIC: []}, config=seed_config())


class QuietStore(FakeStore):
    """A store whose `process` genuinely no-ops, like one outside `LearningMode.ALWAYS`.

    `FakeStore.was_updated` is `bool(self.processed)` -- true after any non-raising `process` --
    so it structurally cannot show a store declining a write, which is what the aggregation
    question below is about.
    """

    @property
    def was_updated(self) -> bool:
        return False


def test_a_tier_reports_the_batch_taken_when_any_bound_store_took_it():
    """Round-2 Major 2, **pinned rather than fixed**, and this is the test that makes it visible.

    `accepted` on the store path means "events the tier took", never "rows written" -- `process`
    folds a batch into the store's own shape and mints no ids, so even one store reporting
    `was_updated` may have written a single row for three events. With several stores the OR is
    the least-wrong reading: requiring *every* store to report updated would receipt an ordinary
    mixed-mode tier's genuine write as `accepted=0`, since a store outside `LearningMode.ALWAYS`
    no-ops on purpose.

    What is lost is the distinction between "two of three stores took it" and "all three did".
    Closing that needs per-store granularity in `WriteReceipt`, which is an envelope change both
    adapters share. This test fails when such a field lands, which is the signal to use it.
    """
    took, declined = FakeStore("user_profile"), QuietStore("user_memory")
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: [took, declined]}, config=seed_config())
    events = [MemoryEvent(content=f"note {i}", scope=ACME) for i in range(3)]

    receipt = bridge.write_tier(ACME, MemoryTier.SEMANTIC, events)

    assert declined.processed, "the quiet store was asked"
    assert receipt.accepted == 3, "KNOWN AGGREGATION (per-store granularity is a WriteReceipt change)"
    assert receipt.tiers_written == [MemoryTier.SEMANTIC]


def test_a_tier_where_no_store_took_the_batch_reports_nothing_accepted():
    """The other side of the same rule, so the OR is not vacuously true."""
    bridge = SeedMemoryProvider(
        stores={MemoryTier.SEMANTIC: [QuietStore("user_profile"), QuietStore("user_memory")]}, config=seed_config()
    )

    receipt = bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=ACME)])

    assert (receipt.accepted, receipt.tiers_written) == (0, [])


def test_a_partial_store_failure_reports_the_tier_the_sibling_committed_to():
    """Round-2 Minor 3. The failure receipt was empty even when a sibling genuinely committed.

    Ids and destinations are different questions: `process` mints no ids, so `event_ids` stays
    empty and there is nothing for a retry to reuse -- but clearing `tiers_written` too told the
    caller nothing landed anywhere, which was false.
    """
    committed, failing = FakeStore("user_profile"), FakeStore("user_memory")
    failing.process = raises_from_process(RuntimeError("store down"))
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: [committed, failing]}, config=seed_config())

    with pytest.raises(PartialWriteError) as exc:
        bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=ACME)])

    assert exc.value.receipt.tiers_written == [MemoryTier.SEMANTIC]
    assert exc.value.receipt.event_ids == [], "process mints none, so a retry has nothing to reuse"


async def test_both_read_halves_ask_the_same_stores_when_one_of_them_fails():
    """Round-2 Minor 4. The sync half short-circuited at the first failing store while the gathered
    async half scheduled every `arecall`, so one identical failing read cost more on the async lane.

    Asserted on which stores were **asked**, because both halves raise either way and the result is
    identical -- the difference is invisible in the return value and shows up as backend load.
    """

    def wire():
        failing, healthy = FakeStore("user_profile"), FakeStore("user_memory")
        failing.recall = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("store down"))

        async def arecall(**kwargs):
            await asyncio.sleep(0)
            raise RuntimeError("store down")

        failing.arecall = arecall
        return SeedMemoryProvider(stores={MemoryTier.SEMANTIC: [failing, healthy]}, config=seed_config()), healthy

    sync_bridge, sync_healthy = wire()
    async_bridge, async_healthy = wire()
    query = MemoryQuery(text="q")

    with pytest.raises(RuntimeError):
        sync_bridge.read_tier(ACME, MemoryTier.SEMANTIC, query)
    with pytest.raises(RuntimeError):
        await async_bridge.aread_tier(ACME, MemoryTier.SEMANTIC, query)

    assert len(sync_healthy.recall_kwargs) == len(async_healthy.recall_kwargs) == 1


async def test_a_refusal_wins_over_a_transport_error_on_the_read_path_too():
    """The same cause-selection rule the write path uses: a read that is unanswerable says so,
    rather than reporting whichever backend happened to fail first."""

    def wire():
        refusing, broken = FakeStore("user_profile"), FakeStore("user_memory")
        refusing.recall = lambda **kwargs: (_ for _ in ()).throw(MemoryOperationError("nope", field_name="scope"))
        broken.recall = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("store down"))

        async def arefuse(**kwargs):
            raise MemoryOperationError("nope", field_name="scope")

        async def abreak(**kwargs):
            raise RuntimeError("store down")

        refusing.arecall, broken.arecall = arefuse, abreak
        # Transport error first in binding order, so picking "the first failure" would pick it.
        return SeedMemoryProvider(stores={MemoryTier.SEMANTIC: [broken, refusing]}, config=seed_config())

    with pytest.raises(MemoryOperationError):
        wire().read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q"))
    with pytest.raises(MemoryOperationError):
        await wire().aread_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="q"))


def test_a_read_failure_is_not_dressed_up_as_a_partial_write():
    """A read commits nothing, so there is no receipt to carry."""
    store = FakeStore("user_profile")
    store.recall = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("store down"))
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: store}, config=seed_config())

    with pytest.raises(RuntimeError) as exc:
        bridge.read(ACME, MemoryQuery(text="q"))

    assert not isinstance(exc.value, PartialWriteError)


@pytest.mark.filterwarnings("ignore:The 'entity_memory' store")
def test_a_real_entity_memory_store_reads_empty_through_this_bridge():
    """Round-2 Nit 8, pinned as a real trap rather than left as an observation.

    `EntityMemoryStore.recall` returns `None` unless *both* `entity_id` and `entity_type` are
    supplied, and `_recall_kwargs` supplies neither -- there is nowhere in `MemoryQuery` to put
    them. So the tier binds, the slot guard passes, and every read is empty. Entity retrieval
    through the seam needs a way to carry an entity key; it is not a slot-guard defect.
    """
    from egpt.learn.config import EntityMemoryConfig
    from egpt.learn.stores import EntityMemoryStore

    store = EntityMemoryStore(config=EntityMemoryConfig())
    bridge = SeedMemoryProvider(
        stores={MemoryTier.SEMANTIC: store},
        config=seed_config(store_slots={"entity_memory": ["user_id"]}),
    )

    assert store.recall(user_id="u1") is None, "no entity key, no answer -- before the bridge is involved"
    assert bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="acme")) == []


# ---------------------------------------------------------------------------
# Review findings (PR #68, round 3)
# ---------------------------------------------------------------------------


def test_a_declared_override_may_not_name_a_slot_the_store_never_named():
    """The escape hatch may not reopen the leak the guard closes.

    `store_slots` exists because the table cannot see a store's configuration -- `entity_memory`
    filters on `user_id` under the `"user"` namespace and not otherwise. It is not a way to assert
    a slot the store never named: `decision_log.recall` takes no `user_id`, so the slot lands in
    `**kwargs` and is ignored, and declaring it handed one user's rows back under another's scope.
    Checked at construction, against the store actually bound.
    """
    with pytest.raises(MemoryProviderConfigError, match="names no such parameter"):
        SeedMemoryProvider(
            stores={MemoryTier.PROCEDURAL: NarrowStore([])},
            config=seed_config(store_slots={"decision_log": ["user_id", "agent_id", "session_id"]}),
        )


@pytest.mark.filterwarnings("ignore:The 'entity_memory' store")
def test_the_override_still_works_for_the_case_it_exists_for():
    """`entity_memory` names `user_id` and honours it under the `"user"` namespace, so a
    deployment running that namespace can say so -- which is the whole point of the hatch."""
    bridge = SeedMemoryProvider(
        stores={MemoryTier.SEMANTIC: SlotNamingStore("entity_memory", "acme is a customer")},
        config=seed_config(store_slots={"entity_memory": ["user_id"]}),
    )

    records = bridge.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="acme"))

    assert [r.content for r in records] == ["[entity_memory] acme is a customer"]


def test_the_declared_slots_a_store_cannot_back_are_named_in_the_refusal():
    """A deployment that gets it wrong needs to know which slot, not only that something is wrong."""
    with pytest.raises(MemoryProviderConfigError) as exc:
        SeedMemoryProvider(
            stores={MemoryTier.SEMANTIC: SlotNamingStore("user_profile")},
            config=seed_config(store_slots={"user_profile": ["user_id", "team_id"]}),
        )

    assert "['team_id']" in str(exc.value)
    assert exc.value.field_name == "options.store_slots"


def test_concurrent_sync_writes_to_one_store_each_get_their_own_answer():
    """The threaded half of the `was_updated` race. The async half is next door; a lock on one
    lane only would leave a threaded deployment reading another writer's flag."""
    from concurrent.futures import ThreadPoolExecutor

    class Slow(SuspendingStore):
        """`SuspendingStore`'s sync twin. Not a `FakeStore`, whose `was_updated` is a read-only
        property computed from `processed` and so cannot show one writer clobbering another."""

        def process(self, messages, user_id=None, agent_id=None, team_id=None, **kwargs):
            self.was_updated = "changes" in messages[0]["content"]  # bookkeeping...
            time.sleep(0.01)  # ...then IO, where the other writer used to interleave

    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: Slow()}, config=seed_config())

    def write(content):
        return bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content=content, scope=ACME)])

    with ThreadPoolExecutor(max_workers=2) as pool:
        landed, no_op = list(pool.map(write, ["this changes things", "no-op"]))

    assert (landed.accepted, landed.tiers_written) == (1, [MemoryTier.SEMANTIC])
    assert (no_op.accepted, no_op.tiers_written) == (0, [])


def test_a_tier_where_every_store_fails_reports_nothing_accepted():
    """The write fan-out's other end: no store took the batch, so the partial write says so.

    The mirror of `test_a_partial_store_failure_reports_the_tier_the_sibling_committed_to` --
    that one asserts a sibling's commit survives into the receipt, this one that an empty receipt
    is what a total failure produces, rather than the tier being credited for the attempt.
    """
    stores = [FakeStore("user_profile"), FakeStore("user_memory")]
    for store in stores:
        store.process = raises_from_process(RuntimeError("store down"))
    bridge = SeedMemoryProvider(stores={MemoryTier.SEMANTIC: stores}, config=seed_config())

    with pytest.raises(PartialWriteError) as exc:
        bridge.write_tier(ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="x", scope=ACME)])

    assert exc.value.receipt.accepted == 0
    assert exc.value.receipt.tiers_written == []
    assert exc.value.receipt.details["stores"] == {}, "no store reported an outcome, so none is claimed"


async def test_a_tier_where_every_store_fails_reports_the_same_on_both_halves():
    """Settled fan-outs on both lanes must agree about a total failure, not only a partial one."""

    def wire():
        stores = [FakeStore("user_profile"), FakeStore("user_memory")]

        async def aprocess(messages, user_id=None, agent_id=None, team_id=None, **kwargs):
            await asyncio.sleep(0)
            raise RuntimeError("store down")

        for store in stores:
            store.process = raises_from_process(RuntimeError("store down"))
            store.aprocess = aprocess
        return SeedMemoryProvider(stores={MemoryTier.SEMANTIC: stores}, config=seed_config())

    events = [MemoryEvent(content="x", scope=ACME)]
    with pytest.raises(PartialWriteError) as sync_exc:
        wire().write_tier(ACME, MemoryTier.SEMANTIC, events)
    with pytest.raises(PartialWriteError) as async_exc:
        await wire().awrite_tier(ACME, MemoryTier.SEMANTIC, events)

    assert sync_exc.value.receipt.details == async_exc.value.receipt.details == {"stores": {}}
    assert sync_exc.value.receipt.accepted == async_exc.value.receipt.accepted == 0


def test_binding_a_store_that_can_never_answer_warns_at_construction():
    """`entity_memory` binds, accepts writes, and returns `[]` on every read at every scope.

    Empty is what the seam's rule calls for -- reads report absence -- so the defect is the
    *silence*: empty-because-structural looks exactly like empty-because-new, and only one is worth
    investigating. Warned at construction, where the deployment can act on it.
    """
    from egpt.learn.config import EntityMemoryConfig
    from egpt.learn.stores import EntityMemoryStore

    with pytest.warns(UserWarning, match="entity_id and entity_type"):
        bridge = SeedMemoryProvider(
            stores={MemoryTier.SEMANTIC: EntityMemoryStore(config=EntityMemoryConfig())},
            config=seed_config(),
        )

    assert bridge.read_tier(Scope(tenant_id="acme"), MemoryTier.SEMANTIC, MemoryQuery(text="acme")) == []


def test_a_slot_override_does_not_silence_a_store_that_still_cannot_answer():
    """The two facts are unrelated: declaring which slots a store filters by does not hand it an
    entity key. Silencing on an override would hide a warning that is still true."""
    from egpt.learn.config import EntityMemoryConfig
    from egpt.learn.stores import EntityMemoryStore

    with pytest.warns(UserWarning, match="every read of this tier"):
        SeedMemoryProvider(
            stores={MemoryTier.SEMANTIC: EntityMemoryStore(config=EntityMemoryConfig())},
            config=seed_config(store_slots={"entity_memory": ["user_id"]}),
        )


@pytest.mark.parametrize("learning_type", ["user_profile", "user_memory", "learned_knowledge", "decision_log"])
def test_a_store_that_can_answer_is_not_warned_about(learning_type, recwarn):
    """`learned_knowledge` also declares no honoured slots, and reads perfectly well -- it searches
    on the query text the bridge does supply. Keying the warning on an empty slot row would have
    warned about a working store."""
    SeedMemoryProvider(stores={MemoryTier.SEMANTIC: SlotNamingStore(learning_type)}, config=seed_config())

    assert [w for w in recwarn if "every read of this tier" in str(w.message)] == []
