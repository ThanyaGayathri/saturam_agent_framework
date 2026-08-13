"""The seed bridge: today's memory behind the new seam (SM-C1).

ADR 0003 section 2 decided **WRAP / EXTEND, never replace**. This module is that decision in code:
a deployment gets the ``MemoryProvider`` API over the memory it already has
(``LearningMachine``'s stores and ``MemoryManager``'s ``UserMemory`` CRUD), with no pgvector, no
graph, and no migration.

Most of this file is refusals, and that is the point. The seed cannot honour tenancy, valid time,
substrates, or an episodic tier, so each of those is rejected at the boundary rather than accepted
and ignored -- an ignored parameter returns a wrong answer that looks right, which is the one
failure the layer above cannot see.

Three seed methods have no async variant (``MemoryManager.search_user_memories`` /
``add_user_memory``, ``Curator.deduplicate``); their async paths hop a thread rather than being an
``async def`` that never awaits.
"""

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, NoReturn, Optional, Sequence, Tuple
from uuid import NAMESPACE_OID, uuid5

from egpt.db.schemas import UserMemory
from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError, register_provider
from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import MemoryTier
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
)
from egpt.memory.validation import reject_duplicate_ids, reject_foreign_scopes

if TYPE_CHECKING:
    from egpt.learn.machine import LearningMachine
    from egpt.learn.stores.protocol import LearningStore
    from egpt.memory.manager import MemoryManager

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


