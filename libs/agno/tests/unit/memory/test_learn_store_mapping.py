"""The six ``learn`` stores mapped into tiers, and the slots each one filters on (SM-C2 #90).

The mapping is ADR 0003 section 2.2 and is asserted against it verbatim. The slot table is the
part that can rot, because it is a claim about six other modules' behaviour -- so it is checked
against the **real stores' own signatures**, and a store that gains or loses a scope parameter
fails here rather than widening a read in production.

SPEC section 11.3 records why the table is declared rather than derived: ``LearningStore.recall``
is ``(self, **kwargs)``, so a derivation from the *protocol* yields nothing. The concrete stores do
name their parameters, which is what makes the drift check below possible.
"""

import inspect

import pytest

from egpt.learn.config import (
    DecisionLogConfig,
    EntityMemoryConfig,
    LearnedKnowledgeConfig,
    SessionContextConfig,
    UserMemoryConfig,
    UserProfileConfig,
)
from egpt.learn.stores import (
    DecisionLogStore,
    EntityMemoryStore,
    LearnedKnowledgeStore,
    SessionContextStore,
    UserMemoryStore,
    UserProfileStore,
)
from egpt.memory.config import MemoryProviderConfigError
from egpt.memory.learn_stores import (
    RECALL_SLOTS,
    SCOPE_SLOTS,
    STORE_TIERS,
    filterable_slots,
    recordable_slots,
    stores_by_tier,
    tier_for_store,
    unhonoured_slots,
)
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import Scope

#: The shipped stores, built with default configs -- enough to read `learning_type` and inspect
#: `recall`, and no db or model touched.
STORE_CLASSES = [
    (UserProfileStore, UserProfileConfig),
    (UserMemoryStore, UserMemoryConfig),
    (SessionContextStore, SessionContextConfig),
    (EntityMemoryStore, EntityMemoryConfig),
    (LearnedKnowledgeStore, LearnedKnowledgeConfig),
    (DecisionLogStore, DecisionLogConfig),
]


def real_stores():
    return [store_class(config=config_class()) for store_class, config_class in STORE_CLASSES]


# ---------------------------------------------------------------------------
# The mapping is ADR 0003 section 2.2
# ---------------------------------------------------------------------------


def test_the_mapping_is_the_one_the_adr_ratified():
    """`session_context -> WORKING`; the three user-facing stores -> SEMANTIC; the two
    instruction-shaped ones -> PROCEDURAL. Issue #90's second acceptance criterion."""
    assert STORE_TIERS == {
        "session_context": MemoryTier.WORKING,
        "user_profile": MemoryTier.SEMANTIC,
        "user_memory": MemoryTier.SEMANTIC,
        "entity_memory": MemoryTier.SEMANTIC,
        "learned_knowledge": MemoryTier.PROCEDURAL,
        "decision_log": MemoryTier.PROCEDURAL,
    }


def test_every_store_the_machine_can_build_is_mapped():
    """ "No orphan" is the issue's own testing note, checked against the shipped classes rather
    than against a list written next to the table."""
    assert {store.learning_type for store in real_stores()} == set(STORE_TIERS)


def test_no_seed_store_maps_to_the_episodic_tier():
    """A **pinned gap**: the seed has no episodic store, which is what SM-E1 (#108) builds.

    When a store lands and takes this tier, this test fails -- which is the signal to route it
    here and drop the `_reject_unserved` special case in the seed bridge.
    """
    assert MemoryTier.EPISODIC not in set(STORE_TIERS.values())


def test_an_unmapped_store_is_refused_rather_than_guessed_into_a_tier():
    """A custom store's tier is the deployment's choice; filing it wrongly stores memory as the
    wrong kind and serves it back as that kind."""
    with pytest.raises(MemoryProviderConfigError, match="No tier is mapped"):
        tier_for_store("weather_cache")


# ---------------------------------------------------------------------------
# The slot table, against the stores it describes
# ---------------------------------------------------------------------------


def recall_slots_in_signature(store):
    return {name for name in inspect.signature(store.recall).parameters if name in SCOPE_SLOTS}


@pytest.mark.parametrize("store", real_stores(), ids=lambda s: s.learning_type)
def test_a_declared_slot_is_one_the_store_actually_takes(store):
    """Declaring a slot the store has no parameter for would be a guarantee of nothing."""
    assert RECALL_SLOTS[store.learning_type] <= recall_slots_in_signature(store)


