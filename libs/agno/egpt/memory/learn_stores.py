"""The six ``learn`` stores mapped into the CoALA tiers (SM-C2 #90).

ADR 0003 section 2.2's mapping as data, plus what it was missing (SPEC section 11.3): which scope
slots each store's ``recall`` actually filters on. Without that the seed bridge handed a bound
store all four slots and could not tell which were honoured, so a narrowed read came back wider,
stamped with the requesting scope.

Here rather than in ``egpt.learn`` because ``memory`` already imports ``learn``; reversing that
risks an import cycle through ``egpt/memory/__init__``.
"""

import inspect
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from egpt.memory.config import MemoryProviderConfigError
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import Scope

if TYPE_CHECKING:
    from egpt.learn.stores.protocol import LearningStore

__all__ = [
    "RECALL_SLOTS",
    "STORE_FILTERABLE_SLOTS",
    "STORE_TIERS",
    "UNANSWERABLE_STORES",
    "filterable_slots",
    "recordable_slots",
    "stores_by_tier",
    "tier_for_store",
    "unhonoured_slots",
]

#: The slots a ``LearningStore`` could narrow a read by. No store takes a ``tenant_id``; the seed
#: bridge pins one instance to one tenant instead.
#:
#: Deliberately **not** named ``SCOPE_SLOTS``: ``memory/types.py`` declares a tuple by that name
#: holding every slot a row is partitioned by, tenant included. Two same-named constants of
#: different length one import apart is the confusion a shared declaration exists to remove, and
#: the subset that silently omits the tenant is the worse one to reach for by muscle memory.
STORE_FILTERABLE_SLOTS: Tuple[str, ...] = ("user_id", "agent_id", "team_id", "session_id")


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
#: Read predicate only, and declared because a signature cannot tell you what a store *filters* by.
#: The write side asks the weaker question -- does ``process`` even name the slot -- which
#: :func:`recordable_slots` derives. Whether a named slot survives into the row is a columns
#: question, SM-C3's (#91).
RECALL_SLOTS: Dict[str, FrozenSet[str]] = {
    "session_context": frozenset({"session_id"}),
    "user_profile": frozenset({"user_id"}),
    "user_memory": frozenset({"user_id"}),
    "entity_memory": frozenset(),
    "learned_knowledge": frozenset(),
    "decision_log": frozenset({"agent_id", "session_id"}),  # takes no `user_id` parameter at all
}


#: Stores whose ``recall`` needs an argument the seam has nowhere to carry, mapped to what it
#: needs. A bridge can bind these -- their ``process`` works, so writes land -- but every read
#: returns empty at every scope, which is indistinguishable from "nothing stored yet".
#:
#: Only ``entity_memory``: it returns ``None`` unless given **both** ``entity_id`` and
#: ``entity_type``, and ``MemoryQuery`` carries neither. Deliberately *not* keyed on an empty
#: :data:`RECALL_SLOTS` row -- ``learned_knowledge`` has one too and reads perfectly well, because
#: it searches on the query text the bridge does supply. The two facts are unrelated, and
#: conflating them would warn about a store that works.
UNANSWERABLE_STORES: Dict[str, str] = {
    "entity_memory": "entity_id and entity_type",
}


def tier_for_store(learning_type: str) -> MemoryTier:
    """The tier a seed store belongs to, per ADR 0003 section 2.2.

    Unmapped raises rather than defaults: a guess files memory as a kind nobody chose, and the
    read side then serves it as that kind.

    ``MemoryProviderConfigError``, not ``MemoryOperationError``: which stores a tier holds is
    settled when the provider is built, so this is a deployment fault rather than the per-request
    refusal that error is documented as.
    """
    try:
        return STORE_TIERS[learning_type]
    except (KeyError, TypeError):
        raise MemoryProviderConfigError(
            f"No tier is mapped for a store of type {learning_type!r}. ADR 0003 section 2.2 maps "
            f"the six seed stores ({', '.join(sorted(STORE_TIERS))}); a custom store's tier is the "
            "deployment's choice, so bind it explicitly with stores={MemoryTier...: store}.",
            field_name="stores",
        ) from None


