"""The co-located memory provider: both substrates behind one seam (SM-C1).

Mode A wiring -- vector and graph living in the same VPC. It is the reference composition: it owns
fusion, write fan-out, point-in-time filtering, and the consolidation skeleton, and it holds no
backend code at all. Mode B (SM-X2 #138) differs only in which substrate instances it is handed.

Four contract properties are implemented here rather than left to backends, because each one was a
real bug in the SM-R11 prototype and each is invisible until it is wrong:

* **One id per logical episode** across both substrates (finding F1). The provider stamps it.
* **Point-in-time applies to the fused set** (finding F2). The vector substrate has no notion of
  validity, so a per-leg filter lets an invalidated fact return through the other leg.
* **Both legs resolve tiers identically** (finding F3). The provider passes the resolved set down.
* **One write model** for bi-temporal contradiction (ADR 0003 section B.6): consolidation routes
  through the same SCD-2 primitive as ``invalidate``, never a second in-place path.
"""

from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Set, Tuple
from uuid import uuid4

from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError, register_provider
from egpt.memory.fusion import fuse_rrf, fuse_weighted
from egpt.memory.provider import MemoryProvider
from egpt.memory.substrates import GraphSubstrate, VectorSubstrate
from egpt.memory.tiers import MemoryTier, substrates_for_tier
from egpt.memory.types import (
    ConsolidationReport,
    FactWrite,
    MemoryEvent,
    MemoryQuery,
    MemoryRecord,
    MemorySlice,
    Scope,
    Substrate,
    WriteReceipt,
)
from egpt.utils.dttm import now_epoch_s

__all__ = ["CoLocatedMemoryProvider"]

#: Jaccard threshold above which two episodes are treated as the same thing during consolidation.
#:
#: Deterministic and deliberately crude. Real extraction and merge -- the mem0-style
#: ADD/UPDATE/DELETE/NOOP backbone SM-R7 ratified -- is SM-L3 (#124) and SM-L4 (#125). This exists
#: so ``consolidate`` is a working, testable pass rather than a stub, not because token overlap is
#: the right similarity measure.
_DUPLICATE_THRESHOLD = 0.8


def _tokens(text: str) -> Set[str]:
    return set(text.lower().split())