#: Slots a store takes but only honours under a configuration the default is not: both stores
#: forward `user_id` to their backend only when the effective namespace is `"user"`
#: (`user_id if effective_namespace == "user" else None`, and `_build_search_filters`). The table
#: leaves them out, so the guard fails closed; `options['store_slots']` is how a deployment running
#: that namespace declares them.
CONDITIONALLY_HONOURED = {
    "user_profile": set(),
    "user_memory": set(),
    "session_context": set(),
    "entity_memory": {"user_id"},
    "learned_knowledge": {"user_id"},
    "decision_log": set(),
}


@pytest.mark.parametrize("store", real_stores(), ids=lambda s: s.learning_type)
def test_every_slot_a_store_takes_is_either_declared_or_a_known_conditional(store):
    """The drift check. A store gaining a scope parameter lands in neither set and fails here,
    which is the moment to decide whether the table should now honour it."""
    undeclared = recall_slots_in_signature(store) - RECALL_SLOTS[store.learning_type]

    assert undeclared == CONDITIONALLY_HONOURED[store.learning_type]


def test_the_decision_log_takes_no_user_id_at_all():
    """The sharpest row in the table, and the one that motivated it: a user-scoped read of the
    decision log is not narrow-but-unenforced, it is unanswerable."""
    decision_log = DecisionLogStore(config=DecisionLogConfig())

    assert "user_id" not in inspect.signature(decision_log.recall).parameters


@pytest.mark.parametrize("store", real_stores(), ids=lambda s: s.learning_type)
def test_the_write_side_slot_set_is_derived_from_each_store_s_process(store):
    """`recall` and `process` take different slot sets, and the write guard reads the second.

    Derived rather than declared, and the two questions differ in strength: on the read side the
    table says which slots a store *filters* by, which a signature cannot tell you; on the write
    side the question is only whether the store **sees** the slot, which the signature answers
    exactly. Whether a seen slot survives into the stored row is a columns question -- SM-C3's
    (#91) -- and is what the guard deliberately does not claim.
    """
    named = {name for name in inspect.signature(store.process).parameters if name in SCOPE_SLOTS}

    assert recordable_slots(store) == named


def test_no_store_records_a_slot_it_does_not_name():
    """`user_profile` is the case the write guard exists for: `process` names no `session_id`, so a
    session-scoped write to it is refused rather than stored without its session."""
    profile = UserProfileStore(config=UserProfileConfig())

    assert "session_id" not in recordable_slots(profile)
    assert {"user_id", "agent_id", "team_id"} <= recordable_slots(profile)


@pytest.mark.parametrize("store", real_stores(), ids=lambda s: s.learning_type)
def test_a_declared_override_can_only_name_slots_the_store_takes(store):
    """`filterable_slots` is what makes `options['store_slots']` checkable rather than trusted.

    A slot arriving through `**kwargs` is ignored by every store in the tree, so a declaration
    naming one would widen a read while it kept carrying the narrow scope -- the leak the guard
    exists to close, reopened through its own escape hatch.
    """
    named = {name for name in inspect.signature(store.recall).parameters if name in SCOPE_SLOTS}

    assert filterable_slots(store) == named
    assert RECALL_SLOTS[store.learning_type] <= filterable_slots(store)


def test_the_decision_log_cannot_be_declared_to_filter_on_a_user():
    """The sharpest case: `user_id` reaches `decision_log.recall` only through `**kwargs`."""
    decision_log = DecisionLogStore(config=DecisionLogConfig())

    assert "user_id" not in filterable_slots(decision_log)


# ---------------------------------------------------------------------------
# unhonoured_slots: the predicate the seed bridge refuses on
# ---------------------------------------------------------------------------


def test_a_scope_the_store_can_filter_reports_nothing_unhonoured():
    assert unhonoured_slots("user_profile", Scope(tenant_id="acme", user_id="u1")) == []


def test_a_slot_the_store_ignores_is_reported_in_slot_order():
    """Ordered, so the message and the `field_name` are the same on every run."""
    narrow = Scope(tenant_id="acme", user_id="u1", agent_id="a1", session_id="s1")

    assert unhonoured_slots("user_profile", narrow) == ["agent_id", "session_id"]


def test_an_unset_slot_is_never_reported():
    """`None` means "any", so it constrains nothing and there is nothing for a store to honour."""
    assert unhonoured_slots("decision_log", Scope(tenant_id="acme")) == []


def test_the_tenant_is_not_a_store_slot():
    """No store takes a tenant. The seed bridge pins one instance to one tenant instead."""
    assert "tenant_id" not in SCOPE_SLOTS
    assert unhonoured_slots("user_profile", Scope(tenant_id="acme")) == []


