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
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from egpt.db.schemas import UserMemory
from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError, register_provider
from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    ConsolidationReport,
    MemoryEvent,
    MemoryQuery,
    MemoryRecord,
    MemorySlice,
    Scope,
    Substrate,
    WriteReceipt,
)

if TYPE_CHECKING:
    from egpt.learn.machine import LearningMachine
    from egpt.learn.stores.protocol import LearningStore
    from egpt.memory.manager import MemoryManager

__all__ = ["SeedMemoryProvider"]


def _record_from_user_memory(memory: UserMemory, scope: Scope, score: Optional[float] = None) -> MemoryRecord:
    """Project a seed ``UserMemory`` into the seam's read-projection.

    ``UserMemory`` has no ``tenant_id`` and no temporal fields -- SM-C3 (#91) adds them. Until
    then the tenant comes from the caller's scope, and the bi-temporal fields stay ``None``, which
    reads correctly as "valid since forever, never invalidated, current belief".
    """
    return MemoryRecord(
        id=memory.memory_id or "",
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

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        store = self._store_for(tier)
        if store is not None:
            return self._store_records(store, tier, scope, store.recall(**self._recall_kwargs(scope, query)))
        if tier is MemoryTier.SEMANTIC and self.manager is not None:
            return self._manager_records(scope, query)
        return []

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        store = self._store_for(tier)
        if store is not None:
            recalled = await store.arecall(**self._recall_kwargs(scope, query))
            return self._store_records(store, tier, scope, recalled)
        if tier is MemoryTier.SEMANTIC and self.manager is not None:
            # search_user_memories has no async variant; a thread hop is honest, a bare `async
            # def` around the sync call would block the loop while looking correct.
            return await asyncio.to_thread(self._manager_records, scope, query)
        return []

    def _manager_records(self, scope: Scope, query: MemoryQuery) -> List[MemoryRecord]:
        """Retrieve through ``MemoryManager``. **This is not vector search.**

        ``search_user_memories`` offers ``last_n`` / ``first_n`` / ``agentic``; the manager has no
        embedder (SPEC section 6.1). ``retrieval_method`` comes from ``config.options`` and defaults
        to ``last_n`` -- recency, which is a defensible default and an honest one. Real similarity
        search over memory arrives with SM-V1 (#95) as a vector substrate.
        """
        assert self.manager is not None  # guarded by the caller
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
            # No fusion happens: there is one leg, because there are no substrates to combine.
            # Saying so explicitly beats reporting a method that never ran.
            fusion={"method": None, "reason": "seed provider has no substrates to fuse"},
        )

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        wanted = [tier for tier in query.resolved_tiers() if tier in self._served_tiers()]
        records: List[MemoryRecord] = []
        for tier in wanted:
            records.extend(self.read_tier(scope, tier, query))
        return self._slice(scope, query, records)

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        wanted = [tier for tier in query.resolved_tiers() if tier in self._served_tiers()]
        records: List[MemoryRecord] = []
        for tier in wanted:
            records.extend(await self.aread_tier(scope, tier, query))
        return self._slice(scope, query, records)

    # -- write ----------------------------------------------------------------

    def _reject_unserved(self, tier: MemoryTier) -> None:
        if tier is MemoryTier.EPISODIC and MemoryTier.EPISODIC not in self.stores:
            raise MemoryProviderConfigError(
                "The seed has no episodic store, so an episode cannot be written through this provider. "
                "Redirecting it into the semantic tier would destroy the raw, non-lossy record "
                "consolidation depends on. The episodic tier is SM-E1's (#108).",
                field_name="tier",
            )
        if tier not in self._served_tiers() and tier not in self.stores:
            raise MemoryProviderConfigError(
                f"No seed component is bound to the {tier.value!r} tier.",
                field_name="tier",
            )

    def _as_messages(self, events: Sequence[MemoryEvent]) -> List[Dict[str, str]]:
        """Events in the message shape ``LearningStore.process`` consumes."""
        return [{"role": event.role or "user", "content": event.content} for event in events]

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        self._reject_unserved(tier)
        store = self._store_for(tier)
        if store is not None:
            store.process(self._as_messages(events), **self._recall_kwargs(scope, MemoryQuery(text="")))
            return WriteReceipt(accepted=len(events), tiers_written=[tier])
        return self._manager_write(scope, tier, events)

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        self._reject_unserved(tier)
        store = self._store_for(tier)
        if store is not None:
            await store.aprocess(self._as_messages(events), **self._recall_kwargs(scope, MemoryQuery(text="")))
            return WriteReceipt(accepted=len(events), tiers_written=[tier])
        # add_user_memory has no async variant either.
        return await asyncio.to_thread(self._manager_write, scope, tier, events)

    def _manager_write(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        assert self.manager is not None  # guarded by _reject_unserved
        ids: List[str] = []
        for event in events:
            memory_id = self.manager.add_user_memory(
                memory=UserMemory(
                    memory=event.content,
                    memory_id=event.id,
                    topics=event.topics,
                    agent_id=scope.agent_id,
                    team_id=scope.team_id,
                ),
                user_id=scope.user_id,
            )
            if memory_id:
                ids.append(memory_id)
        return WriteReceipt(accepted=len(ids), event_ids=ids, tiers_written=[tier] if ids else [])

    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return self.write_tier(scope, MemoryTier.SEMANTIC, events)

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return await self.awrite_tier(scope, MemoryTier.SEMANTIC, events)

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
        merged = 0
        if self.machine is not None and scope.user_id:
            merged = self.machine.curator.deduplicate(user_id=scope.user_id)
        return ConsolidationReport(
            scope=scope,
            episodic_scanned=0,  # no episodic store exists to scan
            semantic_written=0,  # no distillation without extraction
            duplicates_merged=merged,
            contradictions_resolved=0,
            details={
                "curator": "user_profile only",
                "note": "seed has no episodic tier or graph; the consolidation loop is SM-L1+",
            },
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
