"""The six ``learn`` stores mapped into the CoALA tiers (SM-C2 #90).

ADR 0003 section 2.2's mapping as data, plus what it was missing (SPEC section 11.3): which scope
slots each store's ``recall`` actually filters on. Without that the seed bridge handed a bound
store all four slots and could not tell which were honoured, so a narrowed read came back wider,
stamped with the requesting scope.

Here rather than in ``egpt.learn`` because ``memory`` already imports ``learn``; reversing that
risks an import cycle through ``egpt/memory/__init__``.
"""

from typing import TYPE_CHECKING, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from egpt.memory.errors import MemoryOperationError
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import Scope

if TYPE_CHECKING:
    from egpt.learn.stores.protocol import LearningStore

__all__ = [
    "RECALL_SLOTS",
    "SCOPE_SLOTS",
    "STORE_TIERS",
    "stores_by_tier",
    "tier_for_store",
    "unhonoured_slots",
]

#: The slots a ``LearningStore`` could narrow a read by. No store takes a ``tenant_id``; the seed
#: bridge pins one instance to one tenant instead (SM-C3 #91 adds the column).
SCOPE_SLOTS: Tuple[str, ...] = ("user_id", "agent_id", "team_id", "session_id")


#: ADR 0003 section 2.2, ratified. Declaration order is the order stores are consulted within a
#: tier, so a multi-store read is deterministic without sorting on anything the machine controls.
#: **No store maps to EPISODIC** -- that absence is the mapping's finding, and SM-E1 (#108) fills it.
STORE_TIERS: Dict[str, MemoryTier] = {
    "session_context": MemoryTier.WORKING,
    "user_profile": MemoryTier.SEMANTIC,
    "user_memory": MemoryTier.SEMANTIC,
    "entity_memory": MemoryTier.SEMANTIC,
    "learned_knowledge": MemoryTier.PROCEDURAL,
    "decision_log": MemoryTier.PROCEDURAL,
}


#: Which slots each store's ``recall`` narrows by. The rest it absorbs, documented in the stores as
#: *"Additional context (ignored)"*.
#:
#: Two rows are **empty on purpose**, not for want of a parameter: ``entity_memory`` and
#: ``learned_knowledge`` both take ``user_id`` and forward it only under the ``"user"`` namespace,
#: which the default is not. A set true in the minority case reads as a guarantee, so those
#: deployments declare it via ``options['store_slots']`` instead.
#:
#: Read predicate only. Whether ``process`` **persists** a slot is a columns question, SM-C3's
#: (#91), pinned in ``test_learn_store_mapping.py``.
RECALL_SLOTS: Dict[str, FrozenSet[str]] = {
    "session_context": frozenset({"session_id"}),
    "user_profile": frozenset({"user_id"}),
    "user_memory": frozenset({"user_id"}),
    "entity_memory": frozenset(),
    "learned_knowledge": frozenset(),
    "decision_log": frozenset({"agent_id", "session_id"}),  # takes no `user_id` parameter at all
}


def tier_for_store(learning_type: str) -> MemoryTier:
    """The tier a seed store belongs to, per ADR 0003 section 2.2.

    Unmapped raises rather than defaults: a guess files memory as a kind nobody chose, and the
    read side then serves it as that kind.
    """
    try:
        return STORE_TIERS[learning_type]
    except (KeyError, TypeError):
        raise MemoryOperationError(
            f"No tier is mapped for a store of type {learning_type!r}. ADR 0003 section 2.2 maps "
            f"the six seed stores ({', '.join(sorted(STORE_TIERS))}); a custom store's tier is the "
            "deployment's choice, so bind it explicitly with stores={MemoryTier...: store}.",
            field_name="learning_type",
        ) from None


def unhonoured_slots(learning_type: str, scope: Scope, declared: Optional[FrozenSet[str]] = None) -> List[str]:
    """The slots ``scope`` sets that this store will not filter on, in :data:`SCOPE_SLOTS` order.

    Empty means the store can answer the scope exactly. ``declared`` overrides the table for a
    store configured to honour more, or less, than the default.

    An unmapped store honours **nothing**: ``recall`` is declared ``(self, **kwargs)``, so there is
    nothing to derive from, and assuming the slots apply is what widens the answer under a narrow
    label.
    """
    honoured = declared if declared is not None else RECALL_SLOTS.get(learning_type, frozenset())
    return [slot for slot in SCOPE_SLOTS if getattr(scope, slot, None) is not None and slot not in honoured]


def stores_by_tier(
    stores: Mapping[str, "LearningStore"],
    already_bound: Sequence["LearningStore"] = (),
) -> Dict[MemoryTier, Tuple["LearningStore", ...]]:
    """Group a ``LearningMachine.stores`` dict into tiers, in :data:`STORE_TIERS` order.

    A tier holds **several** stores -- three semantic, two procedural -- so this returns a tuple per
    tier; collapsing to one makes the ratified mapping unusable for any real machine.
    ``already_bound`` exempts stores the caller bound itself, by identity: their tier is decided.
    """
    exempt = {id(store) for store in already_bound}
    unmapped = sorted(name for name, store in stores.items() if name not in STORE_TIERS and id(store) not in exempt)
    if unmapped:
        raise MemoryOperationError(
            f"LearningMachine carries store(s) {unmapped} that ADR 0003 section 2.2 does not map to "
            "a tier. Bind them explicitly with stores={MemoryTier...: store}, or drop them from the "
            "machine -- routing a store to a guessed tier files its memory as the wrong kind.",
            field_name="stores",
        )
    grouped: Dict[MemoryTier, List["LearningStore"]] = {}
    for name in STORE_TIERS:
        store = stores.get(name)
        if store is not None:
            grouped.setdefault(STORE_TIERS[name], []).append(store)
    return {tier: tuple(bound) for tier, bound in grouped.items()}