def test_an_unknown_store_is_treated_as_honouring_nothing():
    """Fail-closed: there is nothing to derive an honoured set from, and assuming the slots apply
    is what turns a wide answer into one wearing a narrow label."""
    assert unhonoured_slots("custom", Scope(tenant_id="acme", user_id="u1")) == ["user_id"]


def test_a_declared_override_replaces_the_table_rather_than_extending_it():
    """A deployment running the `"user"` namespace knows more than the table can; one running a
    *narrower* store than the default knows less, and must be able to say so."""
    scope = Scope(tenant_id="acme", user_id="u1")

    assert unhonoured_slots("entity_memory", scope, declared=frozenset({"user_id"})) == []
    assert unhonoured_slots("user_profile", scope, declared=frozenset()) == ["user_id"]


# ---------------------------------------------------------------------------
# stores_by_tier: a machine's stores, grouped
# ---------------------------------------------------------------------------


class Store:
    def __init__(self, learning_type):
        self.learning_type = learning_type


def test_a_tier_holds_every_store_mapped_to_it():
    """Three stores are semantic and two procedural, so one-store-per-tier could not express the
    ratified mapping at all."""
    grouped = stores_by_tier({name: Store(name) for name in STORE_TIERS})

    assert [s.learning_type for s in grouped[MemoryTier.SEMANTIC]] == ["user_profile", "user_memory", "entity_memory"]
    assert [s.learning_type for s in grouped[MemoryTier.PROCEDURAL]] == ["learned_knowledge", "decision_log"]
    assert [s.learning_type for s in grouped[MemoryTier.WORKING]] == ["session_context"]
    assert MemoryTier.EPISODIC not in grouped


def test_the_order_within_a_tier_comes_from_the_table_not_the_machine():
    """Read order decides which store's record lands first in a capped slice, so it must not
    depend on the order stores happened to be enabled in."""
    reversed_machine = {name: Store(name) for name in reversed(list(STORE_TIERS))}

    grouped = stores_by_tier(reversed_machine)

    assert [s.learning_type for s in grouped[MemoryTier.SEMANTIC]] == ["user_profile", "user_memory", "entity_memory"]


def test_a_machine_carrying_an_unmapped_store_is_refused():
    with pytest.raises(MemoryProviderConfigError, match=r"\['weather_cache'\]"):
        stores_by_tier({"user_memory": Store("user_memory"), "weather_cache": Store("weather_cache")})


def test_a_store_the_caller_already_bound_is_not_refused_for_being_unmapped():
    """Its tier is decided, so there is nothing left to guess -- and refusing it would leave a
    custom store with no way to be used alongside a machine at all."""
    custom = Store("weather_cache")

    grouped = stores_by_tier({"user_memory": Store("user_memory"), "weather_cache": custom}, already_bound=[custom])

    assert MemoryTier.SEMANTIC in grouped
    assert custom not in grouped.get(MemoryTier.SEMANTIC, ()), "an exempt store is not also mapped here"


def test_a_machine_with_no_stores_maps_to_nothing():
    assert stores_by_tier({}) == {}


def test_the_unanswerable_table_names_only_stores_the_bridge_cannot_supply_for():
    """Grounded in the store's own signature, not in the slot table.

    A store belongs here when its `recall` takes a parameter it needs and `_recall_kwargs` has
    nowhere to source -- `entity_id` / `entity_type`. `learned_knowledge` also declares no honoured
    slots, but everything its `recall` needs is the query text, which the bridge supplies; keying
    the warning on an empty `RECALL_SLOTS` row would have warned about a store that works.
    """
    from egpt.memory.learn_stores import UNANSWERABLE_STORES

    supplied = {"user_id", "session_id", "agent_id", "team_id", "message", "query"}
    for store in real_stores():
        needs_unsuppliable = {
            name
            for name in inspect.signature(store.recall).parameters
            if name in {"entity_id", "entity_type"} and name not in supplied
        }
        assert bool(needs_unsuppliable) == (store.learning_type in UNANSWERABLE_STORES), store.learning_type


def test_an_unanswerable_store_really_does_answer_nothing():
    """The claim itself, against the real store and the real kwargs the bridge sends."""
    store = EntityMemoryStore(config=EntityMemoryConfig())

    assert (
        store.recall(user_id="u1", session_id=None, agent_id=None, team_id=None, message="acme", query="acme") is None
    )
