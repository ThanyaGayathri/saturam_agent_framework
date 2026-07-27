"""MemoryProvider interface (SM-R11).

Runnable, dependency-free contract for the neuro-symbolic memory seam. It is the
implementable artifact SM-C1 builds from: exact method signatures, return types,
scoping, tier/substrate model, and the config seam that makes Mode A vs Mode B a
configuration change with no call-site edits.

Design decisions (provisional pending SM-R1 #76 ADR ratification):

- Single ``MemoryProvider`` seam over BOTH substrates (issue #86 recommended
  option), composed internally from two substrate ports (``VectorSubstrate`` +
  ``GraphSubstrate``). Callers depend only on the one seam; substrates swap
  independently behind it.
- WRAP / extend the seed's existing abstractions rather than replace them:
  a ``MemoryProvider`` tier may be backed by a seed ``LearningStore``
  (``learn/stores/protocol.py:16``) or ``MemoryManager``
  (``memory/manager.py:46``). Aligns with SM-R1 #76 Decision 2 (reuse the
  ``learn`` store/tier abstraction, add substrates on top).
- Async is first-class: every public method has a sync and an ``a``-prefixed
  async variant, matching the ``learn``/``memory`` convention
  (``arecall``/``aprocess``/``aread_from_db``). NOTE: ``vectordb/base.py`` uses
  the ``async_`` prefix instead; the provider follows the ``learn`` convention
  because it extends that seam.

Layering vs SM-R12 (#87): SM-R12 owns the persisted per-tier ROW schemas
(``EpisodicRecord``, ``SemanticFact``/``SemanticEntity``/``SemanticRelation``,
``ProceduralRecord``, ``WorkingMemory``, ``MemoryLink`` on a
``TenantScoped``+``BiTemporal``+``Embeddable``+``Decaying`` spine). This spec owns
the SEAM ENVELOPE (``MemorySlice``/``MemoryQuery``/``MemoryEvent``/``WriteReceipt``/
``Scope``), which R12 does not define. ``MemoryRecord`` here is the seam's uniform
read-projection over R12's row types; its field names + int epoch-seconds are
aligned to SM-R12's spine (bi-temporal names are SM-R10 canonical) so SM-C1 maps
1:1. Provisional pending SM-R1 #76; final field reconciliation lands with SM-C1.

Grounded in docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, runtime_checkable

try:  # Protocol is stdlib on 3.8+; guard only for very old typing backports.
    from typing import Protocol
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol  # type: ignore


# =============================================================================
# Enums: tiers, substrates, deployment mode
# =============================================================================


class MemoryTier(str, Enum):
    """CoALA memory tiers (research doc section 1, arXiv:2309.02427).

    Tier -> substrate mapping (SM-R1 #76 diagram, provisional):
      WORKING    -> in-context only (no persistent substrate)
      EPISODIC   -> vector + graph (raw, timestamped, non-lossy)
      SEMANTIC   -> vector + graph (facts / entities / relations)
      PROCEDURAL -> rules / prompt fragments (optional weights lane)
    """

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class Substrate(str, Enum):
    """The two co-equal neuro-symbolic substrates (research doc, DECISION locked)."""

    VECTOR = "vector"  # pgvector-style similarity + episodic recall entry
    GRAPH = "graph"  # temporal knowledge graph: multi-hop, provenance, bi-temporal


class DeploymentMode(str, Enum):
    """Tenant-configurable placement of the substrates behind the seam.

    Switching modes is configuration, not a rewrite: both modes satisfy the same
    ``MemoryProvider`` contract; only the concrete substrate adapters differ.
    """

    CO_LOCATED = "A"  # Mode A: in-VPC pgvector + Neo4j
    EXTERNAL = "B"  # Mode B: external vector + graph services behind the seam


# =============================================================================
# Scoping: tenant + agent thread through EVERY call
# =============================================================================


@dataclass(frozen=True)
class Scope:
    """Isolation + addressing for every read/write/consolidate call.

    ``tenant_id`` is mandatory (multi-tenant isolation, research doc section 3:
    ``tenant_id`` + row-level security). ``agent_id``/``user_id``/``session_id``
    narrow the scope; ``user_id`` is the identity namespace that survives
    sessions (research doc section 3).
    """

    tenant_id: str
    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    session_id: Optional[str] = None

    def key(self) -> str:
        """Stable string key for storage partitioning / logging."""
        return "/".join(
            [
                self.tenant_id,
                self.agent_id or "*",
                self.user_id or "*",
                self.team_id or "*",
                self.session_id or "*",
            ]
        )


# =============================================================================
# Seam envelope + read-projection (row schemas owned by SM-R12 #87)
# =============================================================================


@dataclass
class MemoryRecord:
    """The seam's uniform read-projection of a stored memory item.

    This is NOT a new schema: it is the flattened view the seam returns, over
    SM-R12's authoritative per-tier row types (``EpisodicRecord``,
    ``SemanticFact``/``SemanticEntity``/``SemanticRelation``, ``ProceduralRecord``,
    ``MemoryLink``). Field names/types track SM-R12 exactly so SM-C1 can map 1:1:
    ints are Unix epoch **seconds**; bi-temporal names are SM-R10 canonical
    (``t_valid``/``t_invalid``/``created_at``/``expired_at``/``superseded_by``).
    """

    id: str
    tier: MemoryTier
    content: str
    scope: Scope
    score: Optional[float] = None  # fused (RRF) or substrate-native score
    substrate: Optional[Substrate] = None  # origin; None once fused across both
    topics: Optional[List[str]] = None
    provenance: Dict[str, Any] = field(default_factory=dict)  # source_episode_ids, edge ids
    # Bi-temporal (SM-R10, names per SM-R12 spine): valid time = when true in the
    # world (t_valid..t_invalid); created_at = transaction time IN; expired_at =
    # transaction time OUT (row superseded); superseded_by = replacement id.
    # Invalidate-not-delete: a contradiction sets t_invalid, never deletes.
    t_valid: Optional[int] = None
    t_invalid: Optional[int] = None
    created_at: Optional[int] = None
    expired_at: Optional[int] = None
    superseded_by: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryEvent:
    """A write-back unit (an experience/observation to remember).

    Writes land in EPISODIC by default (raw, non-lossy); consolidation later
    distills them into SEMANTIC. ``occurred_at`` is valid time (SM-R10).
    """

    content: str
    scope: Scope
    tier: MemoryTier = MemoryTier.EPISODIC
    id: Optional[str] = None  # optional idempotency key; shared across substrates when set
    role: Optional[str] = None  # for message-shaped events (user/assistant/...)
    topics: Optional[List[str]] = None
    occurred_at: Optional[int] = None  # WHEN it happened (epoch s) -> record t_valid; stamped if None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryQuery:
    """A read request. ``tiers`` is the issue's ``types`` param."""

    text: str
    tiers: Optional[Sequence[MemoryTier]] = None  # None => all persistent tiers
    substrates: Optional[Sequence[Substrate]] = None  # None => provider default (both)
    limit: int = 10
    as_of: Optional[int] = None  # point-in-time read (epoch s; bi-temporal, SM-R10)
    filters: Optional[Dict[str, Any]] = None


