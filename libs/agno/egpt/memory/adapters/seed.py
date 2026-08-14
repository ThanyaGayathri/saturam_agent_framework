"""The seed bridge: today's memory behind the new seam (SM-C1).

ADR 0003 section 2 decided **WRAP / EXTEND, never replace**. This module is that decision in code:
a deployment gets the ``MemoryProvider`` API over the memory it already has
(``LearningMachine``'s stores and ``MemoryManager``'s ``UserMemory`` CRUD), with no pgvector, no
graph, and no migration.

Most of this file is refusals, and that is the point. The seed cannot honour tenancy, valid time,
substrates, or an episodic tier, so each of those is rejected at the boundary rather than accepted
and ignored -- an ignored parameter returns a wrong answer that looks right, which is the one
failure the layer above cannot see.

Which store serves which tier, which scope slots each one's ``recall`` filters on, and which its
``process`` can record, is :mod:`egpt.memory.learn_stores` (SM-C2 #90) -- reads and writes are both
refused on a slot the bound store cannot honour. ``Curator.deduplicate`` has no async variant, so
``aconsolidate`` hops a thread; giving it one is SM-L4's (#125).
"""

import asyncio
import threading
import warnings
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, Iterable, List, NoReturn, Optional, Sequence, Tuple, Union, cast
from uuid import NAMESPACE_OID, uuid5
from weakref import WeakKeyDictionary

from egpt.db.schemas import UserMemory
from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError, register_provider
from egpt.memory.learn_stores import (
    STORE_FILTERABLE_SLOTS,
    UNANSWERABLE_STORES,
    filterable_slots,
    recordable_slots,
    stores_by_tier,
    unhonoured_slots,
)
from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import MemoryTier, policy_for_tier
from egpt.memory.types import (
    ConsolidationReport,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    MemorySlice,
    PartialWriteError,
    Scope,
    Substrate,
    WriteReceipt,
    consolidation_details,
    fusion_diagnostics,
    write_details,
)
from egpt.memory.validation import reject_duplicate_ids, reject_foreign_scopes, require_scope

if TYPE_CHECKING:
    from egpt.learn.machine import LearningMachine
    from egpt.learn.stores.protocol import LearningStore
    from egpt.memory.manager import MemoryManager

    #: What a tier may be bound to: one store, or several.
    StoreBinding = Union["LearningStore", Sequence["LearningStore"]]

__all__ = ["SeedMemoryProvider"]

#: Namespace for projecting an id-less ``UserMemory``, so the same row projects to the same id in
#: every worker process and across restarts.
_PROJECTION_NAMESPACE = uuid5(NAMESPACE_OID, "egpt.memory.seed.projection")


def _group_by_tier(events: Sequence[MemoryEvent]) -> List[Tuple[MemoryTier, List[MemoryEvent]]]:
    """Split a batch by ``event.tier``, preserving first-seen tier order."""
    grouped: Dict[MemoryTier, List[MemoryEvent]] = {}
    for event in events:
        grouped.setdefault(event.tier, []).append(event)
    return list(grouped.items())


def _provenance_note(event: MemoryEvent) -> Optional[str]:
    """Fold role and metadata into ``UserMemory.input``, the one free-text field the seed offers.

    Lossy, and preferable to dropping them: the seed schema has nowhere structured to put either
    (SM-C3 #91 adds columns), and a write that reports ``accepted`` while discarding the caller's
    metadata is a gap nobody notices until they need it.
    """
    parts = []
    if event.role:
        parts.append(f"role={event.role}")
    if event.metadata:
        parts.append(f"metadata={event.metadata}")
    return "; ".join(parts) or None


def _learning_type(store: Any) -> str:
    """A bound store's type name, required and required to be a string.

    The projection id (``learning_type:tier:scope``) and the honoured-slot lookup both key off it,
    and both fail quietly without one: ids read ``None:semantic:...`` and collide, and the lookup
    misses the table and refuses every narrowed read.
    """
    learning_type = getattr(store, "learning_type", None)
    if not isinstance(learning_type, str) or not learning_type:
        raise MemoryProviderConfigError(
            f"A bound store of type {type(store).__name__} has no `learning_type`; the bridge keys "
            "its record ids and its scope-slot lookup on that name.",
            field_name="stores",
        )
    return learning_type


def _record_from_user_memory(memory: UserMemory, scope: Scope) -> MemoryRecord:
    """Project a seed ``UserMemory`` into the seam's read-projection.

    ``UserMemory`` has a ``tenant_id`` column since SM-C3 (#91) but nothing populates or filters
    on it yet, and it has no temporal fields at all, so the tenant comes from the caller's scope
    and the bi-temporal fields stay ``None`` -- which reads correctly as "valid since forever,
    never invalidated, current belief".
    """
    return MemoryRecord(
        # A row with no id still needs a stable one: `MemoryRecord.id` is the dedup key. Derived
        # from content rather than `hash()`, which is salted per process.
        id=memory.memory_id or str(uuid5(_PROJECTION_NAMESPACE, f"{scope.key()}|{memory.memory}")),
        tier=MemoryTier.SEMANTIC,
        content=memory.memory,
        scope=scope,
        topics=memory.topics,
        created_at=memory.created_at,
        metadata={"input": memory.input} if memory.input else {},
    )