def _record_from_user_memory(memory: UserMemory, scope: Scope) -> MemoryRecord:
    """Project a seed ``UserMemory`` into the seam's read-projection.

    ``UserMemory`` has no tenant and no temporal fields (SM-C3 #91 adds them), so the tenant comes
    from the caller's scope and the bi-temporal fields stay ``None`` -- which reads correctly as
    "valid since forever, never invalidated, current belief".
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

    Tier routing follows ADR 0003 section 2.2, which SM-C2 (#90) turns into full per-tier policy:

    * ``SEMANTIC`` -> ``MemoryManager`` (``UserMemory`` CRUD), or a bound ``LearningStore``.
    * ``WORKING`` / ``PROCEDURAL`` -> a bound ``LearningStore``, if one is supplied.
    * ``EPISODIC`` -> **nothing**. The seed has no episodic store (SM-E1 #108). Writing it is
      refused rather than redirected into semantic, which would destroy the raw, non-lossy record
      consolidation depends on.

    ``stores`` binds a tier to a seed ``LearningStore`` explicitly, because which of the six stores
    belongs to which tier is SM-C2's decision, not this module's.
    """

    provider_key = "seed"

    def __init__(
        self,
        manager: Optional["MemoryManager"] = None,
        machine: Optional["LearningMachine"] = None,
        stores: Optional[Dict[MemoryTier, "LearningStore"]] = None,
        config: Optional[MemoryProviderConfig] = None,
    ) -> None:
        if manager is None and machine is None and not stores:
            raise MemoryProviderConfigError(
                "SeedMemoryProvider needs a MemoryManager, a LearningMachine, or at least one bound store.",
                field_name="provider",
            )
        self.manager = manager
        self.machine = machine
        self.stores: Dict[MemoryTier, "LearningStore"] = dict(stores or {})
        self.config = config or MemoryProviderConfig(provider=self.provider_key)
        self._require_single_tenant_acknowledgement()
        self._tenant: str = self._require_declared_tenant()

    # -- construction guards --------------------------------------------------

    def _require_single_tenant_acknowledgement(self) -> None:
        """Refuse to build unless the deployment states it is single-tenant.

        ``UserMemory`` has no tenant column and ``MemoryManager`` partitions on ``user_id`` alone,
        so ``Scope.tenant_id`` is accepted on every call and applied on none::

            write(Scope(tenant_id='acme',   user_id='ada')) -> "acme Q4 revenue is 12M"
            read (Scope(tenant_id='globex', user_id='ada')) -> ["acme Q4 revenue is 12M"]

        The relabel is what makes it critical rather than merely absent: the projection stamps the
        *reader's* scope onto the row, so every check above this layer sees a well-formed ``globex``
        record.

        Namespacing the manager key (``f"{tenant}/{user}"``) is the tempting alternative and is
        worse: it orphans every row an existing deployment already wrote, with no error anywhere.
        That migration belongs with the schema that gains a real tenant column (SM-C3 #91).
        """
        if not self.config.options.get("single_tenant"):
            raise MemoryProviderConfigError(
                "SeedMemoryProvider cannot enforce tenant isolation: UserMemory has no tenant "
                "column and MemoryManager partitions by user_id alone, so Scope.tenant_id is "
                "accepted and never applied. Two tenants sharing a user_id would read each "
                "other's memories, and the projection would stamp the reader's tenant onto the "
                "result, hiding the leak from every check above this layer.\n\n"
                "If this deployment serves exactly one tenant, say so explicitly:\n"
                '    MemoryProviderConfig(provider="seed", options={"single_tenant": True, '
                '"tenant_id": "acme"})\n\n'
                "For real multi-tenant memory use the co_located provider, or wait for the "
                "tenant-scoped schema in SM-C3 (#91).",
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

        **The store path is a known gap, not an exemption.** An earlier version of this docstring
        claimed ``LearningStore.recall`` "takes all four slots", which is false for every store in
        the tree -- each filters on a subset and documents the rest as *"Additional context
        (ignored)"*:

        =====================  ==========================  =====================================
        Store                  Filters on                  Silently absorbs
        =====================  ==========================  =====================================
        ``user_profile``       ``user_id``                 agent, team, session
        ``user_memory``        ``user_id``                 agent, team, session
        ``session_context``    ``session_id``              agent, team, user
        ``learned_knowledge``  ``user_id``                 agent, team, session
        ``entity_memory``      ``user_id``                 agent, team, session
        ``decision_log``       ``agent_id``, ``session_id``  **``user_id`` is not a parameter**
        =====================  ==========================  =====================================

        So a narrowed read against a bound store can return a wider answer, stamped with the
        requesting scope -- the same invisible relabel as the tenant leak, two levels down.

        This branch does **not** refuse it, and the reason is that there is nothing to refuse
        *against*: ``LearningStore.recall`` is declared ``(self, **kwargs)``, so deriving the
        honoured slots by signature yields the empty set and refuses every store read. Which slots
        a bound store honours is part of the tier-to-store mapping, which is **SM-C2's (#90)** --
        recorded in SPEC section 11.3 rather than guessed at here. An incorrect justification is
        worse than an acknowledged gap: it is what stops the next reader adding the guard.
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
        if tier is MemoryTier.EPISODIC and MemoryTier.EPISODIC not in self.stores:
            raise MemoryOperationError(
                "The seed has no episodic store, so an episode cannot be written through this "
                "provider. Redirecting it into the semantic tier would destroy the raw, non-lossy "
                "record consolidation depends on. The episodic tier is SM-E1's (#108).",
                field_name="tier",
            )
        if tier not in self._served_tiers():
            raise MemoryOperationError(f"No seed component is bound to the {tier.value!r} tier.", field_name="tier")

    # -- introspection --------------------------------------------------------

    def substrates(self) -> Dict[Substrate, bool]:
        """Neither. The seed has no vector search over memory and no temporal graph."""
        return {Substrate.VECTOR: False, Substrate.GRAPH: False}

    def _served_tiers(self) -> List[MemoryTier]:
        tiers = set(self.stores)
        if self.manager is not None:
            tiers.add(MemoryTier.SEMANTIC)
        return [tier for tier in MemoryTier if tier in tiers]

    def _store_for(self, tier: MemoryTier) -> Optional["LearningStore"]:
        return self.stores.get(tier)

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

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        self._guard_read(scope, query)
        if not query.limit:
            # Short-circuited *before* the fetch. `MemoryManager` truncates only when `limit > 0`,
            # so asking it for zero rows materialised the user's whole table and then discarded it.
            return []
        store = self._store_for(tier)
        if store is not None:
            records = self._store_records(store, tier, scope, store.recall(**self._recall_kwargs(scope, query)))
        elif tier is MemoryTier.SEMANTIC and self.manager is not None:
            records = self._manager_records(scope, query)
        else:
            return []
        # Capped here as well as in `_slice`: MemoryManager truncates only when `limit > 0`, so
        # `limit=0` returned the whole table on this provider and nothing on the co-located one.
        return records[: query.limit] if query.limit else []

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        self._guard_read(scope, query)
        if not query.limit:
            return []
        store = self._store_for(tier)
        if store is not None:
            recalled = await store.arecall(**self._recall_kwargs(scope, query))
            records = self._store_records(store, tier, scope, recalled)
        elif tier is MemoryTier.SEMANTIC and self.manager is not None:
            records = await asyncio.to_thread(self._manager_records, scope, query)
        else:
            return []
        return records[: query.limit] if query.limit else []

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
        self._guard_read(scope, query)
        per_tier = [self.read_tier(scope, tier, query) for tier in self._wanted_tiers(query)]
        return self._slice(scope, query, self._interleave(per_tier))

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        self._guard_read(scope, query)
        # Gathered: the tiers are independent, and each SEMANTIC hop pays its own
        # `asyncio.to_thread` because `search_user_memories` has no async variant.
        per_tier = list(
            await asyncio.gather(*(self.aread_tier(scope, tier, query) for tier in self._wanted_tiers(query)))
        )
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
    def _store_receipt(updated: bool, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """What a ``LearningStore.process`` write can honestly claim.

        ``process`` returns ``None`` and mints no per-event id -- it folds a batch of messages into
        whatever shape the store keeps. So there are no ``event_ids``, and ``accepted`` reflects
        what the store said about itself via ``was_updated``.

        ``updated`` is captured by the caller *before* any suspension point and defaults to
        ``False`` for a store that does not expose the flag. Defaulting to ``True`` assumed success
        from a field whose entire purpose is not assuming it.
        """
        return WriteReceipt(
            accepted=len(events) if updated else 0,
            event_ids=[],  # process() mints none
            tiers_written=[tier] if updated else [],
        )

    def _manager_write(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        if self.manager is None:  # unreachable via write_tier; explicit beats `assert`, which -O strips
            raise MemoryOperationError(f"No seed component is bound to the {tier.value!r} tier.", field_name="tier")
        self._reject_unstorable_scope(scope)
        ids: List[str] = []
        for event in events:
            try:
                memory_id = self.manager.add_user_memory(
                    memory=UserMemory(
                        memory=event.content,
                        memory_id=event.id,
                        topics=list(event.topics) if event.topics is not None else None,
                        agent_id=scope.agent_id,
                        team_id=scope.team_id,
                        input=_provenance_note(event),
                    ),
                    user_id=scope.user_id,
                )
            except Exception as exc:
                # `add_user_memory` is called once per event, so a failure partway leaves earlier
                # events committed. Without this the ids were lost in a local and the receipt
                # reported `accepted=0, event_ids=[]` for rows that are in the database -- and a
                # caller following `PartialWriteError`'s documented retry advice re-sends them
                # with no id, which mints fresh uuid4 rows and duplicates every one.
                raise PartialWriteError(self._committed(tier, ids)) from exc
            if memory_id:
                ids.append(memory_id)
        return self._committed(tier, ids)

    @staticmethod
    def _committed(tier: MemoryTier, ids: Sequence[str]) -> WriteReceipt:
        """A receipt for rows actually persisted -- `accepted` means committed, never attempted."""
        return WriteReceipt(accepted=len(ids), event_ids=list(ids), tiers_written=[tier] if ids else [])

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """A backend failure here raises ``PartialWriteError``, exactly as ``write`` does.

        ``write_tier`` used to let the backend's own exception through while ``write`` wrapped it,
        so the same failure reached a caller as two different types depending on which verb they
        used -- and `PartialWriteError`'s docstring is explicit that both adapters must fail the
        same way or there is no portable retry.
        """
        self._validate_write(scope, events)
        self._reject_unserved(tier)
        store = self._store_for(tier)
        if store is None:
            return self._manager_write(scope, tier, events)
        updated = self._process_and_flag(store, scope, events)
        return self._store_receipt(updated, tier, events)

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Async variant of :meth:`write_tier`, with the same failure type."""
        self._validate_write(scope, events)
        self._reject_unserved(tier)
        store = self._store_for(tier)
        if store is None:
            return await asyncio.to_thread(self._manager_write, scope, tier, events)
        try:
            await store.aprocess(self._as_messages(events), **self._recall_kwargs(scope, MemoryQuery(text="")))
        except Exception as exc:
            self._shape_store_failure(exc)
        # Read synchronously, immediately after the await returns: `was_updated` is shared mutable
        # state on a store bound once at construction, so two concurrent writers interleaving here
        # read each other's flag.
        return self._store_receipt(bool(getattr(store, "was_updated", False)), tier, events)

    @staticmethod
    def _shape_store_failure(exc: BaseException) -> "NoReturn":
        """What a failed ``process`` becomes, decided once for both halves.

        A refusal stays bare here, unlike the fan-out: ``process`` is a single call, so a refusal
        means this group committed nothing. Whether an *earlier* group did is `write`'s question,
        and `_raise_fanout_failure` answers it.
        """
        if isinstance(exc, MemoryOperationError):
            raise exc
        # `process` folds a batch into the store's own shape and mints no ids, so there is nothing
        # committed to report -- but the *type* must still match the other adapter.
        raise PartialWriteError(WriteReceipt()) from exc

    def _process_and_flag(self, store: "LearningStore", scope: Scope, events: Sequence[MemoryEvent]) -> bool:
        try:
            store.process(self._as_messages(events), **self._recall_kwargs(scope, MemoryQuery(text="")))
        except Exception as exc:
            self._shape_store_failure(exc)
        return bool(getattr(store, "was_updated", False))

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
                "Per-tier consolidation policy is SM-C2's (#90) and the real loop is SM-L1's (#122).",
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
        return await asyncio.to_thread(self.consolidate, scope, tiers)


def _checked_stores(raw: Any) -> Optional[Dict[MemoryTier, "LearningStore"]]:
    """Verify bound stores at build time, as ``checked_vector`` / ``checked_graph`` do next door.

    ``LearningStore`` is already ``runtime_checkable``, so this is the same one structural check --
    and without it a misconfigured seed built cleanly and failed at first recall.
    """
    if raw is None:
        return None
    from egpt.learn.stores.protocol import LearningStore

    if not isinstance(raw, dict):
        raise MemoryProviderConfigError(
            f"config.options['stores'] must be a dict of MemoryTier -> LearningStore, got {type(raw).__name__}.",
            field_name="options.stores",
        )
    for tier, store in raw.items():
        if not isinstance(tier, MemoryTier):
            raise MemoryProviderConfigError(
                f"config.options['stores'] key {tier!r} is not a MemoryTier.", field_name="options.stores"
            )
        if not isinstance(store, LearningStore):
            raise MemoryProviderConfigError(
                f"config.options['stores'][{tier.value!r}] is a {type(store).__name__}, which does not "
                "carry every method LearningStore names.",
                field_name="options.stores",
            )
    return dict(raw)


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