@dataclass
class MemorySlice:
    """The feed-forward result of ``read`` — the memory handed to the agent.

    ``records`` is the fused, ranked list; ``by_tier`` groups them; ``fusion``
    records how retrieval was combined (SM-R8 diagnostics).
    """

    scope: Scope
    query: Optional[MemoryQuery] = None
    records: List[MemoryRecord] = field(default_factory=list)
    by_tier: Dict[MemoryTier, List[MemoryRecord]] = field(default_factory=dict)
    fusion: Dict[str, Any] = field(default_factory=dict)  # {"method": "rrf", "k": 60, ...}

    def as_context(self) -> str:
        """Render the slice as a prompt-injectable context string.

        Parallels ``LearningStore.build_context`` so a slice drops into the same
        place in an agent prompt.
        """
        if not self.records:
            return ""
        lines: List[str] = []
        for record in self.records:
            prefix = record.tier.value
            lines.append("[{tier}] {content}".format(tier=prefix, content=record.content))
        return "\n".join(lines)


@dataclass
class WriteReceipt:
    """Result of a ``write`` — what was accepted and where it landed."""

    accepted: int = 0
    event_ids: List[str] = field(default_factory=list)
    tiers_written: List[MemoryTier] = field(default_factory=list)


@dataclass
class ConsolidationReport:
    """Result of an (async) ``consolidate`` pass (episodic -> semantic).

    The write-side consolidation loop is the "Symbiotic Memory" differentiator
    (research doc section 5.3): extract -> dedup -> contradiction-resolve ->
    write to BOTH substrates, on the sleep-time lane.
    """

    scope: Scope
    episodic_scanned: int = 0
    semantic_written: int = 0
    duplicates_merged: int = 0
    contradictions_resolved: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Substrate ports (sub-interfaces behind the single seam)