def unhonoured_slots(learning_type: str, scope: Scope, declared: Optional[FrozenSet[str]] = None) -> List[str]:
    """The slots ``scope`` sets that this store will not filter on, in :data:`STORE_FILTERABLE_SLOTS` order.

    Empty means the store can answer the scope exactly. ``declared`` overrides the table for a
    store configured to honour more, or less, than the default.

    An unmapped store honours **nothing**: ``recall`` is declared ``(self, **kwargs)``, so there is
    nothing to derive from, and assuming the slots apply is what widens the answer under a narrow
    label.
    """
    honoured = declared if declared is not None else RECALL_SLOTS.get(learning_type, frozenset())
    return [slot for slot in STORE_FILTERABLE_SLOTS if getattr(scope, slot, None) is not None and slot not in honoured]


def filterable_slots(store: Any) -> FrozenSet[str]:
    """The scope slots this store's ``recall`` could possibly filter by: its **named parameters**.

    ``**kwargs`` does not count, and that is the whole point. Every in-tree store accepts
    ``**kwargs`` and documents what lands there as *"Additional context (ignored)"* -- so a slot
    reaching a store through ``**kwargs`` is, by construction, one it does not filter on.
    ``decision_log`` swallows ``user_id`` exactly that way.

    This is what makes a declared override checkable rather than taken on trust: a deployment may
    say a store honours a slot the default table leaves out (``entity_memory`` under the ``"user"``
    namespace), but it cannot say a store honours a slot the store never named.
    """
    try:
        parameters = inspect.signature(store.recall).parameters
    except (TypeError, ValueError):  # a callable without an introspectable signature
        return frozenset()
    return frozenset(name for name in parameters if name in STORE_FILTERABLE_SLOTS)


def recordable_slots(store: Any) -> FrozenSet[str]:
    """The scope slots this store's ``process`` could possibly record: its **named parameters**.

    The write-side twin of :func:`filterable_slots`, and it answers a narrower question than the
    read side's. Naming a slot means the store *sees* it; whether the row it writes keeps it is a
    columns question and SM-C3's (#91). What this closes is the case where the store cannot even
    see it -- ``user_profile.process`` names no ``session_id``, so a session-scoped write to it is
    accepted, stored without its session, and can never be read back at that scope, which is
    exactly what ``_reject_unstorable_scope`` refuses on the manager path.
    """
    try:
        parameters = inspect.signature(store.process).parameters
    except (TypeError, ValueError):  # a callable without an introspectable signature
        return frozenset()
    return frozenset(name for name in parameters if name in STORE_FILTERABLE_SLOTS)


def stores_by_tier(
    stores: Mapping[str, "LearningStore"],
    already_bound: Sequence["LearningStore"] = (),
) -> Dict[MemoryTier, Tuple["LearningStore", ...]]:
    """Group a ``LearningMachine.stores`` dict into tiers, in :data:`STORE_TIERS` order.

    A tier holds **several** stores -- three semantic, two procedural -- so this returns a tuple per
    tier; collapsing to one makes the ratified mapping unusable for any real machine.

    ``already_bound`` names stores the caller has placed itself, by identity, and they are left out
    **entirely** -- not merely exempted from the refusal below. A store bound to one tier while
    still sitting under its native key on the machine would otherwise be returned for its mapped
    tier as well, and answer both.
    """
    exempt = {id(store) for store in already_bound}
    unmapped = sorted(name for name, store in stores.items() if name not in STORE_TIERS and id(store) not in exempt)
    if unmapped:
        raise MemoryProviderConfigError(
            f"LearningMachine carries store(s) {unmapped} that ADR 0003 section 2.2 does not map to "
            "a tier. Bind them explicitly with stores={MemoryTier...: store}, or drop them from the "
            "machine -- routing a store to a guessed tier files its memory as the wrong kind.",
            field_name="stores",
        )
    grouped: Dict[MemoryTier, List["LearningStore"]] = {}
    for name in STORE_TIERS:
        store = stores.get(name)
        if store is not None and id(store) not in exempt:
            grouped.setdefault(STORE_TIERS[name], []).append(store)
    return {tier: tuple(bound) for tier, bound in grouped.items()}
