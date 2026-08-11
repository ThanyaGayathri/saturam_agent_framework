"""The seed bridge: today's memory behind the new seam (SM-C1).

ADR 0003 section 2 decided **WRAP / EXTEND, never replace** -- no seed surface is forked or
discarded. This module is that decision in code. It gives a deployment the ``MemoryProvider`` API
over the memory it already has (``LearningMachine``'s stores and ``MemoryManager``'s ``UserMemory``
CRUD), with no pgvector, no graph, and no migration.

``substrates()`` reports **both substrates absent**, and that is the honest answer rather than a
defect: the seed has no vector search over memory and no temporal knowledge graph (SPEC section 6.2
verified this against the code). A caller that needs multi-hop or bi-temporal reads gets a truthful
"there is no graph here" instead of an empty result it might read as "nothing matched".

Async is where wrapping gets awkward, and the awkwardness is recorded rather than hidden. Three
seed methods have **no async variant**: ``MemoryManager.search_user_memories``,
``MemoryManager.add_user_memory``, and ``Curator.prune`` / ``Curator.deduplicate``. Their async
paths here go through ``asyncio.to_thread`` -- a real thread hop, not an ``async def`` that never
awaits, which CLAUDE.md calls dead weight and which would block the event loop while looking
correct. Extending those seams properly belongs to the tickets that own them (SM-C2 #90 for the
stores, SM-L4 #125 for curation).
"""

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Sequence, Tuple
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
from egpt.memory.validation import reject_duplicate_ids

if TYPE_CHECKING:
    from egpt.learn.machine import LearningMachine
    from egpt.learn.stores.protocol import LearningStore
    from egpt.memory.manager import MemoryManager

__all__ = ["SeedMemoryProvider"]

#: Retrieval strategies this bridge will drive on ``MemoryManager``.
#:
#: ``agentic`` is deliberately absent -- see ``_validate_retrieval_method``. Real similarity search
#: over memory arrives with SM-V1 (#95) as a vector substrate, not by putting a model on this path.
_RETRIEVAL_METHODS = ("last_n", "first_n")

#: Namespace for projecting an id-less ``UserMemory``, so the same row projects to the same id in
#: every worker process and across restarts.
_PROJECTION_NAMESPACE = uuid5(NAMESPACE_OID, "egpt.memory.seed.projection")


def _group_by_tier(events: Sequence[MemoryEvent]) -> List[Tuple[MemoryTier, List[MemoryEvent]]]:
    """Split a batch by ``event.tier``, preserving first-seen tier order.

    A mixed batch still produces one receipt, and each group is routed and validated separately --
    so an episodic event in an otherwise-semantic batch is refused rather than silently rewritten.
    """
    grouped: Dict[MemoryTier, List[MemoryEvent]] = {}
    for event in events:
        grouped.setdefault(event.tier, []).append(event)
    return list(grouped.items())


def _provenance_note(event: MemoryEvent) -> Optional[str]:
    """Fold an event's role and metadata into the one free-text field ``UserMemory`` offers.

    Lossy, and preferable to dropping them: the seed schema has nowhere structured to put either
    (SM-C3 #91 adds real columns), and a write that reports ``accepted`` while discarding the
    caller's metadata is the kind of gap nobody notices until they need it.
    """
    parts = []
    if event.role:
        parts.append(f"role={event.role}")
    if event.metadata:
        parts.append(f"metadata={event.metadata}")
    return "; ".join(parts) or None


def _record_from_user_memory(memory: UserMemory, scope: Scope, score: Optional[float] = None) -> MemoryRecord:
    """Project a seed ``UserMemory`` into the seam's read-projection.

    ``UserMemory`` has no ``tenant_id`` and no temporal fields -- SM-C3 (#91) adds them. Until
    then the tenant comes from the caller's scope, and the bi-temporal fields stay ``None``, which
    reads correctly as "valid since forever, never invalidated, current belief".
    """
    return MemoryRecord(
        # A UserMemory with no id still needs a stable one: `MemoryRecord.id` is the fusion dedup
        # key. `hash()` is salted per process, so the same row projected to a different id in every
        # worker and across restarts -- and `abs()` folded two hash values onto one id.
        id=memory.memory_id or str(uuid5(_PROJECTION_NAMESPACE, f"{scope.key()}|{memory.memory}")),
        tier=MemoryTier.SEMANTIC,
        content=memory.memory,
        scope=scope,
        score=score,
        topics=memory.topics,
        created_at=memory.created_at,
        metadata={"input": memory.input} if memory.input else {},
    )