# =============================================================================
#
# The single MemoryProvider seam composes these two ports. Backends (SM-R3
# vector store, SM-R2/SM-G1 graph engine) are selected per-substrate via config,
# so a caller never imports a concrete backend. Both are Protocols (structural)
# so an existing class — e.g. a `VectorDb` subclass — can satisfy VectorSubstrate
# via a thin adapter without inheritance.


@runtime_checkable
class VectorSubstrate(Protocol):
    """Neural substrate: embedding similarity + full-text, per tenant/tier."""

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None: ...

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None: ...

    def search(
        self,
        scope: Scope,
        query: MemoryQuery,
        tier: Optional[MemoryTier] = None,
    ) -> List[MemoryRecord]: ...

    async def asearch(
        self,
        scope: Scope,
        query: MemoryQuery,
        tier: Optional[MemoryTier] = None,
    ) -> List[MemoryRecord]: ...


@runtime_checkable
class GraphSubstrate(Protocol):
    """Symbolic substrate: temporal knowledge graph (bi-temporal, multi-hop).

    ``invalidate`` implements invalidate-not-delete (SM-R10): a contradiction
    sets ``t_invalid`` on the old edge rather than deleting it, preserving
    point-in-time reconstruction + provenance.
    """

    def upsert_episode(self, scope: Scope, event: MemoryEvent) -> None: ...

    async def aupsert_episode(self, scope: Scope, event: MemoryEvent) -> None: ...

    def traverse(
        self,
        scope: Scope,
        query: MemoryQuery,
        tier: Optional[MemoryTier] = None,
    ) -> List[MemoryRecord]: ...

    async def atraverse(
        self,
        scope: Scope,
        query: MemoryQuery,
        tier: Optional[MemoryTier] = None,
    ) -> List[MemoryRecord]: ...

    def invalidate(self, scope: Scope, edge_id: str, at: int) -> None: ...

    async def ainvalidate(self, scope: Scope, edge_id: str, at: int) -> None: ...


# =============================================================================
# The MemoryProvider seam
# =============================================================================