def _jaccard(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _valid_at(record: MemoryRecord, at: int) -> bool:
    """Whether ``record`` was true at ``at`` -- **two-sided**, per SM-R10 section 7.

    ``t_valid <= at < t_invalid``, with ``None`` meaning open at that end. Both bounds are
    required: checking only ``t_invalid`` lets a **future-dated** record answer a question about
    the past (finding F6).
    """
    if record.t_valid is not None and record.t_valid > at:
        return False
    if record.t_invalid is not None and record.t_invalid <= at:
        return False
    return True


class CoLocatedMemoryProvider(MemoryProvider):
    """A provider over co-located vector and graph substrates.

    Either substrate may be absent -- a vector-only tenant is a supported configuration (SM-C5
    #93), and ``substrates()`` reports which are wired so a caller can tell "the graph found
    nothing" from "there is no graph". Both absent is a misconfiguration and fails at construction.
    """

    provider_key = "co_located"

    def __init__(
        self,
        vector: Optional[VectorSubstrate] = None,
        graph: Optional[GraphSubstrate] = None,
        config: Optional[MemoryProviderConfig] = None,
    ) -> None:
        if vector is None and graph is None:
            raise MemoryProviderConfigError(
                "CoLocatedMemoryProvider needs at least one substrate; both vector and graph are unset.",
                field_name="provider",
            )
        self.vector = vector
        self.graph = graph
        self.config = config or MemoryProviderConfig(provider=self.provider_key)

    # -- introspection --------------------------------------------------------

    def substrates(self) -> Dict[Substrate, bool]:
        return {Substrate.VECTOR: self.vector is not None, Substrate.GRAPH: self.graph is not None}

    @staticmethod
    def _requested(query: MemoryQuery, substrate: Substrate) -> bool:
        """Whether the query allows this substrate. ``None`` means "whatever is wired".

        Deliberately does not also check wiring: the caller tests ``is not None`` inline so the
        Optional narrows, which keeps the read path honest for a type checker rather than relying
        on a helper's return value to imply an invariant it cannot express.
        """
        return query.substrates is None or substrate in query.substrates

    # -- write ----------------------------------------------------------------

    def _prepare(
        self, scope: Scope, events: Sequence[MemoryEvent], tier: Optional[MemoryTier]
    ) -> Tuple[
        List[MemoryEvent],
        List[MemoryRecord],
    ]:
        """Stamp ids and times, and project each event into the record the vector leg stores.

        The id is stamped **once** and reused across substrates: it is the dedup key during fusion,
        and minting a second one downstream turns one episode into two fused results (finding F1).
        """
        now = now_epoch_s()
        stamped: List[MemoryEvent] = []
        records: List[MemoryRecord] = []

        for event in events:
            target = tier or event.tier
            if not substrates_for_tier(target):
                raise MemoryProviderConfigError(
                    f"Tier {target.value!r} has no persistent substrate, so it cannot be written through "
                    "the memory seam. The working buffer is SM-C6's (#94).",
                    field_name="tier",
                )
            occurred_at = event.occurred_at if event.occurred_at is not None else now
            prepared = replace(event, id=event.id or str(uuid4()), tier=target, occurred_at=occurred_at)
            stamped.append(prepared)
            records.append(
                MemoryRecord(
                    id=prepared.id,  # type: ignore[arg-type]  # stamped just above
                    tier=target,
                    content=prepared.content,
                    scope=scope,
                    topics=prepared.topics,
                    t_valid=occurred_at,
                    created_at=now,
                    metadata=dict(prepared.metadata),
                )
            )
        return stamped, records

    @staticmethod
    def _receipt(stamped: Sequence[MemoryEvent]) -> WriteReceipt:
        tiers: List[MemoryTier] = []
        for event in stamped:
            if event.tier not in tiers:
                tiers.append(event.tier)
        return WriteReceipt(
            accepted=len(stamped),
            event_ids=[e.id for e in stamped if e.id is not None],
            tiers_written=tiers,
        )

    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        stamped, records = self._prepare(scope, events, tier=None)
        if self.vector is not None and records:
            self.vector.upsert(scope, records)
        if self.graph is not None:
            for event in stamped:
                if "graph" in substrates_for_tier(event.tier):
                    self.graph.upsert_episode(scope, event)
        return self._receipt(stamped)

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        stamped, records = self._prepare(scope, events, tier=None)
        if self.vector is not None and records:
            await self.vector.aupsert(scope, records)
        if self.graph is not None:
            for event in stamped:
                if "graph" in substrates_for_tier(event.tier):
                    await self.graph.aupsert_episode(scope, event)
        return self._receipt(stamped)

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return self.write(scope, [replace(event, tier=tier) for event in events])

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return await self.awrite(scope, [replace(event, tier=tier) for event in events])

    # -- read -----------------------------------------------------------------

    @staticmethod
    def _without_as_of(query: MemoryQuery) -> MemoryQuery:
        """Strip point-in-time before it reaches a substrate.

        The ports say a backend must not apply ``as_of``, but stripping it is cheap insurance: a
        backend that filtered anyway would produce a subtly narrow result the fused filter could
        never widen back, and the symptom would look like a ranking problem.
        """
        return query if query.as_of is None else replace(query, as_of=None)

    def _fuse(self, legs: Sequence[Sequence[MemoryRecord]]) -> List[MemoryRecord]:
        fusion = self.config.fusion
        if fusion.get("method") == "rrf":
            return fuse_rrf(legs, k=int(fusion.get("k", 60)))
        vector_weight = float(fusion.get("vector_weight", 0.5))
        return fuse_weighted(legs, weights=[vector_weight, 1.0 - vector_weight])

    def _assemble(
        self,
        scope: Scope,
        query: MemoryQuery,
        vector_hits: List[MemoryRecord],
        graph_hits: List[MemoryRecord],
    ) -> MemorySlice:
        """Fuse, apply validity, cap, and group -- the order matters and is the point of F2."""
        fused = self._fuse([vector_hits, graph_hits])

        # Current belief always; point-in-time only when asked. Both are applied here, after
        # fusion, because the graph is the authority for validity and the vector leg is a
        # projection that would otherwise readmit what the graph invalidated (finding F2).
        visible = [record for record in fused if record.is_current_belief()]
        if query.as_of is not None:
            visible = [record for record in visible if _valid_at(record, query.as_of)]

        records = visible[: query.limit]

        by_tier: Dict[MemoryTier, List[MemoryRecord]] = {}
        for record in records:
            by_tier.setdefault(record.tier, []).append(record)

        return MemorySlice(
            scope=scope,
            query=query,
            records=records,
            by_tier=by_tier,
            fusion={
                "method": self.config.fusion.get("method"),
                "vector_weight": self.config.fusion.get("vector_weight"),
                "vector_hits": len(vector_hits),
                "graph_hits": len(graph_hits),
                "fused": len(fused),
                "returned": len(records),
            },
        )

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        probe = self._without_as_of(query)
        vector_hits: List[MemoryRecord] = []
        graph_hits: List[MemoryRecord] = []

        if self.vector is not None and self._requested(query, Substrate.VECTOR):
            vector_hits = list(self.vector.search(scope, probe))
        if self.graph is not None and self._requested(query, Substrate.GRAPH):
            graph_hits = list(self.graph.traverse(scope, probe))
        return self._assemble(scope, query, vector_hits, graph_hits)

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        probe = self._without_as_of(query)
        vector_hits: List[MemoryRecord] = []
        graph_hits: List[MemoryRecord] = []

        if self.vector is not None and self._requested(query, Substrate.VECTOR):
            vector_hits = list(await self.vector.asearch(scope, probe))
        if self.graph is not None and self._requested(query, Substrate.GRAPH):
            graph_hits = list(await self.graph.atraverse(scope, probe))
        return self._assemble(scope, query, vector_hits, graph_hits)

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        return self.read(scope, replace(query, tiers=[tier])).records

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        slice_ = await self.aread(scope, replace(query, tiers=[tier]))
        return slice_.records

    # -- consolidate ----------------------------------------------------------

    def _distil(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> Tuple[List[FactWrite], int]:
        """Group near-duplicate episodes and emit one semantic fact per group.

        Deterministic on purpose: same input, same output, no model call, so the pass is testable
        and the seam is exercised end to end. This is **not** the consolidation strategy -- SM-R7
        ratified mem0-style extract-and-merge as the backbone, which is SM-L3/SM-L4's to build.
        What matters here is the *shape*: provenance is carried, and the fact is written to both
        substrates through the fact path rather than smuggled in as a pseudo-episode (finding F9).
        """
        now = now_epoch_s()
        groups: List[List[MemoryRecord]] = []
        for episode in episodes:
            for group in groups:
                if _jaccard(group[0].content, episode.content) >= _DUPLICATE_THRESHOLD:
                    group.append(episode)
                    break
            else:
                groups.append([episode])

        facts: List[FactWrite] = []
        merged = 0
        for group in groups:
            merged += len(group) - 1
            head = group[0]
            source_ids = [member.id for member in group]
            facts.append(
                FactWrite(
                    record=MemoryRecord(
                        id=str(uuid4()),
                        tier=MemoryTier.SEMANTIC,
                        content=head.content,
                        scope=scope,
                        topics=head.topics,
                        provenance={"source_episode_ids": source_ids},
                        t_valid=head.t_valid,
                        created_at=now,
                    ),
                    source_episode_ids=source_ids,
                    derived_by="co_located.deterministic_dedup",
                )
            )
        return facts, merged

    def _report(self, scope: Scope, scanned: int, facts: Sequence[FactWrite], merged: int) -> ConsolidationReport:
        return ConsolidationReport(
            scope=scope,
            episodic_scanned=scanned,
            semantic_written=len(facts),
            duplicates_merged=merged,
            # Stays zero by design. Contradiction resolution needs extraction to know two facts
            # disagree, which is SM-L5's (#126). When it lands it routes through the graph's
            # SCD-2 invalidate -- one write model, never a second in-place path.
            contradictions_resolved=0,
            details={"derived_by": "co_located.deterministic_dedup"},
        )

    def consolidate(
        self,
        scope: Scope,
        tiers: Optional[Sequence[MemoryTier]] = None,
    ) -> ConsolidationReport:
        if self.vector is None:
            raise MemoryProviderConfigError(
                "consolidate needs the vector substrate to enumerate episodes; none is wired.",
                field_name="provider",
            )
        episodes = list(self.vector.scan(scope, MemoryTier.EPISODIC))
        facts, merged = self._distil(scope, episodes)

        for fact in facts:
            self.vector.upsert(scope, [fact.record])
            if self.graph is not None:
                self.graph.upsert_fact(scope, fact)
        return self._report(scope, len(episodes), facts, merged)

    async def aconsolidate(
        self,
        scope: Scope,
        tiers: Optional[Sequence[MemoryTier]] = None,
    ) -> ConsolidationReport:
        if self.vector is None:
            raise MemoryProviderConfigError(
                "consolidate needs the vector substrate to enumerate episodes; none is wired.",
                field_name="provider",
            )
        episodes = list(await self.vector.ascan(scope, MemoryTier.EPISODIC))
        facts, merged = self._distil(scope, episodes)

        for fact in facts:
            await self.vector.aupsert(scope, [fact.record])
            if self.graph is not None:
                await self.graph.aupsert_fact(scope, fact)
        return self._report(scope, len(episodes), facts, merged)


@register_provider("co_located")
def _build_co_located(config: MemoryProviderConfig) -> MemoryProvider:
    """Build the co-located provider from config.

    Substrates arrive through ``config.options`` for now. That is the **pre-backend** path: no
    vector or graph backend is registered yet (SM-V1 #95, SM-G1 #99), so there is nothing for a
    factory to resolve, and inventing a config shape for backends that do not exist would fix the
    wrong thing. When they land, ``config.vector["backend"]`` and ``config.graph["backend"]``
    resolve through ``egpt.registry.discovery.construct`` and this stays the only call site.
    """
    return CoLocatedMemoryProvider(
        vector=config.options.get("vector_substrate"),
        graph=config.options.get("graph_substrate"),
        config=config,
    )