class SeedMemoryProvider(MemoryProvider):
    """A ``MemoryProvider`` backed by the seed's existing memory, wrapping rather than replacing.

    Tier routing follows ADR 0003 section 2.2's ratified map, which SM-C2 (#90) turns into full
    per-tier policy:

    * ``SEMANTIC`` -> ``MemoryManager`` (``UserMemory`` CRUD), or a bound ``LearningStore``.
    * ``WORKING`` / ``PROCEDURAL`` -> a bound ``LearningStore``, if one is supplied.
    * ``EPISODIC`` -> **nothing**. The seed has no episodic store; that is net-new work
      (SM-E1 #108). Reading it returns empty, writing it is refused rather than silently
      redirected into the semantic tier, which would corrupt the distinction the whole design
      rests on.

    ``stores`` binds a tier to a seed ``LearningStore`` explicitly. Which of the six stores belongs
    to which tier is SM-C2's decision, so this takes the mapping rather than assuming it.
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
        self._validate_retrieval_method()

    def _require_declared_tenant(self) -> str:
        """The one tenant this instance may ever serve. **Mandatory.**

        Making the *acknowledgement* mandatory and the *pin* optional was a regression: with no pin
        the guard's ``None`` branch admitted every scope, so an unpinned provider served one
        tenant's memory to another -- relabelled with the reader's tenant, which is the invisible
        half. That was weaker in its default configuration than the lazy pin it replaced, even
        though the lazy pin was correctly rejected as racy and per-instance.

        So the pin is the thing required. There is no configuration of this provider that accepts
        two tenants, and none that has to discover its tenant at runtime.
        """
        declared = self.config.options.get("tenant_id")
        if not isinstance(declared, str) or not declared.strip():
            raise MemoryProviderConfigError(
                "SeedMemoryProvider requires options['tenant_id']. UserMemory has no tenant column "
                "and MemoryManager partitions by user_id alone, so pinning this instance to one "
                "declared tenant is the only isolation available -- and an undeclared pin means no "
                "isolation at all, with the leaked row stamped as the reader's own.\n\n"
                '    MemoryProviderConfig(provider="seed", options={"single_tenant": True, '
                '"tenant_id": "acme"})',
                field_name="options.tenant_id",
            )
        return declared.strip()

    def _validate_retrieval_method(self) -> None:
        """Refuse a retrieval method that would put a model call on the read path.

        ``MemoryManager.search_user_memories(retrieval_method="agentic")`` runs an LLM round trip
        (`manager.py` -> ``_search_user_memories_agentic`` -> ``model.response``), and
        ``MemoryProvider.read`` states in bold that there is **no query-time LLM on the hot path**.
        On the async side it would hold a pooled thread for the duration of that call. Both claims
        cannot stand, and the contract is the one worth keeping.

        A typo is refused too. The manager's own dispatch falls through to ``last_n`` for anything
        it does not recognise, so ``"lastn"`` silently degraded to a different retrieval strategy.
        """
        method = self.config.options.get("retrieval_method", "last_n")
        if method not in _RETRIEVAL_METHODS:
            known = ", ".join(repr(m) for m in _RETRIEVAL_METHODS)
            extra = (
                " 'agentic' is excluded deliberately: it runs an LLM call, and MemoryProvider.read "
                "promises no query-time LLM on the hot path."
                if method == "agentic"
                else ""
            )
            raise MemoryProviderConfigError(
                f"Unknown retrieval_method {method!r}. Expected one of: {known}.{extra}",
                field_name="options.retrieval_method",
            )

    def _require_single_tenant_acknowledgement(self) -> None:
        """Refuse to build unless the deployment states it is single-tenant.

        **The seed cannot honour ``Scope.tenant_id``, and the failure was worse than doing
        nothing.** ``UserMemory`` has no tenant column, and ``MemoryManager.search_user_memories``
        / ``add_user_memory`` partition on ``user_id`` alone -- so the tenant was accepted on every
        call and used on none:

            write(Scope(tenant_id='acme',   user_id='ada')) -> "acme Q4 revenue is 12M"
            read (Scope(tenant_id='globex', user_id='ada')) -> ["acme Q4 revenue is 12M"]

        What made it critical rather than merely absent is the relabel: the projection stamped the
        *reader's* scope onto the row, so the leaked memory came back looking correctly tenanted.
        Every defence above this layer -- audit logs, ``record.scope.tenant_id`` assertions, a
        post-fusion tenant filter -- would have seen a well-formed ``globex`` record. A leak that is
        invisible to the layer above it is worse than one that returns nothing.

        So this fails closed. Namespacing the manager key (``f"{tenant}/{user}"``) is the tempting
        alternative and is worse here: it silently orphans every row an existing seed deployment
        already wrote -- reads return nothing, writes go elsewhere, no error anywhere. That
        migration belongs with the schema that gains a real ``tenant_id`` column (SM-C3 #91).

        Opting in is one config key, and it is a claim the operator is in a position to make:

            MemoryProviderConfig(provider="seed", options={"single_tenant": True})
        """
        if not self.config.options.get("single_tenant"):
            raise MemoryProviderConfigError(
                "SeedMemoryProvider cannot enforce tenant isolation: UserMemory has no tenant "
                "column and MemoryManager partitions by user_id alone, so Scope.tenant_id is "
                "accepted and never applied. Two tenants sharing a user_id would read each "
                "other's memories, and the projection would stamp the reader's tenant onto the "
                "result, hiding the leak from every check above this layer.\n\n"
                "If this deployment serves exactly one tenant, say so explicitly:\n"
                '    MemoryProviderConfig(provider="seed", options={"single_tenant": True})\n\n'
                "For real multi-tenant memory use the co_located provider, or wait for the "
                "tenant-scoped schema in SM-C3 (#91).",
                field_name="options.single_tenant",
            )

    def _guard_tenant(self, scope: Scope) -> None:
        """Refuse a scope from a different tenant than the one this provider is configured for.

        ``single_tenant: True`` is a claim about the deployment, not a blanket exemption.

        The pin comes from ``options["tenant_id"]`` -- **config, not a first-request side effect.**
        Pinning lazily on the first call was two bugs at once: the state is per-instance, so two
        workers each pinned their own tenant and each served it happily (and
        ``build_memory_provider`` is called per process, which is the ordinary deployment shape);
        and the check-then-set was an unsynchronised read-modify-write that could admit two tenants
        under thread interleaving. A value declared in config is per-deployment and auditable,
        which a first-request side effect is neither.

        The pin is mandatory (see :meth:`_require_declared_tenant`), so there is no branch here
        that admits an unpinned instance -- that branch was the leak.
        """
        if self._tenant != scope.tenant_id:
            raise MemoryOperationError(
                f"This SeedMemoryProvider is configured single_tenant for {self._tenant!r}; "
                f"refusing a call scoped to {scope.tenant_id!r}. The seed's storage cannot "
                "separate them, so serving this would be a cross-tenant read.",
                field_name="tenant_id",
            )

    # -- introspection --------------------------------------------------------

    def substrates(self) -> Dict[Substrate, bool]:
        """Neither. The seed has no vector-search-over-memory and no temporal graph."""
        return {Substrate.VECTOR: False, Substrate.GRAPH: False}

    def _served_tiers(self) -> List[MemoryTier]:
        tiers = set(self.stores)
        if self.manager is not None:
            tiers.add(MemoryTier.SEMANTIC)
        return [tier for tier in MemoryTier if tier in tiers]

    def _store_for(self, tier: MemoryTier) -> Optional["LearningStore"]:
        return self.stores.get(tier)

    # -- context helpers ------------------------------------------------------

    def _recall_kwargs(self, scope: Scope, query: MemoryQuery) -> Dict[str, Any]:
        """The keyword contract ``LearningStore.recall`` and ``LearningMachine.recall`` expect.

        ``message`` and ``query`` are both passed because the seed's stores disagree about which
        one they read -- ``learn/machine.py`` sets both for exactly this reason.
        """
        return {
            "user_id": scope.user_id,
            "session_id": scope.session_id,
            "agent_id": scope.agent_id,
            "team_id": scope.team_id,
            "message": query.text,
            "query": query.text,
        }

    def _store_records(self, store: "LearningStore", tier: MemoryTier, scope: Scope, data: Any) -> List[MemoryRecord]:
        """Turn what a store recalled into records, via its own ``build_context``.

        One record per store, not one per underlying item. A ``LearningStore`` returns whatever
        shape it likes from ``recall`` and only ``build_context`` knows how to render it -- so the
        rendered context *is* the projection. Parsing a store's internal shape here would couple
        the seam to six store implementations and break the moment one changed.
        """
        if data is None:
            return []
        context = store.build_context(data=data)
        if not context:
            return []
        return [
            MemoryRecord(
                id=f"{store.learning_type}:{scope.key()}",
                tier=tier,
                content=context,
                scope=scope,
                provenance={"learning_type": store.learning_type},
            )
        ]

    # -- read -----------------------------------------------------------------

    def _reject_unservable_substrates(self, query: MemoryQuery) -> None:
        """Refuse a read that names a substrate this provider does not have.

        ``substrates`` was ignored entirely here while the co-located adapter honours it, so the
        same query meant two different things on two registered providers --
        ``read(substrates=[GRAPH])`` returned rows from a provider whose own ``substrates()`` says
        it has no graph. That breaks the config-only swap property the seam exists to provide, and
        it is the very asymmetry ``MemoryQuery.resolved_tiers`` was fixed to close one module over.

        Refusing rather than returning empty, because naming a substrate this provider cannot have
        is a mistaken assumption about the deployment, not a query that legitimately matches nothing.
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

        ``UserMemory`` has no bi-temporal columns (SM-C3 #91 adds them) and ``LearningStore`` has
        no notion of validity at all, so ``as_of`` was accepted and silently ignored -- the caller
        got present-day memory labelled as a historical answer. Refusing is the only honest option
        for a query whose whole point is that the answer is time-qualified.
        """
        if query.as_of is not None:
            raise MemoryOperationError(
                "The seed provider cannot answer a point-in-time read: UserMemory and the learn "
                "stores carry no valid-time columns, so as_of would be ignored and present-day "
                "memory returned as a historical answer. Bi-temporal storage is SM-C3's (#91).",
                field_name="as_of",
            )

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        self._guard_tenant(scope)
        self._reject_as_of(query)
        self._reject_unservable_substrates(query)
        store = self._store_for(tier)
        if store is not None:
            records = self._store_records(store, tier, scope, store.recall(**self._recall_kwargs(scope, query)))
        elif tier is MemoryTier.SEMANTIC and self.manager is not None:
            records = self._manager_records(scope, query)
        else:
            return []
        # Cap here as well as in `_slice`. MemoryManager truncates only when `limit > 0`
        # (`manager.py`), so `read_tier(limit=0)` returned the tenant's whole table while the
        # co-located provider returned nothing for the same call -- through the seam whose promise
        # is that a consumer behaves the same on either.
        return records[: query.limit] if query.limit else []

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        self._guard_tenant(scope)
        self._reject_as_of(query)
        self._reject_unservable_substrates(query)
        store = self._store_for(tier)
        if store is not None:
            recalled = await store.arecall(**self._recall_kwargs(scope, query))
            records = self._store_records(store, tier, scope, recalled)
        elif tier is MemoryTier.SEMANTIC and self.manager is not None:
            # search_user_memories has no async variant; a thread hop is honest, a bare `async
            # def` around the sync call would block the loop while looking correct.
            records = await asyncio.to_thread(self._manager_records, scope, query)
        else:
            return []
        return records[: query.limit] if query.limit else []

    def _manager_records(self, scope: Scope, query: MemoryQuery) -> List[MemoryRecord]:
        """Retrieve through ``MemoryManager``. **This is not vector search.**

        ``search_user_memories`` offers ``last_n`` / ``first_n`` / ``agentic``; the manager has no
        embedder (SPEC section 6.1). ``retrieval_method`` comes from ``config.options`` and defaults
        to ``last_n`` -- recency, which is a defensible default and an honest one. Real similarity
        search over memory arrives with SM-V1 (#95) as a vector substrate.
        """
        if self.manager is None:  # unreachable via read_tier; explicit beats `assert`, which -O strips
            return []
        method = self.config.options.get("retrieval_method", "last_n")
        memories = self.manager.search_user_memories(
            query=query.text,
            limit=query.limit,
            retrieval_method=method,
            user_id=scope.user_id,
        )
        return [_record_from_user_memory(memory, scope) for memory in memories or []]

    def _slice(self, scope: Scope, query: MemoryQuery, records: List[MemoryRecord]) -> MemorySlice:
        capped = records[: query.limit]
        by_tier: Dict[MemoryTier, List[MemoryRecord]] = {}
        for record in capped:
            by_tier.setdefault(record.tier, []).append(record)
        return MemorySlice(
            scope=scope,
            query=query,
            records=capped,
            by_tier=by_tier,
            # Every FUSION_KEYS entry is present even though nothing is fused, so a consumer
            # written against one provider does not KeyError on another -- the swap property this
            # seam exists to provide. `method=None` and zero hits are the honest values: there are
            # no legs here, not an unknown number of them.
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

    @staticmethod
    def _interleave(per_tier: List[List[MemoryRecord]]) -> List[MemoryRecord]:
        """Round-robin across tiers so the cap does not silently drop the later ones.

        Each tier is asked for up to ``limit`` records and the slice then caps at ``limit``, so
        concatenating in declaration order let the first tier consume the whole budget -- a
        two-tier read returned semantic memory only, and the procedural tier never appeared.

        Round-robin is the honest merge here because **the seed produces no scores**: there is no
        embedder and ``LearningStore.recall`` returns rendered context, so there is nothing to rank
        by and any ordering claim would be invented. Giving every tier a turn is a fair share of a
        fixed budget rather than a relevance ordering, and ``MemorySlice.records`` for this provider
        should be read that way.
        """
        merged: List[MemoryRecord] = []
        for column in range(max((len(records) for records in per_tier), default=0)):
            for records in per_tier:
                if column < len(records):
                    merged.append(records[column])
        return merged

    def _guard_read(self, scope: Scope, query: MemoryQuery) -> None:
        """Run every read guard up front, so they apply even when no tier is served.

        Previously the guards lived only inside ``read_tier``, so a read naming an unserved tier
        skipped them entirely -- ``read(tiers=[PROCEDURAL], as_of=999)`` returned ``[]`` with no
        error, which is exactly the "present-day memory read as a historical answer" misreading
        ``_reject_as_of`` exists to prevent, just with an empty answer.
        """
        self._guard_tenant(scope)
        self._reject_as_of(query)
        self._reject_unservable_substrates(query)

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        self._guard_read(scope, query)
        per_tier = [self.read_tier(scope, tier, query) for tier in self._wanted_tiers(query)]
        return self._slice(scope, query, self._interleave(per_tier))

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        self._guard_read(scope, query)
        per_tier = [await self.aread_tier(scope, tier, query) for tier in self._wanted_tiers(query)]
        return self._slice(scope, query, self._interleave(per_tier))

    # -- write ----------------------------------------------------------------

    @staticmethod
    def _reject_foreign_scopes(scope: Scope, events: Sequence[MemoryEvent]) -> None:
        """Refuse an event carrying a different scope than the write.

        ``MemoryEvent.scope`` is a required field that was read by nothing, so a caller building
        events per tenant and batching them through one ``write`` silently got them all under the
        call scope -- accepted-and-ignored on the one field whose mismatch is a tenancy question.
        """
        for event in events:
            if event.scope != scope:
                raise MemoryOperationError(
                    f"MemoryEvent.scope ({event.scope.key()}) does not match the write scope "
                    f"({scope.key()}). One write is one scope; batch per scope.",
                    field_name="scope",
                )

    def _reject_unserved(self, tier: MemoryTier) -> None:
        """Refuse a tier nothing here can store, naming the ticket that will build it."""
        if tier is MemoryTier.EPISODIC and MemoryTier.EPISODIC not in self.stores:
            raise MemoryOperationError(
                "The seed has no episodic store, so an episode cannot be written through this provider. "
                "Redirecting it into the semantic tier would destroy the raw, non-lossy record "
                "consolidation depends on. The episodic tier is SM-E1's (#108).",
                field_name="tier",
            )
        # `_served_tiers()` already includes every bound store, so the second clause the earlier
        # version carried (`and tier not in self.stores`) could never be reached.
        if tier not in self._served_tiers():
            raise MemoryOperationError(
                f"No seed component is bound to the {tier.value!r} tier.",
                field_name="tier",
            )

    def _as_messages(self, events: Sequence[MemoryEvent]) -> List[Dict[str, Any]]:
        """Events in the message shape ``LearningStore.process`` consumes.

        ``metadata`` and ``occurred_at`` ride along rather than being dropped: a store that ignores
        the extra keys is unaffected, and one that reads them no longer has to be told twice. The
        earlier version passed only role and content while reporting the event accepted, so a
        caller's metadata vanished with no signal.
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

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        self._guard_tenant(scope)
        self._reject_foreign_scopes(scope, events)
        reject_duplicate_ids(events)
        self._reject_unserved(tier)
        store = self._store_for(tier)
        if store is not None:
            store.process(self._as_messages(events), **self._recall_kwargs(scope, MemoryQuery(text="")))
            return self._store_receipt(store, tier, events)
        return self._manager_write(scope, tier, events)

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        self._guard_tenant(scope)
        self._reject_foreign_scopes(scope, events)
        reject_duplicate_ids(events)
        self._reject_unserved(tier)
        store = self._store_for(tier)
        if store is not None:
            await store.aprocess(self._as_messages(events), **self._recall_kwargs(scope, MemoryQuery(text="")))
            return self._store_receipt(store, tier, events)
        # add_user_memory has no async variant either.
        return await asyncio.to_thread(self._manager_write, scope, tier, events)

    @staticmethod
    def _store_receipt(store: "LearningStore", tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """What a ``LearningStore.process`` write can honestly claim.

        ``process`` returns ``None`` and mints no per-event id -- it folds a batch of messages into
        whatever shape the store keeps. So there are no ``event_ids`` to report, and ``accepted``
        reflects what the store said about itself via ``was_updated`` rather than assuming every
        event landed. Reporting ``accepted=len(events)`` unconditionally claimed knowledge this
        path does not have.
        """
        updated = bool(getattr(store, "was_updated", True))
        return WriteReceipt(
            accepted=len(events) if updated else 0,
            event_ids=[],  # process() mints none; see docstring
            tiers_written=[tier] if updated else [],
        )

    def _manager_write(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        if self.manager is None:  # unreachable via write_tier; explicit beats `assert`, which -O strips
            raise MemoryOperationError(
                f"No seed component is bound to the {tier.value!r} tier.",
                field_name="tier",
            )
        ids: List[str] = []
        for event in events:
            memory_id = self.manager.add_user_memory(
                memory=UserMemory(
                    memory=event.content,
                    memory_id=event.id,
                    topics=list(event.topics) if event.topics is not None else None,
                    agent_id=scope.agent_id,
                    team_id=scope.team_id,
                    # `input` is the seed's field for the utterance a memory came from, which is
                    # the closest home UserMemory has for an event's role/metadata. Without this
                    # the write silently discarded both while reporting success.
                    input=_provenance_note(event),
                ),
                user_id=scope.user_id,
            )
            if memory_id:
                ids.append(memory_id)
        return WriteReceipt(accepted=len(ids), event_ids=ids, tiers_written=[tier] if ids else [])

    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Route each event to **its own tier**, rather than forcing the batch into SEMANTIC.

        The earlier version hardcoded ``SEMANTIC``, so a default ``MemoryEvent`` -- whose tier is
        ``EPISODIC`` (``types.py``), and which ``MemoryProvider.write`` documents as landing there
        -- was silently redirected into the semantic tier. That is exactly the behaviour this
        module's own docstring and SPEC section 11.2 said it refuses, and the test named for the
        refusal only exercised the explicit ``write_tier`` path.

        Now the batch is grouped by tier and each group goes through ``write_tier``, so
        ``_reject_unserved`` gets to do its job and an episodic write is refused rather than
        rewritten.

        Every group is validated **before any of them writes**. Validating inside ``write_tier``
        meant a later group's refusal arrived after earlier groups had already committed, so a
        mixed batch -- the ordinary case, since ``MemoryEvent`` defaults to ``EPISODIC`` -- half
        landed and then raised, and whether the write was atomic depended on the caller's list
        order. ``_reject_unserved`` depends only on the tier and this provider's wiring, so
        hoisting it costs nothing.
        """
        groups = self._validated_groups(scope, events)
        receipts: List[WriteReceipt] = []
        try:
            for tier, group in groups:
                receipts.append(self.write_tier(scope, tier, group))
        except Exception as exc:
            # A *runtime* failure mid-fan-out, distinct from the validation-driven partial commit
            # hoisting fixed: `add_user_memory` is called once per event, so group n+1 runs after
            # group n has committed. Mode A raises PartialWriteError with the stamped ids for
            # exactly this, and two providers behind one seam must fail the same way or a caller
            # has no portable retry.
            raise PartialWriteError(self._merge_receipts(receipts)) from exc
        return self._merge_receipts(receipts)

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Async variant of :meth:`write`, with the same per-tier routing."""
        groups = self._validated_groups(scope, events)
        receipts: List[WriteReceipt] = []
        try:
            for tier, group in groups:
                receipts.append(await self.awrite_tier(scope, tier, group))
        except Exception as exc:
            raise PartialWriteError(self._merge_receipts(receipts)) from exc
        return self._merge_receipts(receipts)

    def _validated_groups(
        self, scope: Scope, events: Sequence[MemoryEvent]
    ) -> List[Tuple[MemoryTier, List[MemoryEvent]]]:
        """Group by tier and validate everything **before** any group writes.

        The tenant guard runs here rather than only inside ``write_tier`` because an empty batch
        produces no groups, so every per-group guard was skipped and a scope this provider would
        otherwise refuse got an empty receipt instead of an error. A guard whose coverage depends
        on the batch being non-empty is not covering "every public entry point".
        """
        self._guard_tenant(scope)
        self._reject_foreign_scopes(scope, events)
        reject_duplicate_ids(events)
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

    # -- consolidate ----------------------------------------------------------

    def consolidate(
        self,
        scope: Scope,
        tiers: Optional[Sequence[MemoryTier]] = None,
    ) -> ConsolidationReport:
        """Run the seed's curation as the dedup lane. There is no episodic-to-semantic pass here.

        ``Curator`` (``learn/curate.py:29``) is narrower than ADR 0003 section 2.1's "REUSE inside
        consolidate" implies -- its own docstring says *"Currently supports user_profile store
        only"*, and it exposes ``prune`` / ``deduplicate`` with no async variants. So this wires it
        as the dedup hook it can actually serve, and reports zero for everything it cannot do,
        rather than reporting a consolidation that did not happen.

        The real loop -- extract, merge, resolve contradictions, write both substrates -- needs an
        episodic tier to read from and a graph to write to, and the seed has neither. It belongs to
        SM-L1 (#122) onward.
        """
        self._guard_tenant(scope)
        # Polarity is the *opposite* of the co-located adapter's, and the guard was copy-adapted
        # from it without flipping: this provider accepts only `tiers=None`. `[EPISODIC]` -- the
        # one set co-located accepts -- is the single tier the seed refuses everywhere else,
        # because it has no episodic store at all.
        if tiers is not None:
            requested = sorted(tier.value for tier in tiers)
            raise MemoryOperationError(
                f"The seed provider runs only the curator's dedup lane -- it has no episodic tier "
                f"to consolidate from -- but was asked to consolidate {requested}. Pass None. "
                "Per-tier consolidation policy is SM-C2's (#90) and the real loop is SM-L1's (#122).",
                field_name="tiers",
            )

        can_curate = self.machine is not None and scope.user_id is not None
        merged = self.machine.curator.deduplicate(user_id=scope.user_id) if can_curate else 0  # type: ignore[union-attr, arg-type]
        return ConsolidationReport(
            scope=scope,
            episodic_scanned=0,  # no episodic store exists to scan
            semantic_written=0,  # no distillation without extraction
            duplicates_merged=merged,
            contradictions_resolved=0,
            # Every CONSOLIDATION_KEYS entry is present even though this provider distils nothing,
            # so a scheduler written against one adapter does not KeyError on another. `ran` and
            # `curator` ride along as extras.
            details=consolidation_details(
                derived_by="seed.curator_dedup" if can_curate else None,
                batches=1 if can_curate else 0,
                complete=True,
                ran=can_curate,
                skipped_because=None
                if can_curate
                else ("no LearningMachine wired" if self.machine is None else "scope has no user_id"),
                curator="user_profile only",
                note="seed has no episodic tier or graph; the consolidation loop is SM-L1+",
            ),
        )

    async def aconsolidate(
        self,
        scope: Scope,
        tiers: Optional[Sequence[MemoryTier]] = None,
    ) -> ConsolidationReport:
        """Async variant. ``Curator.deduplicate`` is sync-only, so this hops a thread."""
        return await asyncio.to_thread(self.consolidate, scope, tiers)


@register_provider("seed")
def _build_seed(config: MemoryProviderConfig) -> MemoryProvider:
    """Build the seed-backed provider from config.

    Like the co-located factory, the collaborators arrive through ``config.options``: a
    ``MemoryManager`` and a ``LearningMachine`` are user-constructed objects holding a db and a
    model, not things a registry key can build.
    """
    return SeedMemoryProvider(
        manager=config.options.get("memory_manager"),
        machine=config.options.get("learning_machine"),
        stores=config.options.get("stores"),
        config=config,
    )