class MemoryProvider(ABC):
    """The single memory seam SM-C1 implements and every consumer depends on.

    Three core verbs (issue #86) — ``read`` (feed-forward), ``write``
    (write-back), ``consolidate`` (episodic -> semantic, async-first) — plus
    tier-aware single-tier variants. Every method takes ``scope`` (tenant +
    agent thread through) and has a sync and an async variant.

    Concrete adapters (Mode A co-located, Mode B external, stubs) subclass this.
    Consumers NEVER instantiate an adapter directly; they call
    ``build_memory_provider(config)`` so the backend is a config choice.
    """

    #: Registry key; concrete adapters set this so config can select them.
    provider_key: str = "abstract"

    # ---- read (feed-forward): query -> MemorySlice --------------------------

    @abstractmethod
    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        """Hybrid neuro-symbolic retrieval fused into one ranked slice.

        Fuses vector + graph (+ full-text) across the requested ``query.tiers``
        using RRF (k~=60), optional rerank / PPR (SM-R8). No query-time LLM on
        the hot path (research doc section 5.5).
        """
        raise NotImplementedError

    @abstractmethod
    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        """Async variant of :meth:`read`."""
        raise NotImplementedError

    # ---- write (write-back): append events ----------------------------------

    @abstractmethod
    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Append raw events (default EPISODIC). Cheap, non-lossy; no distillation.

        Distillation into SEMANTIC happens later via :meth:`consolidate`.
        """
        raise NotImplementedError

    @abstractmethod
    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Async variant of :meth:`write`."""
        raise NotImplementedError

    # ---- consolidate (episodic -> semantic), async-first --------------------

    @abstractmethod
    def consolidate(
        self,
        scope: Scope,
        tiers: Optional[Sequence[MemoryTier]] = None,
    ) -> ConsolidationReport:
        """Sleep-time pass: extract -> dedup -> contradiction-resolve, writing to
        BOTH substrates. Intended for the async/background lane; the sync variant
        exists for tests and manual triggers.
        """
        raise NotImplementedError

    @abstractmethod
    async def aconsolidate(
        self,
        scope: Scope,
        tiers: Optional[Sequence[MemoryTier]] = None,
    ) -> ConsolidationReport:
        """Async variant of :meth:`consolidate` (the primary entry point)."""
        raise NotImplementedError

    # ---- tier-aware single-tier read/write ----------------------------------

    @abstractmethod
    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        """Read a single tier directly (no cross-tier fusion)."""
        raise NotImplementedError

    @abstractmethod
    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        """Async variant of :meth:`read_tier`."""
        raise NotImplementedError

    @abstractmethod
    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Write events to a specific tier (overriding each event's default)."""
        raise NotImplementedError

    @abstractmethod
    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        """Async variant of :meth:`write_tier`."""
        raise NotImplementedError

    # ---- introspection ------------------------------------------------------

    @abstractmethod
    def substrates(self) -> Dict[Substrate, bool]:
        """Which substrates this provider has wired (for diagnostics/health)."""
        raise NotImplementedError


# =============================================================================
# Config seam + provider registry (this is the swap-proof)
# =============================================================================


@dataclass
class MemoryProviderConfig:
    """Everything needed to build a provider WITHOUT touching call sites.

    ``provider`` selects the adapter from the registry; ``mode`` records Mode A
    vs Mode B; ``vector``/``graph`` are opaque backend settings (dsn, dim, uri);
    ``fusion`` carries SM-R8 knobs (rrf k, rerank, ppr). Swapping backend or mode
    is editing this object (or its source config file), never the consumer.
    """

    provider: str
    mode: DeploymentMode = DeploymentMode.CO_LOCATED
    vector: Dict[str, Any] = field(default_factory=dict)
    graph: Dict[str, Any] = field(default_factory=dict)
    fusion: Dict[str, Any] = field(default_factory=lambda: {"method": "rrf", "k": 60})
    options: Dict[str, Any] = field(default_factory=dict)


# Adapter factory: takes a config, returns a ready MemoryProvider.
ProviderFactory = Callable[[MemoryProviderConfig], MemoryProvider]

_REGISTRY: Dict[str, ProviderFactory] = {}


def register_provider(key: str) -> Callable[[ProviderFactory], ProviderFactory]:
    """Decorator to register an adapter factory under ``key``.

    Adapters self-register on import; a consumer selects one by string in
    ``MemoryProviderConfig.provider``. This is what makes the swap config-only.
    """

    def _decorator(factory: ProviderFactory) -> ProviderFactory:
        if key in _REGISTRY:
            raise ValueError("MemoryProvider '{key}' already registered".format(key=key))
        _REGISTRY[key] = factory
        return factory

    return _decorator


def build_memory_provider(config: MemoryProviderConfig) -> MemoryProvider:
    """Resolve ``config.provider`` to an adapter and build it.

    The ONE call sites use. Changing ``config.provider`` (or ``config.mode``)
    swaps the concrete implementation with no other edits.
    """
    if config.provider not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "<none registered>"
        raise KeyError(
            "Unknown MemoryProvider '{provider}'. Registered: {known}".format(provider=config.provider, known=known)
        )
    return _REGISTRY[config.provider](config)


def registered_providers() -> List[str]:
    """Sorted list of registered provider keys (for diagnostics/tests)."""
    return sorted(_REGISTRY)


if __name__ == "__main__":
    # Dependency-free smoke summary of the seam (no backends required).
    print("MemoryProvider seam (SM-R11)")
    print("  tiers      :", ", ".join(t.value for t in MemoryTier))
    print("  substrates :", ", ".join(s.value for s in Substrate))
    print("  modes      :", ", ".join("{n}={v}".format(n=m.name, v=m.value) for m in DeploymentMode))
    core = ["read", "write", "consolidate", "read_tier", "write_tier"]
    print("  methods    :", ", ".join("{m}/a{m}".format(m=m) for m in core))
    print("  registered :", registered_providers() or "<none — import an adapter module>")
