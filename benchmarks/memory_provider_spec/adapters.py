"""Two MemoryProvider adapters that prove the seam is plug-and-play (SM-R11).

Both are dependency-free (stdlib only) and deterministic — no LLM, no network,
no randomness — so the swap test is CI-safe and the proof is real, not asserted.

- ``co_located`` (Mode A): a functional co-located provider that composes two
  in-memory substrate ports (``VectorSubstrate`` + ``GraphSubstrate``), fuses
  their results with RRF, and runs a real episodic->semantic ``consolidate``.
  This stands in for "pgvector + Neo4j in-VPC": swap the two substrate objects
  for SM-R3 / SM-R2 backends and nothing else changes.
- ``external_stub`` (Mode B): a minimal contract-complete stub standing in for an
  external memory service. It drops in via config with NO call-site changes.

Both self-register with the interface registry, so a caller selects one purely by
``MemoryProviderConfig.provider`` (see swap_test.py). Neither imports egpt core.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

from benchmarks.memory_provider_spec.interface import (
    ConsolidationReport,
    DeploymentMode,
    GraphSubstrate,
    MemoryEvent,
    MemoryProvider,
    MemoryProviderConfig,
    MemoryQuery,
    MemoryRecord,
    MemorySlice,
    MemoryTier,
    Scope,
    Substrate,
    VectorSubstrate,
    WriteReceipt,
    register_provider,
)

# Deterministic monotonic clock (Date.now()/random are intentionally avoided so
# runs are reproducible). Each stamp is a strictly increasing integer.
_CLOCK = {"t": 0}


def _tick() -> int:
    _CLOCK["t"] += 1
    return _CLOCK["t"]


def _tokens(text: str) -> set:
    return {w for w in text.lower().replace(".", " ").replace(",", " ").split() if w}


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# =============================================================================
# In-memory substrate ports (stand-ins for pgvector / Neo4j)
# =============================================================================


class DictVectorSubstrate:
    """Neural substrate stand-in: token-Jaccard similarity over stored records.

    Structurally satisfies ``VectorSubstrate``. Swap for an SM-R3 pgvector/Qdrant
    backend without touching the provider.
    """

    def __init__(self) -> None:
        self._by_key: Dict[str, List[MemoryRecord]] = {}

    def _bucket(self, scope: Scope, tier: MemoryTier) -> List[MemoryRecord]:
        return self._by_key.setdefault(scope.key() + "::" + tier.value, [])

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        for record in records:
            bucket = self._bucket(scope, record.tier)
            bucket[:] = [r for r in bucket if r.id != record.id]
            bucket.append(record)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        self.upsert(scope, records)

    def search(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        tiers = [tier] if tier is not None else list(query.tiers or list(MemoryTier))
        scan_all = not _tokens(query.text)  # empty query text = enumerate (scan), no ranking
        scored: List[Tuple[float, MemoryRecord]] = []
        for candidate_tier in tiers:
            for record in self._bucket(scope, candidate_tier):
                sim = 0.0 if scan_all else _jaccard(query.text, record.content)
                if scan_all or sim > 0.0:
                    hit = _copy_with(record, score=sim, substrate=Substrate.VECTOR)
                    scored.append((sim, hit))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [record for _, record in scored[: query.limit]]

    async def asearch(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        return self.search(scope, query, tier)


class DictGraphSubstrate:
    """Symbolic substrate stand-in: topic-overlap traversal + bi-temporal invalidate.

    Structurally satisfies ``GraphSubstrate``. Swap for an SM-R2 Graphiti/Neo4j
    backend without touching the provider. ``invalidate`` sets ``t_invalid`` rather
    than deleting (invalidate-not-delete, SM-R10).
    """

    def __init__(self) -> None:
        self._episodes: Dict[str, List[MemoryRecord]] = {}

    def _bucket(self, scope: Scope) -> List[MemoryRecord]:
        return self._episodes.setdefault(scope.key(), [])

    def upsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        record = _event_to_record(event, substrate=Substrate.GRAPH)
        self._bucket(scope).append(record)

    async def aupsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        self.upsert_episode(scope, event)

    def traverse(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        q_topics = _tokens(query.text)
        scan_all = not q_topics  # empty query text = enumerate (scan), no ranking
        wanted = {tier} if tier is not None else set(query.tiers or list(MemoryTier))
        scored: List[Tuple[int, MemoryRecord]] = []
        for record in self._bucket(scope):
            if record.tier not in wanted:
                continue
            if query.as_of is not None and record.t_invalid is not None and record.t_invalid <= query.as_of:
                continue  # point-in-time read excludes already-invalidated edges
            overlap = (
                0 if scan_all else len(q_topics & set(record.topics or [])) + len(q_topics & _tokens(record.content))
            )
            if scan_all or overlap > 0:
                hit = _copy_with(record, score=float(overlap), substrate=Substrate.GRAPH)
                scored.append((overlap, hit))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [record for _, record in scored[: query.limit]]

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        return self.traverse(scope, query, tier)

    def invalidate(self, scope: Scope, edge_id: str, at: int) -> None:
        for record in self._bucket(scope):
            if record.id == edge_id and record.t_invalid is None:
                record.t_invalid = at

    async def ainvalidate(self, scope: Scope, edge_id: str, at: int) -> None:
        self.invalidate(scope, edge_id, at)


def _copy_with(record: MemoryRecord, score: float, substrate: Optional[Substrate]) -> MemoryRecord:
    return MemoryRecord(
        id=record.id,
        tier=record.tier,
        content=record.content,
        scope=record.scope,
        score=score,
        substrate=substrate,
        topics=record.topics,
        provenance=dict(record.provenance),
        t_valid=record.t_valid,
        t_invalid=record.t_invalid,
        created_at=record.created_at,
        expired_at=record.expired_at,
        superseded_by=record.superseded_by,
        metadata=dict(record.metadata),
    )


def _event_to_record(event: MemoryEvent, substrate: Optional[Substrate]) -> MemoryRecord:
    stamp = _tick()
    occurred = event.occurred_at if event.occurred_at is not None else stamp
    return MemoryRecord(
        id=event.id or "ev-{n}".format(n=stamp),
        tier=event.tier,
        content=event.content,
        scope=event.scope,
        substrate=substrate,
        topics=event.topics,
        provenance={"source": "write", "role": event.role} if event.role else {"source": "write"},
        t_valid=occurred,
        created_at=stamp,
        metadata=dict(event.metadata),
    )


def _min_t_invalid(current: Optional[int], incoming: Optional[int]) -> Optional[int]:
    """Most-restrictive validity wins when the same episode appears in both substrates.

    The graph is authoritative for bi-temporal validity (SM-R10); the vector copy
    may still show ``None``. Merging keeps the earliest non-None ``t_invalid``.
    """
    if current is None:
        return incoming
    if incoming is None:
        return current
    return min(current, incoming)


def _rrf(ranked_lists: Sequence[List[MemoryRecord]], k: int) -> List[MemoryRecord]:
    """Reciprocal Rank Fusion (research doc section 3, k~=60).

    Dedups by episode id across substrates and carries the most-restrictive
    ``t_invalid`` so the provider can apply point-in-time (``as_of``) filtering.
    """
    fused: Dict[str, MemoryRecord] = {}
    scores: Dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, record in enumerate(ranked):
            scores[record.id] = scores.get(record.id, 0.0) + 1.0 / (k + rank + 1)
            if record.id not in fused:
                fused[record.id] = _copy_with(record, score=0.0, substrate=None)
            else:
                fused[record.id].t_invalid = _min_t_invalid(fused[record.id].t_invalid, record.t_invalid)
    for rid, record in fused.items():
        record.score = scores[rid]
    return sorted(fused.values(), key=lambda r: r.score or 0.0, reverse=True)


# =============================================================================
# Adapter #1 (Mode A): co-located, functional
# =============================================================================


class CoLocatedMemoryProvider(MemoryProvider):
    """Functional co-located provider composing two substrate ports."""

    provider_key = "co_located"

    def __init__(
        self,
        vector: VectorSubstrate,
        graph: GraphSubstrate,
        fusion_k: int = 60,
    ) -> None:
        self._vector = vector
        self._graph = graph
        self._fusion_k = fusion_k

    # ---- write --------------------------------------------------------------

    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        receipt = WriteReceipt()
        for event in events:
            record = _event_to_record(event, substrate=Substrate.VECTOR)
            # Same logical episode -> same id in BOTH substrates (so RRF dedups it).
            stamped = replace(event, id=record.id)
            self._vector.upsert(scope, [record])
            self._graph.upsert_episode(scope, stamped)
            receipt.accepted += 1
            receipt.event_ids.append(record.id)
            if event.tier not in receipt.tiers_written:
                receipt.tiers_written.append(event.tier)
        return receipt

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        receipt = WriteReceipt()
        for event in events:
            record = _event_to_record(event, substrate=Substrate.VECTOR)
            stamped = replace(event, id=record.id)
            await self._vector.aupsert(scope, [record])
            await self._graph.aupsert_episode(scope, stamped)
            receipt.accepted += 1
            receipt.event_ids.append(record.id)
            if event.tier not in receipt.tiers_written:
                receipt.tiers_written.append(event.tier)
        return receipt

    # ---- read ---------------------------------------------------------------

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        # Substrates do similarity/traversal; the provider owns temporal validity.
        sub_query = replace(query, as_of=None)
        vector_hits = self._vector.search(scope, sub_query)
        graph_hits = self._graph.traverse(scope, sub_query)
        fused = self._as_of(_rrf([vector_hits, graph_hits], self._fusion_k), query.as_of)[: query.limit]
        return self._slice(scope, query, fused, len(vector_hits), len(graph_hits))

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        sub_query = replace(query, as_of=None)
        vector_hits = await self._vector.asearch(scope, sub_query)
        graph_hits = await self._graph.atraverse(scope, sub_query)
        fused = self._as_of(_rrf([vector_hits, graph_hits], self._fusion_k), query.as_of)[: query.limit]
        return self._slice(scope, query, fused, len(vector_hits), len(graph_hits))

    @staticmethod
    def _as_of(records: List[MemoryRecord], as_of: Optional[int]) -> List[MemoryRecord]:
        """Point-in-time filter: drop episodes invalidated at or before ``as_of`` (SM-R10)."""
        if as_of is None:
            return records
        return [r for r in records if r.t_invalid is None or r.t_invalid > as_of]

    def _slice(
        self,
        scope: Scope,
        query: MemoryQuery,
        fused: List[MemoryRecord],
        n_vec: int,
        n_graph: int,
    ) -> MemorySlice:
        by_tier: Dict[MemoryTier, List[MemoryRecord]] = {}
        for record in fused:
            by_tier.setdefault(record.tier, []).append(record)
        return MemorySlice(
            scope=scope,
            query=query,
            records=fused,
            by_tier=by_tier,
            fusion={"method": "rrf", "k": self._fusion_k, "vector_hits": n_vec, "graph_hits": n_graph},
        )

    # ---- consolidate (episodic -> semantic) ---------------------------------

    def consolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        # Empty query text enumerates all episodic records (scan, not ranked search).
        episodic = self._graph.traverse(scope, MemoryQuery(text="", tiers=[MemoryTier.EPISODIC], limit=10_000))
        report = ConsolidationReport(scope=scope, episodic_scanned=len(episodic))
        seen: Dict[str, MemoryRecord] = {}
        for record in episodic:
            merged = False
            for kept in list(seen.values()):
                if _jaccard(record.content, kept.content) >= 0.6:
                    report.duplicates_merged += 1
                    merged = True
                    break
            if not merged:
                seen[record.id] = record
        for record in seen.values():
            semantic = MemoryRecord(
                id="sem-{n}".format(n=_tick()),
                tier=MemoryTier.SEMANTIC,
                content=record.content,
                scope=scope,
                topics=record.topics,
                provenance={"source": "consolidate", "source_episode_ids": [record.id]},
                t_valid=record.t_valid,
                created_at=_tick(),
            )
            self._vector.upsert(scope, [semantic])
            report.semantic_written += 1
        report.details = {"note": "deterministic dedup+promote; LLM extraction is SM-C* work"}
        return report

    async def aconsolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        return self.consolidate(scope, tiers)

    # ---- tier-aware ---------------------------------------------------------

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        return self._vector.search(scope, query, tier)

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        return await self._vector.asearch(scope, query, tier)

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        retiered = [_retier(event, tier) for event in events]
        return self.write(scope, retiered)

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        retiered = [_retier(event, tier) for event in events]
        return await self.awrite(scope, retiered)

    def substrates(self) -> Dict[Substrate, bool]:
        return {Substrate.VECTOR: True, Substrate.GRAPH: True}


def _retier(event: MemoryEvent, tier: MemoryTier) -> MemoryEvent:
    return MemoryEvent(
        content=event.content,
        scope=event.scope,
        tier=tier,
        role=event.role,
        topics=event.topics,
        occurred_at=event.occurred_at,
        metadata=dict(event.metadata),
    )


# =============================================================================
# Adapter #2 (Mode B): external service stub — contract-complete, minimal
# =============================================================================


@dataclass
class ExternalStubMemoryProvider(MemoryProvider):
    """Minimal stand-in for an external memory service (Mode B).

    Contract-complete so it drops in behind the seam with NO call-site changes.
    Vector-only (no graph substrate wired) to also exercise ``substrates()``
    reporting a partially-wired provider.
    """

    provider_key: str = "external_stub"
    _store: List[MemoryRecord] = field(default_factory=list)

    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        receipt = WriteReceipt()
        for event in events:
            record = _event_to_record(event, substrate=Substrate.VECTOR)
            record.provenance["backend"] = "external"
            self._store.append(record)
            receipt.accepted += 1
            receipt.event_ids.append(record.id)
            if event.tier not in receipt.tiers_written:
                receipt.tiers_written.append(event.tier)
        return receipt

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return self.write(scope, events)

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        tiers = set(query.tiers or list(MemoryTier))
        hits = [
            _copy_with(r, score=_jaccard(query.text, r.content), substrate=Substrate.VECTOR)
            for r in self._store
            if r.scope.tenant_id == scope.tenant_id and r.tier in tiers and _jaccard(query.text, r.content) > 0.0
        ]
        hits.sort(key=lambda r: r.score or 0.0, reverse=True)
        hits = hits[: query.limit]
        by_tier: Dict[MemoryTier, List[MemoryRecord]] = {}
        for record in hits:
            by_tier.setdefault(record.tier, []).append(record)
        return MemorySlice(
            scope=scope,
            query=query,
            records=hits,
            by_tier=by_tier,
            fusion={"method": "external", "backend": "stub"},
        )

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        return self.read(scope, query)

    def consolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        episodic = [r for r in self._store if r.tier is MemoryTier.EPISODIC and r.scope.tenant_id == scope.tenant_id]
        return ConsolidationReport(
            scope=scope,
            episodic_scanned=len(episodic),
            details={"note": "external service performs consolidation asynchronously"},
        )

    async def aconsolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        return self.consolidate(scope, tiers)

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        return self.read(scope, MemoryQuery(text=query.text, tiers=[tier], limit=query.limit)).records

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        return self.read_tier(scope, tier, query)

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return self.write(scope, [_retier(event, tier) for event in events])

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return self.write_tier(scope, tier, events)

    def substrates(self) -> Dict[Substrate, bool]:
        return {Substrate.VECTOR: True, Substrate.GRAPH: False}


# =============================================================================
# Registration: adapters become selectable purely by config
# =============================================================================


@register_provider("co_located")
def _build_co_located(config: MemoryProviderConfig) -> MemoryProvider:
    fusion_k = int(config.fusion.get("k", 60))
    return CoLocatedMemoryProvider(
        vector=DictVectorSubstrate(),
        graph=DictGraphSubstrate(),
        fusion_k=fusion_k,
    )


@register_provider("external_stub")
def _build_external_stub(config: MemoryProviderConfig) -> MemoryProvider:
    assert config.mode in (DeploymentMode.EXTERNAL, DeploymentMode.CO_LOCATED)
    return ExternalStubMemoryProvider()
