"""The memory seam: one interface every consumer depends on (SM-C1, implementing SM-R11).

Three verbs -- ``read`` (feed-forward), ``write`` (write-back), ``consolidate`` (episodic ->
semantic, async-first) -- plus tier-scoped variants and an introspection call. Every method takes
a :class:`~egpt.memory.types.Scope`, and every method has a sync and an ``a``-prefixed async twin.

**One seam, two substrates.** Consumers depend on this class alone; a provider composes the
:mod:`egpt.memory.substrates` ports underneath and owns fusion. The alternative -- exposing the
vector and graph interfaces separately and letting callers compose them -- was rejected as the
public seam because it pushes fusion, dual-write, and the Mode A/B choice into every call site
(SPEC section 3).

**Consumers never instantiate an adapter.** They call
:func:`~egpt.memory.config.build_memory_provider`, so which backend and which mode are running is
a configuration fact, not a code fact. That is what makes SM-C5 (#93) per-tenant memory possible
without touching a single call site.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Sequence

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

__all__ = ["MemoryProvider"]


class MemoryProvider(ABC):
    """The single memory seam. Adapters subclass it; consumers depend only on it.

    Both halves of every verb are abstract on purpose. A default async implementation delegating
    to the sync one would let a backend ship without a real async path and look complete -- the
    failure would surface as a blocked event loop under load, far from the omission.
    """

    #: Registry key. Concrete adapters set this so ``config.provider`` can select them.
    provider_key: str = "abstract"

    # -- read (feed-forward): query -> MemorySlice ----------------------------

    @abstractmethod
    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        """Retrieve across substrates and tiers, fused into one ranked slice.

        Fuses the vector and graph legs with **weighted / relative-score fusion** (SM-R8's
        ratified default, ``vector_weight`` about 0.5). RRF (k about 60) is selectable but
        reserved for the SM-G9-gated graph-join case. Cross-encoder rerank (SM-R8) and
        personalized PageRank (SM-R9) are both per-tenant and off by default. **No query-time LLM
        on the hot path.**

        ``query.as_of`` is applied to the **fused** set, not per substrate: the vector substrate
        has no notion of validity, so filtering per-leg lets an invalidated fact return through
        the other leg (finding F2).

        .. note::
           **Signature deviates from issue #89's body, deliberately.** The issue sketches
           ``read(tenant, agent, query, types)``. That literal is a **retired strawman** -- ADR
           0003 section 3 ratifies these signatures and asks that it not be reintroduced, after two
           sibling documents copied it from the issue text. ``tenant`` and ``agent`` fold into
           ``Scope`` so scoping threads uniformly through every method rather than being passed
           positionally, and ``types`` is ``MemoryQuery.tiers``. Same information; one way to say it.
        """
        raise NotImplementedError

    @abstractmethod
    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        """Async variant of :meth:`read`."""
        raise NotImplementedError

    # -- write (write-back): append raw events --------------------------------

    @abstractmethod
    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Append raw events, defaulting to the episodic tier.

        Cheap and non-lossy: no distillation, no extraction, no model call on the request path.
        Turning episodes into facts is :meth:`consolidate`'s job, on the background lane.

        The provider stamps one id per event and uses it across both substrates -- that id is the
        cross-substrate dedup key during fusion (finding F1).
        """
        raise NotImplementedError

    @abstractmethod
    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Async variant of :meth:`write`."""
        raise NotImplementedError

    # -- consolidate (episodic -> semantic), async-first ----------------------

    @abstractmethod
    def consolidate(
        self,
        scope: Scope,
        tiers: Optional[Sequence[MemoryTier]] = None,
    ) -> ConsolidationReport:
        """Sleep-time pass: extract -> dedup -> contradiction-resolve, writing to both substrates.

        **Complete when it returns.** An implementation may batch internally to bound memory, but
        the caller does not drive that loop and does not resume it: exposing a cursor put the
        termination condition in the caller's hands, where ignoring it silently skipped episodes
        and mis-driving it span forever. Scheduling *when* to consolidate is still SM-L1's (#122).

        ``ConsolidationReport.details`` carries at least :data:`~egpt.memory.types.CONSOLIDATION_KEYS`
        on every provider, so a scheduler reading it does not have to know which one it got.

        This is the write-side loop the whole design is built around, and the least-solved problem
        in the field -- no production system has a reliable episodic-to-semantic consolidation
        (research doc section 1).

        The async variant is the primary lane; this sync one exists for tests and manual triggers.
        Ordering constraint from SM-R5 section 6: decay-pruning runs **after** this pass, never
        before, or an episode can be pruned before its knowledge reaches the semantic tier.
        """
        raise NotImplementedError

    @abstractmethod
    async def aconsolidate(
        self,
        scope: Scope,
        tiers: Optional[Sequence[MemoryTier]] = None,
    ) -> ConsolidationReport:
        """Async variant of :meth:`consolidate`, and the primary entry point."""
        raise NotImplementedError

    # -- tier-scoped access ---------------------------------------------------

    @abstractmethod
    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        """Read one tier directly, with no cross-tier fusion.

        The seam-level counterpart of ``LearningStore.recall`` (``learn/stores/protocol.py:48``),
        which is what a wrapped seed store maps onto.
        """
        raise NotImplementedError

    @abstractmethod
    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        """Async variant of :meth:`read_tier`."""
        raise NotImplementedError

    @abstractmethod
    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Write events to a named tier, overriding each event's own ``tier``.

        The counterpart of ``LearningStore.process``. Per-tier write *policy* -- which tier a given
        event should land in -- is SM-C2's (#90); this is the mechanism it will route through.
        """
        raise NotImplementedError

    @abstractmethod
    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Async variant of :meth:`write_tier`."""
        raise NotImplementedError

    # -- introspection --------------------------------------------------------

    @abstractmethod
    def substrates(self) -> Dict[Substrate, bool]:
        """Which substrates this provider actually has wired.

        For diagnostics and health checks, and for the vector-only tenant SM-C5 (#93) supports:
        a caller can tell "the graph returned nothing" from "there is no graph".
        """
        raise NotImplementedError