class SeedMemoryProvider(MemoryProvider):
    """A ``MemoryProvider`` backed by the seed's existing memory, wrapping rather than replacing.

    Tier routing is ADR 0003 section 2.2's mapping, held as data in
    :mod:`egpt.memory.learn_stores`:

    * ``WORKING`` -> ``session_context``.
    * ``SEMANTIC`` -> ``user_profile`` / ``user_memory`` / ``entity_memory``, or -- with no store
      bound -- ``MemoryManager``'s ``UserMemory`` CRUD.
    * ``PROCEDURAL`` -> ``learned_knowledge`` / ``decision_log``.
    * ``EPISODIC`` -> **nothing**. The seed has no episodic store (SM-E1 #108). Writing it is
      refused rather than redirected into semantic, which would destroy the raw, non-lossy record
      consolidation depends on.

    A tier holds **several** stores, because three of the six are semantic and two procedural.
    Passing a ``LearningMachine`` binds its enabled stores by that mapping; ``stores`` overrides a
    tier outright, and accepts one store or a sequence.
    """

    provider_key = "seed"

    def __init__(
        self,
        manager: Optional["MemoryManager"] = None,
        machine: Optional["LearningMachine"] = None,
        stores: Optional[Dict[MemoryTier, "StoreBinding"]] = None,
        config: Optional[MemoryProviderConfig] = None,
    ) -> None:
        self.stores: Dict[MemoryTier, Tuple["LearningStore", ...]] = self._bind_stores(stores, machine)
        # Checked against what actually bound, not against the argument: `stores={tier: []}` is a
        # dict a truthiness test calls non-empty while binding nothing.
        if manager is None and machine is None and not any(self.stores.values()):
            raise MemoryProviderConfigError(
                "SeedMemoryProvider needs a MemoryManager, a LearningMachine, or at least one bound store.",
                field_name="provider",
            )
        self.manager = manager
        self.machine = machine
        self.config = config or MemoryProviderConfig(provider=self.provider_key)
        self._store_slots: Dict[str, FrozenSet[str]] = _checked_slots(self.config.options.get("store_slots"))
        self._reject_undeclarable_slots()
        self._warn_about_unanswerable_stores()
        #: Each bound store's write-side slot set, resolved once. The store set is fixed at
        #: construction and `inspect.signature` is not free, so deriving it per write put signature
        #: introspection on the hot path for an answer that cannot change.
        #:
        #: The read side needs no such map: it reads the declared table, and `filterable_slots` is
        #: called once per store at construction to check that declaration.
        self._recordable: Dict[int, FrozenSet[str]] = {
            id(store): recordable_slots(store) for group in self.stores.values() for store in group
        }
        #: One lock per bound store, so a write and its `was_updated` read are one critical
        #: section. Built here because the store set is fixed at construction, which keeps the
        #: lookup free of its own race.
        #:
        #: **Within a lane, not across them.** This one serialises threaded callers; the async map
        #: below serialises coroutines. A deployment driving *one store* from a thread pool and an
        #: event loop at the same time is not excluded, and would need the async side to acquire
        #: this lock through a worker thread -- a hop on every write, to close a shape
        #: ``LearningStore`` does not support in the first place.
        self._write_locks: Dict[int, threading.Lock] = {
            id(store): threading.Lock() for group in self.stores.values() for store in group
        }
        #: The async twin, per loop: an ``asyncio.Lock`` binds to the loop that first awaits it,
        #: and one provider may be driven from more than one. Keyed **weakly** by the loop, so a
        #: finished loop's locks are collected with it rather than accumulating for the life of the
        #: provider -- a long-lived provider across many short-lived loops is the ordinary shape in
        #: tests and in anything using ``asyncio.run`` per request.
        self._awrite_locks: "WeakKeyDictionary[asyncio.AbstractEventLoop, Dict[int, asyncio.Lock]]" = (
            WeakKeyDictionary()
        )
        self._require_single_tenant_acknowledgement()
        self._tenant: str = self._require_declared_tenant()

    @staticmethod
    def _bind_stores(
        stores: Optional[Dict[MemoryTier, "StoreBinding"]],
        machine: Optional["LearningMachine"],
    ) -> Dict[MemoryTier, Tuple["LearningStore", ...]]:
        """Explicit bindings first, then whatever the machine's mapping adds.

        A tier named explicitly is **replaced**, not extended: appending the machine's stores to it
        would give the caller a read they did not ask for. Duplicate ``learning_type`` within one
        tier is refused -- a store's projection id is ``learning_type:tier:scope``, so two of them
        collide into one record.

        **Naming a tier is what claims it, not binding a store to it.** An empty binding
        (``{SEMANTIC: []}``) is how a deployment says "serve nothing here", so it is recorded as an
        empty group rather than skipped -- skipping it left the key absent, and the machine below
        then filled the tier with exactly the stores the caller was removing.
        """
        bound: Dict[MemoryTier, Tuple["LearningStore", ...]] = {}
        for tier, value in (stores or {}).items():
            if not isinstance(tier, MemoryTier):
                raise MemoryProviderConfigError(f"stores key {tier!r} is not a MemoryTier.", field_name="stores")
            # `cast` because the union's non-sequence arm is what remains after the list/tuple
            # test, and mypy cannot narrow a Protocol out of it. `_learning_type` is the runtime
            # check that a bound value really is a store, and it is tested.
            bound[tier] = tuple(value) if isinstance(value, (list, tuple)) else (cast("LearningStore", value),)
        if machine is not None:
            already = [store for group in bound.values() for store in group]
            for tier, group in stores_by_tier(getattr(machine, "stores", {}) or {}, already_bound=already).items():
                bound.setdefault(tier, group)
        for tier, group in bound.items():
            types = [_learning_type(store) for store in group]
            duplicates = sorted({t for t in types if types.count(t) > 1})
            if duplicates:
                raise MemoryProviderConfigError(
                    f"Tier {tier.value!r} has {len(group)} stores but repeats learning_type "
                    f"{duplicates}: their read projections would share one record id and overwrite "
                    "each other.",
                    field_name="stores",
                )
        return bound

    def _awrite_lock(self, store: "LearningStore") -> asyncio.Lock:
        """This store's lock for the running loop, created on the loop that will await it."""
        per_store = self._awrite_locks.setdefault(asyncio.get_running_loop(), {})
        lock = per_store.get(id(store))
        if lock is None:
            lock = per_store[id(store)] = asyncio.Lock()
        return lock

    # -- construction guards --------------------------------------------------

    def _warn_about_unanswerable_stores(self) -> None:
        """Say so, at construction, when a bound store's reads can only ever be empty.

        ``entity_memory.recall`` returns ``None`` without ``entity_id`` **and** ``entity_type``, and
        :meth:`_recall_kwargs` supplies neither -- there is nowhere in ``MemoryQuery`` to put them.
        A deployment that enables the store on its ``LearningMachine`` therefore gets a tier that
        binds, passes every guard, accepts writes, and returns ``[]`` on every read.

        Warned rather than refused: ``process`` works, so refusing the binding would discard
        writes that do land, and an empty read is what the seam's own rule calls for -- reads
        report absence. What is wrong is the *silence*: empty-because-structural is
        indistinguishable from empty-because-new, and only one of those is worth investigating.
        Not suppressed by ``store_slots`` either, since declaring slots does not supply an entity
        key; the standard ``warnings`` filters are how a deployment that knows silences it.
        """
        for tier, group in self.stores.items():
            for store in group:
                needs = UNANSWERABLE_STORES.get(_learning_type(store))
                if needs is None:
                    continue
                warnings.warn(
                    f"The {_learning_type(store)!r} store is bound to the {tier.value!r} tier, but "
                    f"its recall needs {needs}, which MemoryQuery cannot carry -- every read of "
                    "this tier through this provider will be empty at every scope. Writes are "
                    "unaffected. Retrieval for it needs a query envelope that carries an entity "
                    "key.",
                    stacklevel=3,
                )

    def _reject_undeclarable_slots(self) -> None:
        """Refuse a ``store_slots`` declaration the bound store's ``recall`` cannot possibly honour.

        The override exists because the table cannot see a store's configuration -- ``entity_memory``
        filters on ``user_id`` under the ``"user"`` namespace and not otherwise, and only the
        deployment knows which. It is **not** a way to assert a slot the store never named: a slot
        that reaches ``recall`` through ``**kwargs`` is ignored by every store in the tree, so
        declaring ``user_id`` for ``decision_log`` -- which has no such parameter -- would hand back
        one user's rows under another's scope, which is the exact leak this guard exists to close.

        Checked at construction against the store actually bound, so a deployment that gets it wrong
        fails at build rather than serving a wide answer under a narrow label.
        """
        for tier, group in self.stores.items():
            for store in group:
                learning_type = _learning_type(store)
                declared = self._store_slots.get(learning_type)
                if declared is None:
                    continue
                undeclarable = sorted(declared - filterable_slots(store))
                if undeclarable:
                    raise MemoryProviderConfigError(
                        f"options['store_slots'][{learning_type!r}] declares {undeclarable}, but that "
                        f"store's recall names no such parameter -- a slot arriving through **kwargs "
                        f"is ignored, so the declaration would widen the {tier.value!r} tier's reads "
                        "while they keep carrying the narrower scope. Declare only slots the store "
                        "takes by name.",
                        field_name="options.store_slots",
                    )

    def _require_single_tenant_acknowledgement(self) -> None:
        """Refuse to build unless the deployment states it is single-tenant.

        ``MemoryManager`` partitions on ``user_id`` alone, so ``Scope.tenant_id`` is accepted on
        every call and applied on none. SM-C3 (#91) added the ``tenant_id`` column, but a column
        no query filters on changes nothing here::

            write(Scope(tenant_id='acme',   user_id='ada')) -> "acme Q4 revenue is 12M"
            read (Scope(tenant_id='globex', user_id='ada')) -> ["acme Q4 revenue is 12M"]

        The relabel is what makes it critical rather than merely absent: the projection stamps the
        *reader's* scope onto the row, so every check above this layer sees a well-formed ``globex``
        record.

        Namespacing the manager key (``f"{tenant}/{user}"``) is the tempting alternative and is
        worse: it orphans every row an existing deployment already wrote, with no error anywhere.
        Threading the column through the manager and every backend is aicoe-tracker#175.
        """
        if not self.config.options.get("single_tenant"):
            raise MemoryProviderConfigError(
                "SeedMemoryProvider cannot enforce tenant isolation: MemoryManager partitions "
                "by user_id alone, so Scope.tenant_id is accepted and never applied. The "
                "UserMemory.tenant_id column exists but no query filters on it. Two tenants "
                "sharing a user_id would read each "
                "other's memories, and the projection would stamp the reader's tenant onto the "
                "result, hiding the leak from every check above this layer.\n\n"
                "If this deployment serves exactly one tenant, say so explicitly:\n"
                '    MemoryProviderConfig(provider="seed", options={"single_tenant": True, '
                '"tenant_id": "acme"})\n\n'
                "For real multi-tenant memory use the co_located provider, or wait for the "
                "tenant-scoped read/write path (aicoe-tracker#175).",
                field_name="options.single_tenant",
            )

    def _require_declared_tenant(self) -> str:
        """The one tenant this instance may ever serve. **Mandatory, and from config.**

        Mandatory because an unpinned instance has no isolation at all -- the guard below would
        have nothing to compare against. From config rather than pinned on the first request,
        because that state is per-instance (each worker pins its own tenant and serves it happily)
        and the check-then-set admits two tenants under thread interleaving.
        """
        declared = self.config.options.get("tenant_id")
        if not isinstance(declared, str) or not declared.strip():
            raise MemoryProviderConfigError(
                "SeedMemoryProvider requires options['tenant_id']. Pinning this instance to one "
                "declared tenant is the only isolation available, and an undeclared pin means no "
                "isolation at all -- with the leaked row stamped as the reader's own.\n\n"
                '    MemoryProviderConfig(provider="seed", options={"single_tenant": True, '
                '"tenant_id": "acme"})',
                field_name="options.tenant_id",
            )
        return declared.strip()

    # -- request guards -------------------------------------------------------

    def _guard_tenant(self, scope: Scope) -> None:
        """``single_tenant: True`` is a claim about the deployment, not a blanket exemption."""
        if self._tenant != scope.tenant_id:
            raise MemoryOperationError(
                f"This SeedMemoryProvider is configured single_tenant for {self._tenant!r}; "
                f"refusing a call scoped to {scope.tenant_id!r}. The seed's storage cannot "
                "separate them, so serving this would be a cross-tenant read.",
                field_name="tenant_id",
            )

    @staticmethod
    def _reject_unfilterable_scope(scope: Scope) -> None:
        """Refuse a **read** scoped narrower than ``MemoryManager`` can filter.

        ``search_user_memories`` takes ``user_id`` and nothing else, so a read scoped to one agent
        returns every agent's memories for that user -- and ``_record_from_user_memory`` stamps the
        requesting scope onto each one, so the result looks correctly narrow. That is the tenant
        leak's shape exactly, one level down, and it fails the same way: invisibly.

        Writes are unaffected. ``UserMemory`` has ``agent_id`` and ``team_id`` columns, so a write
        records them faithfully; it is only the retrieval predicate that cannot use them.

        A bound store fails the same way on a different slot set, and is refused the same way by
        :meth:`_reject_unhonoured_slots` -- against ``learn_stores.RECALL_SLOTS`` rather than this
        hard-coded pair of predicates, because six stores' worth of it belongs in a table.
        """
        # `MemoryManager` substitutes `user_id = "default"` when it is None (`manager.py`), so
        # within the pinned tenant every user-less scope shares one pool -- and the projection then
        # stamps the *requesting* scope onto each row, so the answer looks correctly narrow. Same
        # shape as the two leaks below, on the slot that was left unguarded.
        # `Scope.tenant_id`'s own rationale applies one level down: unscoped is the widest, not the
        # narrowest.
        if scope.user_id is None:
            raise MemoryOperationError(
                "MemoryManager substitutes user_id='default' for an unset user, so this read would "
                "share one pool with every other user-less scope in the tenant and come back "
                "stamped with your scope. Set Scope.user_id, or bind a LearningStore for this tier.",
                field_name="user_id",
            )
        narrowed = [
            name
            for name, value in (
                ("agent_id", scope.agent_id),
                ("team_id", scope.team_id),
                ("session_id", scope.session_id),
            )
            if value is not None
        ]
        if narrowed:
            raise MemoryOperationError(
                f"MemoryManager.search_user_memories filters on user_id alone, so it cannot honour "
                f"{narrowed}: the answer would cover every agent, team, and session for this user "
                "while carrying the narrower scope. Bind a LearningStore for this tier, or widen "
                "the scope.",
                field_name=narrowed[0],
            )

    def _guard_store_writes(self, stores: Sequence["LearningStore"], scope: Scope) -> None:
        """Check every store before **any** of them is written, for the reason :meth:`_guard_stores`
        gives on the read side -- a refusal partway leaves the tier half written.

        The first unrecordable slot refuses the whole write rather than collecting every offending
        store: the caller's next move is to widen the scope or rebind the tier either way, and one
        named slot is what tells them which.
        """
        for store in stores:
            self._reject_unrecordable_slots(store, scope)

    def _guard_stores(self, stores: Sequence["LearningStore"], scope: Scope) -> None:
        """Check every store in the tier before **any** of them is asked: guarding inside the loop
        has the sync half recall the first and refuse on the second while the gathered async half
        recalls them all -- one refusal, two different sets of backend calls."""
        for store in stores:
            self._reject_unhonoured_slots(store, scope)

    def _reject_unrecordable_slots(self, store: "LearningStore", scope: Scope) -> None:
        """Refuse a **write** scoped to a slot this store's ``process`` cannot even see.

        The store-path twin of :meth:`_reject_unstorable_scope`, which refuses the same shape on
        the manager path: ``user_profile.process`` names no ``session_id``, so a session-scoped
        write was accepted, stored without its session, and could never be read back at that scope
        -- while the matching *read* was refused. Accepting on one side and refusing on the other
        is the asymmetry; both sides now refuse.

        Signature-derived, and deliberately the weaker of the two questions: a slot the store names
        is one it *sees*, not necessarily one it persists. Whether a seen slot survives into the
        row is a columns question, SM-C3's (#91).
        """
        # Indexed, not `.get` with a fallback: every store in `self.stores` is resolved at
        # construction and this guard only ever sees those, so a fallback would be a branch nothing
        # can reach -- and an unreachable branch is one no test can hold to account.
        recordable = self._recordable[id(store)]
        unrecordable = [
            slot for slot in STORE_FILTERABLE_SLOTS if getattr(scope, slot, None) is not None and slot not in recordable
        ]
        if unrecordable:
            learning_type = _learning_type(store)
            raise MemoryOperationError(
                f"The {learning_type!r} store's process takes no {unrecordable}, so this write would "
                f"be stored without {unrecordable[0]} and could never be read back at that scope. "
                "Widen the scope, or bind a store that records it.",
                field_name=unrecordable[0],
            )

    def _reject_unhonoured_slots(self, store: "LearningStore", scope: Scope) -> None:
        """Refuse a read narrowed on a slot this store's ``recall`` will not filter by.

        The store-path twin of :meth:`_reject_unfilterable_scope`. ``decision_log`` takes no
        ``user_id`` parameter at all and ``user_profile`` ignores every slot but ``user_id``, so a
        read scoped to one agent comes back covering all of them, stamped with the agent's scope.
        An unknown store type honours **nothing** -- fail-closed, since ``recall`` is
        ``(self, **kwargs)`` and there is nothing to derive from. ``options['store_slots']`` is how
        a deployment that knows better says so.
        """
        learning_type = _learning_type(store)
        unhonoured = unhonoured_slots(learning_type, scope, self._store_slots.get(learning_type))
        if unhonoured:
            raise MemoryOperationError(
                f"The {learning_type!r} store's recall does not filter on {unhonoured}, so this read "
                f"would cover every value of {unhonoured[0]} and come back carrying your narrower "
                "scope. Widen the scope, or declare the slots this deployment's store honours in "
                "options['store_slots'].",
                field_name=unhonoured[0],
            )

    def _reject_unservable_substrates(self, query: MemoryQuery) -> None:
        """Refuse a read naming a substrate this provider does not have.

        Refusing rather than returning empty: naming a substrate is a mistaken assumption about the
        deployment, not a query that legitimately matches nothing. The co-located adapter honours
        ``substrates``, so a provider that ignored it would break the config-only swap property.
        """
        if query.substrates is None:
            return
        raise MemoryOperationError(
            f"This provider has no substrates (it wraps the seed's own memory), so it cannot serve "
            f"a read restricted to {sorted(s.value for s in query.substrates)}. Drop "
            "MemoryQuery.substrates, or use a provider that wires the substrate you need.",
            field_name="substrates",
        )

    def _reject_as_of(self, query: MemoryQuery) -> None:
        """Refuse a point-in-time read the seed cannot answer.

        Neither ``UserMemory`` nor ``LearningStore`` carries valid time, so ``as_of`` would be
        ignored and present-day memory returned as a historical answer.
        """
        if query.as_of is not None:
            raise MemoryOperationError(
                "The seed provider cannot answer a point-in-time read: UserMemory and the learn "
                "stores carry no valid-time columns, so as_of would be ignored and present-day "
                "memory returned as a historical answer. Bi-temporal storage is SM-C3's (#91).",
                field_name="as_of",
            )

    def _guard_read(self, scope: Scope, query: MemoryQuery) -> None:
        """Every read guard, up front -- so they apply even when no tier is served.

        Guards that lived only inside ``read_tier`` were skipped entirely by a read naming an
        unserved tier, which returned ``[]`` and no error.
        """
        self._guard_tenant(scope)
        self._reject_as_of(query)
        self._reject_unservable_substrates(query)

    def _reject_unserved(self, tier: MemoryTier) -> None:
        """Refuse a tier nothing here can store, naming the ticket that will build it."""
        if tier is MemoryTier.EPISODIC and not self._stores_for(MemoryTier.EPISODIC):
            raise MemoryOperationError(
                "The seed has no episodic store, so an episode cannot be written through this "
                "provider. Redirecting it into the semantic tier would destroy the raw, non-lossy "
                f"record consolidation depends on. The episodic tier is {policy_for_tier(tier).owner}.",
                field_name="tier",
            )
        if tier not in self._served_tiers():
            raise MemoryOperationError(f"No seed component is bound to the {tier.value!r} tier.", field_name="tier")

    # -- introspection --------------------------------------------------------

    def substrates(self) -> Dict[Substrate, bool]:
        """Neither. The seed has no vector search over memory and no temporal graph."""
        return {Substrate.VECTOR: False, Substrate.GRAPH: False}

    def tiers(self) -> Dict[MemoryTier, bool]:
        """A tier is served when a store is bound to it, or -- for semantic -- a manager is."""
        served = set(self._served_tiers())
        return {tier: tier in served for tier in MemoryTier}

    def _served_tiers(self) -> List[MemoryTier]:
        tiers = {tier for tier, group in self.stores.items() if group}
        if self.manager is not None:
            tiers.add(MemoryTier.SEMANTIC)
        return [tier for tier in MemoryTier if tier in tiers]

    def _stores_for(self, tier: MemoryTier) -> Tuple["LearningStore", ...]:
        return self.stores.get(tier, ())

    def _recall_kwargs(self, scope: Scope, query: MemoryQuery) -> Dict[str, Any]:
        """The keyword contract ``LearningStore.recall`` and ``.process`` expect.

        ``message`` and ``query`` are both passed because the seed's stores disagree about which
        one they read -- ``learn/machine.py`` sets both for the same reason.
        """
        return {
            "user_id": scope.user_id,
            "session_id": scope.session_id,
            "agent_id": scope.agent_id,
            "team_id": scope.team_id,
            "message": query.text,
            "query": query.text,
        }

    # -- read -----------------------------------------------------------------

    def _store_records(self, store: "LearningStore", tier: MemoryTier, scope: Scope, data: Any) -> List[MemoryRecord]:
        """Turn what a store recalled into records, via its own ``build_context``.

        One record per store, not one per underlying item: ``recall`` returns whatever shape the
        store likes and only ``build_context`` renders it, so the rendered context *is* the
        projection. Parsing a store's internal shape would couple the seam to six implementations.

        The id carries the tier as well as the store, because two tiers may be bound to stores
        reporting the same ``learning_type`` and this path has no dedup to collapse the collision.
        """
        if data is None:
            return []
        context = store.build_context(data=data)
        if not context:
            return []
        return [
            MemoryRecord(
                id=f"{store.learning_type}:{tier.value}:{scope.key()}",
                tier=tier,
                content=context,
                scope=scope,
                provenance={"learning_type": store.learning_type},
            )
        ]

    def _manager_records(self, scope: Scope, query: MemoryQuery) -> List[MemoryRecord]:
        """Retrieve through ``MemoryManager``. **This is not vector search.**

        ``last_n`` -- recency -- is hardcoded. The manager has no embedder (SPEC section 6.1), so
        there is nothing to rank by; ``agentic`` is excluded because it runs an LLM round trip and
        ``MemoryProvider.read`` promises no query-time LLM on the hot path. Real similarity search
        over memory arrives with SM-V1 (#95) as a vector substrate, not as a knob here.
        """
        if self.manager is None:  # unreachable via read_tier; explicit beats `assert`, which -O strips
            return []
        self._reject_unfilterable_scope(scope)
        memories = self.manager.search_user_memories(
            query=query.text, limit=query.limit, retrieval_method="last_n", user_id=scope.user_id
        )
        return [_record_from_user_memory(memory, scope) for memory in memories or []]

    async def _amanager_records(self, scope: Scope, query: MemoryQuery) -> List[MemoryRecord]:
        """The same retrieval on the async lane, awaiting the manager when it has an async half.

        The thread hop stays for a duck-typed manager without one -- this bridge checks its
        collaborators by method, not by type. ``last_n`` is hardcoded for the reason
        :meth:`_manager_records` gives: there is nothing to rank by, and ``agentic`` would put an
        LLM round trip on a read path that promises none.
        """
        if self.manager is None:
            return []
        asearch = getattr(self.manager, "asearch_user_memories", None)
        if asearch is None:
            return await asyncio.to_thread(self._manager_records, scope, query)
        self._reject_unfilterable_scope(scope)
        memories = await asearch(query=query.text, limit=query.limit, retrieval_method="last_n", user_id=scope.user_id)
        return [_record_from_user_memory(memory, scope) for memory in memories or []]

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        require_scope(scope, method="read_tier")
        self._guard_read(scope, query)
        if not query.limit:
            # Short-circuited *before* the fetch. `MemoryManager` truncates only when `limit > 0`,
            # so asking it for zero rows materialised the user's whole table and then discarded it.
            return []
        stores = self._stores_for(tier)
        if stores:
            self._guard_stores(stores, scope)
            recalled: List[Any] = []
            failures: List[BaseException] = []
            for store in stores:
                try:
                    recalled.append(store.recall(**self._recall_kwargs(scope, query)))
                except Exception as exc:  # noqa: PERF203 - per-store isolation, as on the write path
                    failures.append(exc)
                    recalled.append(None)
            self._raise_read_failure(failures)
            records = self._records_from(stores, recalled, tier, scope)
        elif tier is MemoryTier.SEMANTIC and self.manager is not None:
            records = self._manager_records(scope, query)
        else:
            return []
        # Capped here as well as in `_slice`: MemoryManager truncates only when `limit > 0`, so
        # `limit=0` returned the whole table on this provider and nothing on the co-located one.
        return records[: query.limit] if query.limit else []

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        require_scope(scope, method="aread_tier")
        self._guard_read(scope, query)
        if not query.limit:
            return []
        stores = self._stores_for(tier)
        if stores:
            self._guard_stores(stores, scope)
            # Settled on both halves, as the write path is: a bare `gather` schedules every
            # `arecall` and does not cancel the siblings when one raises, so the async lane made
            # more backend calls than the sync lane for the same failing read.
            settled = await asyncio.gather(
                *(store.arecall(**self._recall_kwargs(scope, query)) for store in stores), return_exceptions=True
            )
            self._raise_read_failure([r for r in settled if isinstance(r, BaseException)])
            records = self._records_from(stores, settled, tier, scope)
        elif tier is MemoryTier.SEMANTIC and self.manager is not None:
            records = await self._amanager_records(scope, query)
        else:
            return []
        return records[: query.limit] if query.limit else []

    def _records_from(
        self,
        stores: Sequence["LearningStore"],
        recalled: Sequence[Any],
        tier: MemoryTier,
        scope: Scope,
    ) -> List[MemoryRecord]:
        """Project what each store recalled, in binding order. Shared, so the halves cannot differ."""
        return [
            record for store, data in zip(stores, recalled) for record in self._store_records(store, tier, scope, data)
        ]

    @staticmethod
    def _raise_read_failure(failures: Sequence[BaseException]) -> None:
        """One failure out of a settled fan-out, chosen the same way the write path chooses.

        A refusal wins over a transport error, so a read that is unanswerable reports *that* rather
        than whichever backend happened to fail first. Nothing is wrapped: a read commits nothing,
        so there is no receipt to carry and ``PartialWriteError`` would be a lie.
        """
        if not failures:
            return
        refusal = next((f for f in failures if isinstance(f, MemoryOperationError)), None)
        raise refusal or failures[0]

    @staticmethod
    def _interleave(per_tier: List[List[MemoryRecord]]) -> List[MemoryRecord]:
        """Round-robin across tiers so the cap does not silently drop the later ones.

        Each tier is asked for up to ``limit`` records and the slice then caps at ``limit``, so
        concatenating in tier order lets the first tier consume the whole budget.

        Round-robin rather than a ranking because **the seed produces no scores** -- there is no
        embedder, and ``recall`` returns rendered context. ``MemorySlice.records`` here is a fair
        share of a fixed budget, not a relevance ordering.
        """
        merged: List[MemoryRecord] = []
        for column in range(max((len(records) for records in per_tier), default=0)):
            for records in per_tier:
                if column < len(records):
                    merged.append(records[column])
        return merged

    def _slice(self, scope: Scope, query: MemoryQuery, records: List[MemoryRecord]) -> MemorySlice:
        capped = records[: query.limit] if query.limit else []
        by_tier: Dict[MemoryTier, List[MemoryRecord]] = {}
        for record in capped:
            by_tier.setdefault(record.tier, []).append(record)
        return MemorySlice(
            scope=scope,
            query=query,
            records=capped,
            by_tier=by_tier,
            # Every FUSION_KEYS entry present even though nothing is fused, so a consumer written
            # against one provider does not KeyError on another. Zeros are the honest values: there
            # are no legs here, not an unknown number of them.
            fusion=fusion_diagnostics(
                method=None,
                fused=len(records),
                returned=len(capped),
                reason="seed provider has no substrates to fuse",
            ),
        )

    def _wanted_tiers(self, query: MemoryQuery) -> List[MemoryTier]:
        served = self._served_tiers()
        return [tier for tier in query.resolved_tiers() if tier in served]

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        require_scope(scope, method="read")
        self._guard_read(scope, query)
        per_tier: List[List[MemoryRecord]] = []
        failures: List[BaseException] = []
        for tier in self._wanted_tiers(query):
            try:
                per_tier.append(self.read_tier(scope, tier, query))
            except Exception as exc:  # noqa: PERF203 - per-tier isolation, matching `aread`
                failures.append(exc)
        self._raise_read_failure(failures)
        return self._slice(scope, query, self._interleave(per_tier))

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        require_scope(scope, method="aread")
        self._guard_read(scope, query)
        # Gathered: the tiers are independent, and a manager without an async search still pays a
        # thread hop per tier. Settled like the store-level fan-out inside `aread_tier`, and like
        # the sync half above -- the asymmetry compounds if it is fixed at only one nesting level.
        settled = await asyncio.gather(
            *(self.aread_tier(scope, tier, query) for tier in self._wanted_tiers(query)), return_exceptions=True
        )
        self._raise_read_failure([r for r in settled if isinstance(r, BaseException)])
        per_tier = [r for r in settled if not isinstance(r, BaseException)]
        return self._slice(scope, query, self._interleave(per_tier))

    # -- write ----------------------------------------------------------------

    def _as_messages(self, events: Sequence[MemoryEvent]) -> List[Dict[str, Any]]:
        """Events in the message shape ``LearningStore.process`` consumes.

        ``metadata`` and ``occurred_at`` ride along rather than being dropped: a store that ignores
        the extra keys is unaffected, and one that reads them no longer has to be told twice.
        """
        messages: List[Dict[str, Any]] = []
        for event in events:
            message: Dict[str, Any] = {"role": event.role or "user", "content": event.content}
            if event.occurred_at is not None:
                message["occurred_at"] = event.occurred_at
            if event.metadata:
                message["metadata"] = dict(event.metadata)
            messages.append(message)
        return messages

    @staticmethod
    def _store_receipt(took: Dict[str, bool], tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """What a ``LearningStore.process`` write can honestly claim.

        ``process`` returns ``None`` and mints no per-event id -- it folds a batch of messages into
        whatever shape the store keeps -- so there are no ``event_ids`` and ``accepted`` cannot mean
        "rows written". It means **events the tier took**: the batch reached memory if any bound
        store took it. Requiring every store to agree was the alternative and is worse, because a
        store outside ``LearningMode.ALWAYS`` no-ops by design, so an ordinary mixed-mode tier would
        receipt a genuine write as zero.

        Which stores took it is not collapsed into that number: ``details["stores"]`` carries the
        per-store answer, so "two of three" is readable rather than inferred. Each flag is read
        under that store's lock, immediately after its own write, and defaults to ``False`` for a
        store that does not expose one -- assuming ``True`` would take success from the field whose
        purpose is not assuming it.
        """
        accepted = any(took.values())
        return WriteReceipt(
            accepted=len(events) if accepted else 0,
            event_ids=[],  # process() mints none
            tiers_written=[tier] if accepted else [],
            details=write_details(stores=took),
        )

    @staticmethod
    def _user_memory(event: MemoryEvent, scope: Scope) -> UserMemory:
        """The row one event becomes. Shared, so the two halves cannot store different columns."""
        return UserMemory(
            memory=event.content,
            memory_id=event.id,
            topics=list(event.topics) if event.topics is not None else None,
            agent_id=scope.agent_id,
            team_id=scope.team_id,
            input=_provenance_note(event),
        )

    def _manager_write(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        if self.manager is None:  # unreachable via write_tier; explicit beats `assert`, which -O strips
            raise MemoryOperationError(f"No seed component is bound to the {tier.value!r} tier.", field_name="tier")
        self._reject_unstorable_scope(scope)
        ids: List[str] = []
        for event in events:
            try:
                memory_id = self.manager.add_user_memory(memory=self._user_memory(event, scope), user_id=scope.user_id)
            except Exception as exc:
                # `add_user_memory` is called once per event, so a failure partway leaves earlier
                # events committed. Without this the ids were lost in a local and the receipt
                # reported `accepted=0, event_ids=[]` for rows that are in the database -- and a
                # caller following `PartialWriteError`'s documented retry advice re-sends them
                # with no id, which mints fresh uuid4 rows and duplicates every one.
                raise PartialWriteError(self._committed(tier, ids)) from exc
            self._require_stored(memory_id, tier, ids)
            ids.append(memory_id)  # type: ignore[arg-type]  # `_require_stored` narrowed it
        return self._committed(tier, ids)

    async def _amanager_write(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """The same write on the async lane, awaiting the manager when it has an async half.

        Sequential and short-circuiting, exactly as the sync half is: gathering would leave the two
        halves with different rows committed for one identical failure.
        """
        if self.manager is None:
            raise MemoryOperationError(f"No seed component is bound to the {tier.value!r} tier.", field_name="tier")
        aadd = getattr(self.manager, "aadd_user_memory", None)
        if aadd is None:
            return await asyncio.to_thread(self._manager_write, scope, tier, events)
        self._reject_unstorable_scope(scope)
        ids: List[str] = []
        for event in events:
            try:
                # Annotated because `aadd` came from `getattr`, which types as `Any` and would
                # silently switch this loop's checking off -- the sync twin below is checked, and
                # two halves type-checked differently is how they drift.
                memory_id: Optional[str] = await aadd(memory=self._user_memory(event, scope), user_id=scope.user_id)
            except Exception as exc:
                raise PartialWriteError(self._committed(tier, ids)) from exc
            self._require_stored(memory_id, tier, ids)
            ids.append(memory_id)  # type: ignore[arg-type]  # `_require_stored` narrowed it
        return self._committed(tier, ids)

    @classmethod
    def _require_stored(cls, memory_id: Optional[str], tier: MemoryTier, ids: List[str]) -> None:
        """Treat "the manager returned no id" as a failed write, not as a quiet skip.

        ``MemoryManager`` logs a db failure and returns ``None`` rather than raising, because its
        extraction loops must survive one bad row. Read as "nothing to append", that turned a
        write nothing stored into a receipt reporting it accepted -- and made the ``except`` arm
        above dead code for the ordinary failure. A missing id is the same event as an exception,
        so it takes the same exit, carrying the ids that did land.
        """
        if not memory_id:
            raise PartialWriteError(cls._committed(tier, ids))

    @staticmethod
    def _committed(tier: MemoryTier, ids: Sequence[str]) -> WriteReceipt:
        """A receipt for rows actually persisted -- `accepted` means committed, never attempted.

        ``details["stores"]`` is empty: this path is ``MemoryManager``'s CRUD, not a bound store,
        and reporting a store that took nothing would be inventing one.
        """
        return WriteReceipt(
            accepted=len(ids),
            event_ids=list(ids),
            tiers_written=[tier] if ids else [],
            details=write_details(),
        )

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """A backend failure here raises ``PartialWriteError``, exactly as ``write`` does.

        ``write_tier`` used to let the backend's own exception through while ``write`` wrapped it,
        so the same failure reached a caller as two different types depending on which verb they
        used -- and `PartialWriteError`'s docstring is explicit that both adapters must fail the
        same way or there is no portable retry.
        """
        require_scope(scope, method="write_tier")
        self._validate_write(scope, events)
        self._reject_unserved(tier)
        stores = self._stores_for(tier)
        if not stores:
            return self._manager_write(scope, tier, events)
        self._guard_store_writes(stores, scope)
        took: Dict[str, bool] = {}
        failures: List[BaseException] = []
        for store in stores:
            try:
                # The write and the flag read are one critical section. `was_updated` reports the
                # last operation on the *store*, not on this call, so without the lock a second
                # writer's `process` lands between them and this receipt describes their write.
                # It also makes the write itself safe: stores reset their own progress flags on
                # entry, so two concurrent `process` calls corrupted more than the count.
                with self._write_locks[id(store)]:
                    store.process(self._as_messages(events), **self._recall_kwargs(scope, MemoryQuery(text="")))
                    took[_learning_type(store)] = bool(getattr(store, "was_updated", False))
            except Exception as exc:  # noqa: PERF203 - per-store isolation, as in the co-located fan-out
                failures.append(exc)
        self._shape_store_failures(failures, took, tier, events)
        return self._store_receipt(took, tier, events)

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Async variant of :meth:`write_tier`, with the same failure type."""
        require_scope(scope, method="awrite_tier")
        self._validate_write(scope, events)
        self._reject_unserved(tier)
        stores = self._stores_for(tier)
        if not stores:
            return await self._amanager_write(scope, tier, events)
        self._guard_store_writes(stores, scope)

        async def process(store: "LearningStore") -> Tuple[str, bool]:
            # Held across the await, exactly as the sync half holds it across the call: the flag
            # reports the last operation on the *store*, so a sibling writer suspending inside
            # `aprocess` would otherwise set it between our write and our read.
            async with self._awrite_lock(store):
                await store.aprocess(self._as_messages(events), **self._recall_kwargs(scope, MemoryQuery(text="")))
                return _learning_type(store), bool(getattr(store, "was_updated", False))

        # Settled, not short-circuited: the sync half attempts every store, so this one must too,
        # or one identical failure leaves the two halves with different stores written.
        results = await asyncio.gather(*(process(store) for store in stores), return_exceptions=True)
        took = {name: flag for name, flag in (r for r in results if not isinstance(r, BaseException))}
        self._shape_store_failures([r for r in results if isinstance(r, BaseException)], took, tier, events)
        return self._store_receipt(took, tier, events)

    @classmethod
    def _shape_store_failures(
        cls,
        failures: Sequence[BaseException],
        took: Dict[str, bool],
        tier: MemoryTier,
        events: Sequence[MemoryEvent],
    ) -> None:
        """What a failed ``process`` becomes, decided once for both halves.

        Two rules, matching ``co_located._raise_write_failure`` because a caller retrying across
        providers cannot have them differ: a **refusal wins over a transport error** as the cause,
        and a refusal with nothing committed propagates **bare** -- once a sibling store in the tier
        has taken the write it is a partial write like any other.

        The receipt is the **same one the success path would have built**. An empty
        ``WriteReceipt()`` was the obvious thing to raise, since ``process`` mints no ids for a
        retry to reuse -- but it also cleared ``tiers_written``, so a caller inspecting the error
        could not tell that a sibling store had genuinely committed. Ids and destinations are
        different questions, and only the first is unanswerable here.
        """
        if not failures:
            return
        refusal = next((f for f in failures if isinstance(f, MemoryOperationError)), None)
        if refusal is not None and not any(took.values()):
            raise refusal
        raise PartialWriteError(cls._store_receipt(took, tier, events)) from (refusal or failures[0])

    @staticmethod
    def _reject_unstorable_scope(scope: Scope) -> None:  # noqa: D401
        """Refuse a write scoped to a slot ``UserMemory`` has no column for.

        ``UserMemory`` carries ``user_id`` / ``agent_id`` / ``team_id`` and no session column, and
        ``_provenance_note`` does not fold one in -- so a session-scoped write was accepted, lost
        its session identity, and could never be read back at that scope, while reads refused the
        same scope outright. Accept-and-ignore on a scoping field, in the module whose whole
        posture is the opposite.
        """
        if scope.user_id is None:
            raise MemoryOperationError(
                "MemoryManager substitutes user_id='default' for an unset user, so this write would "
                "land in a pool shared with every other user-less scope in the tenant. Set "
                "Scope.user_id, or bind a LearningStore for this tier.",
                field_name="user_id",
            )
        if scope.session_id is not None:
            raise MemoryOperationError(
                "UserMemory has no session column, so a session-scoped write would be stored "
                "without its session identity and could never be read back at that scope. Bind a "
                "LearningStore for this tier, or widen the scope.",
                field_name="session_id",
            )

    def _validate_write(self, scope: Scope, events: Sequence[MemoryEvent]) -> None:
        # The tenant guard runs here rather than per group, because an empty batch produces no
        # groups and a scope this provider would refuse got an empty receipt instead of an error.
        self._guard_tenant(scope)
        reject_foreign_scopes(scope, events)
        reject_duplicate_ids(events)

    def _validated_groups(
        self, scope: Scope, events: Sequence[MemoryEvent]
    ) -> List[Tuple[MemoryTier, List[MemoryEvent]]]:
        """Group by tier and validate everything **before** any group writes.

        Validating inside ``write_tier`` instead would let a later group's refusal arrive after
        earlier groups had committed, making a mixed batch's atomicity depend on the caller's list
        order -- and mixed is the ordinary case, since ``MemoryEvent`` defaults to ``EPISODIC``.
        """
        self._validate_write(scope, events)
        groups = _group_by_tier(events)
        for tier, _ in groups:
            self._reject_unserved(tier)
        return groups

    @staticmethod
    def _merge_receipts(receipts: Iterable[WriteReceipt]) -> WriteReceipt:
        """One receipt for a batch that fanned out across tiers."""
        merged = WriteReceipt()
        for receipt in receipts:
            merged.accepted += receipt.accepted
            merged.event_ids.extend(receipt.event_ids)
            for tier in receipt.tiers_written:
                if tier not in merged.tiers_written:
                    merged.tiers_written.append(tier)
        return merged

    def _raise_fanout_failure(self, receipts: List[WriteReceipt], exc: BaseException) -> "NoReturn":
        """What a failure partway through the per-tier fan-out becomes. One decision, both halves.

        Written inline in each half it drifted on the co-located adapter for six rounds, so it is
        a method here even though it is short.

        Three cases:

        * a group that already raised ``PartialWriteError`` carries its own committed ids -- merge
          them in, or they are lost in the group that raised and the documented retry duplicates
          every one;
        * a **refusal after an earlier group committed** is a partial write too. Groups are written
          in sequence, so a refusal in group two leaves group one persisted. Propagating it bare
          lost those ids -- the same defect the co-located adapter had, found here by sweeping for
          the sibling rather than by review;
        * a refusal with nothing committed propagates bare: there is nothing to reconcile.
        """
        if isinstance(exc, PartialWriteError):
            receipts.append(exc.receipt)
            raise PartialWriteError(self._merge_receipts(receipts)) from exc.__cause__
        if isinstance(exc, MemoryOperationError) and not any(r.event_ids for r in receipts):
            raise exc
        raise PartialWriteError(self._merge_receipts(receipts)) from exc

    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Route each event to **its own tier**, rather than forcing the batch into SEMANTIC.

        Grouping by tier is what lets ``_reject_unserved`` do its job: a default ``MemoryEvent`` is
        ``EPISODIC``, and the seed must refuse that rather than rewrite it as semantic.
        """
        require_scope(scope, method="write")
        groups = self._validated_groups(scope, events)
        receipts: List[WriteReceipt] = []
        try:
            for tier, group in groups:
                receipts.append(self.write_tier(scope, tier, group))
        except Exception as exc:
            self._raise_fanout_failure(receipts, exc)
        return self._merge_receipts(receipts)

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Async variant of :meth:`write`, with the same per-tier routing."""
        require_scope(scope, method="awrite")
        groups = self._validated_groups(scope, events)
        receipts: List[WriteReceipt] = []
        try:
            for tier, group in groups:
                receipts.append(await self.awrite_tier(scope, tier, group))
        except Exception as exc:
            self._raise_fanout_failure(receipts, exc)
        return self._merge_receipts(receipts)

    # -- consolidate ----------------------------------------------------------

    def consolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        """Run the seed's curation as the dedup lane. There is no episodic-to-semantic pass here.

        ``Curator`` (``learn/curate.py``) is narrower than ADR 0003 section 2.1's "REUSE inside
        consolidate" implies -- its own docstring says *"Currently supports user_profile store
        only"*. So it is wired as the dedup hook it can serve, and everything it cannot do reports
        zero rather than reporting a consolidation that did not happen.

        The real loop -- extract, merge, resolve contradictions, write both substrates -- needs an
        episodic tier to read from and a graph to write to. It is SM-L1's (#122) onward.
        """
        require_scope(scope, method="consolidate")
        self._guard_tenant(scope)
        # `deduplicate` filters on user_id alone, exactly like `search_user_memories`, so a scope
        # narrowed below it is accepted and silently ignored -- on a *write* path, which is worse
        # than the read-side accept-and-ignore this guard was written for.
        self._reject_unfilterable_scope(scope)
        # Polarity is the *opposite* of the co-located adapter's: this provider accepts only
        # `tiers=None`, because `[EPISODIC]` -- the one set co-located accepts -- is the single
        # tier the seed refuses everywhere else.
        if tiers is not None:
            requested = sorted(tier.value for tier in tiers)
            raise MemoryOperationError(
                f"The seed provider runs only the curator's dedup lane -- it has no episodic tier "
                f"to consolidate from -- but was asked to consolidate {requested}. Pass None. "
                "The real loop is SM-L1's (#122).",
                field_name="tiers",
            )

        can_curate = self.machine is not None and scope.user_id is not None
        raw_merged = self.machine.curator.deduplicate(user_id=scope.user_id) if can_curate else 0  # type: ignore[union-attr, arg-type]
        # Coerced: `_checked_machine` verifies `deduplicate` is callable, not what it returns, and
        # `ConsolidationReport.duplicates_merged` is a typed int a scheduler reads.
        merged = raw_merged if isinstance(raw_merged, int) and not isinstance(raw_merged, bool) else 0
        return ConsolidationReport(
            scope=scope,
            episodic_scanned=0,  # no episodic store exists to scan
            semantic_written=0,  # no distillation without extraction
            duplicates_merged=merged,
            contradictions_resolved=0,
            details=consolidation_details(
                derived_by="seed.curator_dedup" if can_curate else None,
                complete=True,
                ran=can_curate,
                skipped_because=None
                if can_curate
                else ("no LearningMachine wired" if self.machine is None else "scope has no user_id"),
                note="seed has no episodic tier or graph; the consolidation loop is SM-L1+",
            ),
        )

    async def aconsolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        """Async variant. ``Curator.deduplicate`` is sync-only, so this hops a thread."""
        # Before the hop, so an unscoped call is refused on the caller's stack rather than
        # surfacing from a worker thread, and does not spend one to be refused.
        require_scope(scope, method="aconsolidate")
        return await asyncio.to_thread(self.consolidate, scope, tiers)


def _checked_stores(raw: Any) -> Optional[Dict[MemoryTier, "StoreBinding"]]:
    """Verify bound stores at build time, as ``checked_vector`` / ``checked_graph`` do next door.

    ``LearningStore`` is already ``runtime_checkable``, so this is the same one structural check --
    and without it a misconfigured seed built cleanly and failed at first recall. A tier may name
    one store or a sequence of them, because three of the six seed stores are semantic.
    """
    if raw is None:
        return None
    from egpt.learn.stores.protocol import LearningStore

    if not isinstance(raw, dict):
        raise MemoryProviderConfigError(
            f"config.options['stores'] must be a dict of MemoryTier -> LearningStore, got {type(raw).__name__}.",
            field_name="options.stores",
        )
    for tier, value in raw.items():
        if not isinstance(tier, MemoryTier):
            raise MemoryProviderConfigError(
                f"config.options['stores'] key {tier!r} is not a MemoryTier.", field_name="options.stores"
            )
        for store in value if isinstance(value, (list, tuple)) else (value,):
            if not isinstance(store, LearningStore):
                raise MemoryProviderConfigError(
                    f"config.options['stores'][{tier.value!r}] is a {type(store).__name__}, which does not "
                    "carry every method LearningStore names.",
                    field_name="options.stores",
                )
    return dict(raw)


def _checked_slots(raw: Any) -> Dict[str, FrozenSet[str]]:
    """Validate ``options['store_slots']``: an override of which scope slots a store honours.

    A typo here ("session" for "session_id") *widens* what the bridge will answer, which is the
    failure the guard exists to stop -- so an unknown slot name is refused rather than ignored.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise MemoryProviderConfigError(
            f"config.options['store_slots'] must be a dict of learning_type -> slot names, got {type(raw).__name__}.",
            field_name="options.store_slots",
        )
    checked: Dict[str, FrozenSet[str]] = {}
    for learning_type, slots in raw.items():
        if not isinstance(learning_type, str):
            raise MemoryProviderConfigError(
                f"config.options['store_slots'] key {learning_type!r} is not a learning_type string.",
                field_name="options.store_slots",
            )
        if isinstance(slots, str) or not isinstance(slots, (list, tuple, set, frozenset)):
            raise MemoryProviderConfigError(
                f"config.options['store_slots'][{learning_type!r}] must be a collection of slot names.",
                field_name="options.store_slots",
            )
        unknown = sorted(set(slots) - set(STORE_FILTERABLE_SLOTS))
        if unknown:
            raise MemoryProviderConfigError(
                f"config.options['store_slots'][{learning_type!r}] names unknown slot(s) {unknown}; "
                f"a scope slot is one of {list(STORE_FILTERABLE_SLOTS)}. A misspelled slot silently widens what "
                "this provider will answer.",
                field_name="options.store_slots",
            )
        checked[learning_type] = frozenset(slots)
    return checked


def _checked_machine(raw: Any) -> Any:
    """The one collaborator that was taken on faith while its two siblings were checked.

    A string built cleanly and then died with ``AttributeError: 'str' object has no attribute
    'curator'`` inside ``consolidate`` -- and only on a *user-scoped* call, because the pass short-
    circuits without a ``user_id``.
    """
    if raw is None:
        return None
    if not callable(getattr(getattr(raw, "curator", None), "deduplicate", None)):
        raise MemoryProviderConfigError(
            f"config.options['learning_machine'] is a {type(raw).__name__}; consolidate needs "
            "`.curator.deduplicate(user_id=...)`.",
            field_name="options.learning_machine",
        )
    return raw


def _checked(raw: Any, attributes: Sequence[str], option: str) -> Any:
    """Duck-check a collaborator the same way, by the methods this bridge actually calls."""
    if raw is None:
        return None
    missing = [name for name in attributes if not callable(getattr(raw, name, None))]
    if missing:
        raise MemoryProviderConfigError(
            f"config.options[{option!r}] is a {type(raw).__name__}, missing {missing}.", field_name=f"options.{option}"
        )
    return raw


@register_provider("seed")
def _build_seed(config: MemoryProviderConfig) -> MemoryProvider:
    """Collaborators arrive through ``config.options``: a ``MemoryManager`` and a
    ``LearningMachine`` hold a db and a model, so they are user-constructed rather than
    registry-built. They are still checked here, so a misconfigured seed fails at build."""
    return SeedMemoryProvider(
        manager=_checked(
            config.options.get("memory_manager"), ("search_user_memories", "add_user_memory"), "memory_manager"
        ),
        # `curator` is a property, so `_checked`'s `callable(getattr(...))` would reject a valid
        # machine -- duck-check the method actually called instead.
        machine=_checked_machine(config.options.get("learning_machine")),
        stores=_checked_stores(config.options.get("stores")),
        config=config,
    )
