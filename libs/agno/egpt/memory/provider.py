"""The memory seam: one interface every consumer depends on (SM-C1, implementing SM-R11).

Three verbs -- ``read`` (feed-forward), ``write`` (write-back), ``consolidate`` (episodic ->
semantic, async-first) -- plus tier-scoped variants and an introspection call. Every method takes
a :class:`~egpt.memory.types.Scope`, and every method has a sync and an ``a``-prefixed async twin.

**One seam, two substrates.** Consumers depend on this class alone; a provider composes the
:mod:`egpt.memory.substrates` ports underneath. The alternative -- exposing the vector and graph
interfaces separately and letting callers compose them -- was rejected as the public seam because
it pushes leg-merging, dual-write, and the Mode A/B choice into every call site (SPEC section 3).

**Manual invalidation is not on this seam.** Recording that a fact became false is
``GraphSubstrate.invalidate``, a substrate port, and consolidation is the only thing in SM-C1 that
drives it. Adding ``invalidate`` / ``ainvalidate`` here would expand the verb set ADR 0003 section
3 ratifies, so a caller that needs it today reaches the concrete adapter deliberately -- as the
cookbook does, with a comment saying so. Promoting it is SM-L5's (#126), which owns contradiction
resolution.

**Consumers never instantiate an adapter.** They call
:func:`~egpt.memory.config.build_memory_provider`, so which backend and which mode are running is
a configuration fact, not a code fact. That is what makes SM-C5 (#93) per-tenant memory possible
without touching a single call site.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

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

if TYPE_CHECKING:  # `config` imports this module, so the annotation cannot be a runtime import
    from egpt.memory.config import MemoryProviderConfig

__all__ = ["MemoryProvider"]


class MemoryProvider(ABC):
    """The single memory seam. Adapters subclass it; consumers depend only on it.

    Both halves of every verb are abstract on purpose. A default async implementation delegating
    to the sync one would let a backend ship without a real async path and look complete -- the
    failure would surface as a blocked event loop under load, far from the omission.
    """

    #: Registry key. Concrete adapters set this so ``config.provider`` can select them.
    provider_key: str = "abstract"

    #: The config the adapter was built from. Declared on the contract rather than left to each
    #: adapter because the seam's mode story reads it: SM-C5 (#93) resolves ``config.mode`` per
    #: tenant *through* the seam, and both the swap tests and the cookbook reach it through a
    #: ``MemoryProvider``-typed reference. Its absence here is what forced the cookbook's cast.
    config: "MemoryProviderConfig"

    # -- read (feed-forward): query -> MemorySlice ----------------------------

    @abstractmethod
    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        """Retrieve across substrates and tiers, fused into one ranked slice.

        How the legs are combined is the adapter's business; ``MemorySlice.fusion`` reports it.
        Ranked fusion strategies and their configuration are SM-H2's (#132) -- SM-R8 measured
        dense+BM25, never this seam's vector+graph pairing, so there is nothing yet to choose
        between. **No query-time LLM on the hot path**, whatever the strategy.

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
        cross-substrate dedup key on the read path (finding F1).
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

        **One bounded pass, and the caller does not resume it.** An implementation may cap how much
        it reads; ``details["complete"]`` says whether the tier was drained. There is deliberately
        no cursor in this signature -- it would put the termination condition in the caller's hands,
        where ignoring it silently skips episodes. An implementation MAY track a resumption cursor
        of its own, internally, so that repeated calls jointly drain a tier larger than one pass --
        ``CoLocatedMemoryProvider`` does (SM-L1 #122) -- but that state is never exposed here: a
        caller that never calls again has lost nothing it was ever given, and one that does call
        again makes progress without having to pass anything back. Scheduling passes at all --
        which scopes, how often, retrying a failed one -- is :mod:`egpt.memory.consolidation`'s
        (SM-L1 #122).

        **It distils and writes; it does not retire.** A provider derives a fact id from the
        episodes it came from, so a group whose membership changes mints a *new* id and the
        previous row stays current belief -- one logical fact, two rows. Retiring the subsumed row
        needs supersede semantics that cover growth, shrinkage and split/merge, which belong with
        the extraction strategy that decides group membership: SM-L3 (#124). Until then a provider
        must say so in ``details["supersede"]`` rather than leave a caller to discover it.

        ``ConsolidationReport.details`` carries at least :data:`~egpt.memory.types.CONSOLIDATION_KEYS`
        on every provider, so a scheduler reading it does not have to know which one it got.

        This is the write-side loop the whole design is built around, and the least-solved problem
        in the field -- no production system has a reliable episodic-to-semantic consolidation
        (research doc section 1).

        The async variant is the primary lane; this sync one exists for tests and manual triggers.
        Ordering constraint from SM-R5 section 6: decay-pruning runs **after** this pass, never
        before, or an episode can be pruned before its knowledge reaches the semantic tier.

        **Concurrent calls on the same scope and tier are safe, not free.** Idempotent upsert
        (by content-derived fact id) means two overlapping passes cannot corrupt state or produce
        duplicate rows, but an implementation that tracks a resumption cursor internally (the
        previous paragraph) reads it once per call, before either has advanced it -- so both scan
        and distil the same window, redoing one pass's work rather than splitting it.
        ``ConsolidationWorker`` never does this to itself (one call in flight per scope at a
        time); a caller driving ``consolidate``/``aconsolidate`` directly, outside that worker,
        is responsible for serializing calls to the same scope if avoiding the redundant work
        matters to it.
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
        """Read one tier directly, without merging across tiers.

        The seam-level counterpart of ``LearningStore.recall`` (``learn/stores/protocol.py:48``),
        which is what a wrapped seed store maps onto.

        **A tier this provider does not serve reads empty; it is not refused.** One rule, so a
        caller can write one handler: reads report absence, and only **writes** refuse an unserved
        tier -- writing where nothing will store it loses data, reading there simply finds nothing.
        :meth:`tiers` is how a caller tells the two apart.
        """
        raise NotImplementedError

    @abstractmethod
    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        """Async variant of :meth:`read_tier`."""
        raise NotImplementedError

    @abstractmethod
    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Write events to a named tier, overriding each event's own ``tier``.

        The counterpart of ``LearningStore.process``. Where a tier's writes then land --- which
        substrates, and in what shape --- is :data:`~egpt.memory.tiers.TIER_POLICY`, read by every
        provider rather than decided per adapter.
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

    @abstractmethod
    def tiers(self) -> Dict[MemoryTier, bool]:
        """Which tiers this provider actually serves, every member of the enum present.

        The tier axis's answer to :meth:`substrates`, and it is needed for the same reason: reads
        of an unserved tier are **empty rather than refused**, so without this a caller cannot tell
        an empty tier from an absent one -- and only one of those is worth reporting. A write to an
        unserved tier is refused, so this is also how a caller avoids the refusal.

        Sync-only, like ``substrates``: it reports wiring the provider settled at construction and
        awaits nothing.
        """
        raise NotImplementedError

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        """Release whatever the substrates hold. A default no-op, and safe to call twice.

        Concrete rather than abstract because most providers hold nothing: every substrate behind
        this seam was an in-memory double until SM-G1 (#99) brought one with a connection pool, and
        an abstract method would have made a third-party adapter implement an empty body.

        It is on the **seam** rather than left to ``provider.graph.close()`` because reaching past
        the seam for a backend method is exactly what the seam exists to prevent -- and a caller
        building a provider per request would have no way to know it needed to.
        """

    async def aclose(self) -> None:
        """Async variant of :meth:`close`, for substrates whose driver has an async half."""
