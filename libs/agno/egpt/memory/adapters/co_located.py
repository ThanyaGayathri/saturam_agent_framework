"""The co-located memory provider: both substrates behind one seam (SM-C1).

Mode A wiring -- vector and graph living in the same VPC. It is the reference composition: it owns
fusion, write fan-out, point-in-time filtering, and the consolidation skeleton, and it holds no
backend code at all. Mode B (SM-X2 #138) differs only in which substrate instances it is handed.

Four contract properties are implemented here rather than left to backends, because each one was a
real bug in the SM-R11 prototype and each is invisible until it is wrong:

* **One id per logical episode** across both substrates (finding F1). The provider stamps it.
* **Both legs resolve tiers identically** (finding F3). The provider passes the resolved set down.
* **One write model** for bi-temporal contradiction (ADR 0003 section B.6): consolidation routes
  through the same SCD-2 primitive as ``invalidate``, never a second in-place path.
* **Two post-conditions the provider refuses to take on faith** -- tenant isolation and validity.
  Both are port requirements; both are re-checked here because a backend that drops either fails
  silently, and one of them puts another tenant's data into a prompt.

Validity filtering is the substrates' job, not this provider's. An earlier version filtered after
fusion and then grew an over-fetch ladder to compensate for the rows that filter discarded, which
was a mitigation that no fixed multiplier can make correct. SM-R12's row schemas are ``BiTemporal``,
so a real backend has the columns to filter before ranking -- which is where a ``WHERE`` clause
belongs.
"""

import asyncio
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Set, Tuple
from uuid import NAMESPACE_OID, uuid4, uuid5

from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError, register_provider
from egpt.memory.fusion import fuse_rrf, fuse_weighted
from egpt.memory.provider import MemoryProvider
from egpt.memory.substrates import GraphSubstrate, VectorSubstrate, checked_graph, checked_vector
from egpt.memory.tiers import MemoryTier, substrates_for_tier
from egpt.memory.types import (
    ConsolidationReport,
    FactWrite,
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
from egpt.utils.dttm import now_epoch_s

__all__ = ["CoLocatedMemoryProvider", "PartialWriteError"]


#: Jaccard threshold above which two episodes are treated as the same thing during consolidation.
#:
#: Deterministic and deliberately crude. Real extraction and merge -- the mem0-style
#: ADD/UPDATE/DELETE/NOOP backbone SM-R7 ratified -- is SM-L3 (#124) and SM-L4 (#125). This exists
#: so ``consolidate`` is a working, testable pass rather than a stub, not because token overlap is
#: the right similarity measure.
_DUPLICATE_THRESHOLD = 0.8

#: Namespace for deriving a stable semantic-fact id from its source episodes, so re-running
#: ``consolidate`` upserts the same row instead of minting a new one each pass.
_FACT_NAMESPACE = uuid5(NAMESPACE_OID, "egpt.memory.consolidate.fact")

#: How many episodes one consolidation pass scans.
#:
#: Bounded because ``_distil`` is O(n^2) in the batch -- every episode is compared against every
#: group head -- and an unbounded scan materialises a tenant's entire episodic history into a list
#: first. ``scan`` grew ``since`` and ``limit`` for exactly this (deviation D4), and the only caller
#: was passing neither.
#:
#: The loop over batches is internal, so the bound is a memory ceiling rather than a protocol the
#: caller has to drive correctly.
_CONSOLIDATE_BATCH = 500

#: Runaway guard on that internal loop. A conforming ``scan`` always advances, so reaching this
#: means the substrate is not honouring the documented ordering -- terminating is better than
#: spinning, and the batch count in the report shows how far it got.
_MAX_CONSOLIDATE_BATCHES = 10_000


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
        seen: Set[str] = set()

        for event in events:
            # `MemoryEvent.scope` is a required field, and until now no adapter read it -- the
            # write scope won silently. That is finding F5's accepted-and-ignored class applied to
            # the one field whose mismatch is a *tenancy* question: a caller building events per
            # tenant and batching them through one `write` got them all under the call scope.
            if event.scope != scope:
                raise MemoryOperationError(
                    f"MemoryEvent.scope ({event.scope.key()}) does not match the write scope "
                    f"({scope.key()}). One write is one scope; batch per scope.",
                    field_name="scope",
                )
            target = tier or event.tier
            self._require_a_substrate_for(target)
            occurred_at = event.occurred_at if event.occurred_at is not None else now
            prepared = replace(
                event,
                id=event.id or str(uuid4()),
                tier=target,
                occurred_at=occurred_at,
                # Copied, not shared: `replace` keeps references, so the event handed to the graph
                # leg still aliased the caller's lists. The vector leg was already defensive; this
                # makes the two symmetric, which the comment below used to claim prematurely.
                topics=list(event.topics) if event.topics is not None else None,
                metadata=dict(event.metadata),
            )
            # Duplicate ids inside one batch destroy content silently: the vector leg upserts by id
            # (last wins) while the graph leg appends, so the substrates end up holding different
            # content under one id -- which then collides in fusion's id dedup -- and the receipt
            # still says every event was accepted. Five lines here beat a receipt that lies.
            if prepared.id in seen:
                raise MemoryOperationError(
                    f"Duplicate event id {prepared.id!r} in one write batch. One id must identify "
                    "one episode across both substrates (finding F1); two events sharing it would "
                    "overwrite in the vector leg and duplicate in the graph leg.",
                    field_name="id",
                )
            seen.add(prepared.id)  # type: ignore[arg-type]  # stamped just above
            stamped.append(prepared)
            records.append(
                MemoryRecord(
                    id=prepared.id,  # type: ignore[arg-type]  # stamped just above
                    tier=target,
                    content=prepared.content,
                    scope=scope,
                    topics=list(prepared.topics) if prepared.topics is not None else None,
                    t_valid=occurred_at,
                    created_at=now,
                    metadata=dict(prepared.metadata),
                )
            )
        return stamped, records

    def _require_a_substrate_for(self, tier: MemoryTier) -> None:
        """Refuse a write no *wired* substrate would actually store.

        Two distinct failures, and both used to return a success receipt for a write that vanished:

        * The tier routes nowhere at all -- ``WORKING`` is in-context state, owned by SM-C6 (#94).
        * The tier routes only to a substrate this provider does not have. A graph-only provider
          asked for a ``PROCEDURAL`` write skipped the vector branch (no vector) and the graph
          branch (procedural does not route there), then reported ``accepted=1``.

        Checking the intersection of *routed* and *wired* covers both, and covers whatever
        SM-C2 (#90) does to the routing table later without this needing to change.
        """
        routed = substrates_for_tier(tier)
        if not routed:
            raise MemoryOperationError(
                f"Tier {tier.value!r} has no persistent substrate, so it cannot be written through "
                "the memory seam. The working buffer is SM-C6's (#94).",
                field_name="tier",
            )
        wired = {
            name
            for name, present in (("vector", self.vector is not None), ("graph", self.graph is not None))
            if present
        }
        if not (routed & wired):
            raise MemoryOperationError(
                f"No wired substrate serves the {tier.value!r} tier: it routes to "
                f"{sorted(routed)} and this provider has {sorted(wired) or 'none'}.",
                field_name="tier",
            )

    def _require_readable(self, tier: MemoryTier) -> None:
        """Refuse a single-tier read of a tier nothing here can hold.

        Separate from :meth:`_require_a_substrate_for`, which is a *write* check -- gating a read
        with the write check was accidental, and it produced a third behaviour for the same tier:
        ``read_tier(WORKING)`` raised while ``read(tiers=[WORKING])`` returned ``[]``.

        Refusing is the right side of that inconsistency for a *named* tier. Asking for one tier by
        name is a claim it exists; a multi-tier read that happens to include it is a filter, and
        filters legitimately match nothing.
        """
        if not substrates_for_tier(tier):
            raise MemoryOperationError(
                f"Tier {tier.value!r} has no persistent substrate, so there is nothing to read. "
                "The working buffer is SM-C6's (#94).",
                field_name="tier",
            )

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

    @staticmethod
    def _fact_for(event: MemoryEvent, record: MemoryRecord) -> FactWrite:
        """A directly-written semantic memory, in the shape the graph's fact door expects.

        No SPO: a caller writing a semantic memory through the seam supplies a statement, not a
        triple, and ``FactWrite.is_triple() == False`` is an explicitly supported shape. The point
        is the *door* -- see :meth:`write`.
        """
        return FactWrite(record=record, derived_by="co_located.direct_write")

    def write(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        stamped, records = self._prepare(scope, events, tier=None)
        # Routed, not "everything to the vector leg". The graph leg has always consulted
        # `substrates_for_tier`; the vector leg did not, which is latent only because every tier
        # currently routes to vector. SM-C2 (#90) adding one graph-only tier would have made it a
        # silent extra write.
        vector_records = [r for r in records if "vector" in substrates_for_tier(r.tier)]
        try:
            if self.vector is not None and vector_records:
                self.vector.upsert(scope, vector_records)
            if self.graph is not None:
                for event, record in zip(stamped, records):
                    if "graph" not in substrates_for_tier(event.tier):
                        continue
                    # Route by tier. A SEMANTIC write reaching the graph through `upsert_episode`
                    # is finding F9's failure class verbatim -- a fact stored as a flat
                    # pseudo-episode -- and deviation D3 added `upsert_fact` precisely to stop it.
                    # It fixed the consolidation path and left this one doing the thing it was
                    # added to prevent.
                    if event.tier is MemoryTier.SEMANTIC:
                        self.graph.upsert_fact(scope, self._fact_for(event, record))
                    else:
                        self.graph.upsert_episode(scope, event)
        except Exception as exc:
            raise PartialWriteError(self._receipt(stamped)) from exc
        return self._receipt(stamped)

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        stamped, records = self._prepare(scope, events, tier=None)
        vector_records = [r for r in records if "vector" in substrates_for_tier(r.tier)]
        try:
            if self.vector is not None and vector_records:
                await self.vector.aupsert(scope, vector_records)
            if self.graph is not None:
                for event, record in zip(stamped, records):
                    if "graph" not in substrates_for_tier(event.tier):
                        continue
                    if event.tier is MemoryTier.SEMANTIC:
                        await self.graph.aupsert_fact(scope, self._fact_for(event, record))
                    else:
                        await self.graph.aupsert_episode(scope, event)
        except Exception as exc:
            raise PartialWriteError(self._receipt(stamped)) from exc
        return self._receipt(stamped)

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return self.write(scope, [replace(event, tier=tier) for event in events])

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return await self.awrite(scope, [replace(event, tier=tier) for event in events])

    # -- read -----------------------------------------------------------------

    @staticmethod
    def _probe(query: MemoryQuery) -> MemoryQuery:
        """The query the substrates see. One call per leg, ``limit`` passed through unchanged.

        There is no over-fetch and no retry ladder. Both existed to compensate for filtering
        validity *after* the substrates had already applied ``limit`` -- which meant a page of
        invalidated rows could consume the whole limit and leave nothing. The compensation was
        always a mitigation: any fixed multiplier loses to a sufficiently lopsided corpus, and
        widening rounds multiply every read's cost to fix a case the backend should never have
        produced.

        The predicate now lives on the ports, where it belongs: a substrate filters to current
        belief valid at ``as_of`` *before* ranking, exactly as a ``WHERE`` clause does. SM-R12's
        row schemas are ``BiTemporal``, so a vector backend has the columns to do it -- the
        original "the vector substrate has no notion of validity" was true of the prototype's dict
        stand-in, not of the shipped schema.

        ``as_of`` is therefore **passed through** rather than stripped.
        """
        return query

    def _require_validity_authority(self, query: MemoryQuery) -> None:
        """Refuse a read whose validity answer no participating substrate can vouch for.

        The graph is the authority for bi-temporal validity. The precondition is that it is
        *participating* -- not that an ``as_of`` was passed, which is only the query shape that
        first exposed the gap.
        """
        if self.graph is not None and self._requested(query, Substrate.GRAPH):
            return
        if query.as_of is not None:
            raise MemoryOperationError(
                "A point-in-time read (as_of) needs the graph substrate, which is the authority "
                "for bi-temporal validity. Wire a graph substrate, or drop as_of from the query.",
                field_name="as_of",
            )
        if self.graph is not None:
            raise MemoryOperationError(
                "This provider has a graph substrate -- the authority for bi-temporal validity -- "
                "but the query excludes it. Answering from the vector projection alone returns "
                "facts the graph has recorded as false. Drop MemoryQuery.substrates.",
                field_name="substrates",
            )

    def _require_wired_substrates(self, query: MemoryQuery) -> None:
        """Refuse a query naming a substrate this provider does not have.

        Silently returning nothing was a third behaviour for one situation: a wired-but-excluded
        graph was refused, the seed refused any named substrate, and a named-but-unwired substrate
        here quietly emptied the result. Naming a substrate the deployment lacks is a mistaken
        assumption about the deployment, not a filter that legitimately matches nothing.
        """
        if query.substrates is None:
            return
        wired = self.substrates()
        missing = sorted(s.value for s in query.substrates if not wired[s])
        if missing:
            raise MemoryOperationError(
                f"This provider does not have the {missing} substrate(s) the query names; it has "
                f"{sorted(s.value for s, on in wired.items() if on)}. Drop MemoryQuery.substrates, "
                "or use a provider that wires them.",
                field_name="substrates",
            )

    def _fuse(self, legs: Sequence[Sequence[MemoryRecord]]) -> List[MemoryRecord]:
        fusion = self.config.fusion
        if fusion.get("method") == "rrf":
            return fuse_rrf(legs, k=int(fusion.get("k", 60)))
        vector_weight = float(fusion.get("vector_weight", 0.5))
        return fuse_weighted(legs, weights=[vector_weight, 1.0 - vector_weight])

    @staticmethod
    def _visible_at(fused: Sequence[MemoryRecord], instant: int) -> List[MemoryRecord]:
        """Defensive post-condition: drop anything not currently believed and valid at ``instant``.

        The ports require substrates to have done this already, so in a conforming deployment this
        removes nothing. It stays because a backend that drops the predicate would otherwise put
        facts the graph recorded as false straight into a prompt, and that failure is silent --
        the same reasoning as :meth:`_assert_tenant_isolation`.

        Both halves are checked. ``is_current_belief()`` is transaction-time only, and under SCD-2
        the successor row carries a past ``t_invalid`` *with* ``expired_at = None``, so checking
        only current belief let ``read()`` and ``read(as_of=now)`` disagree at the same instant.
        """
        return [record for record in fused if record.is_current_belief() and _valid_at(record, instant)]

    @staticmethod
    def _assert_tenant_isolation(scope: Scope, records: Sequence[MemoryRecord]) -> None:
        """No record may leave the seam under a tenant that did not ask for it.

        Every substrate is contractually required to apply the tenant predicate; this is the
        provider declining to take that on faith. A backend that drops its ``WHERE tenant_id``
        otherwise leaks straight through, and the projection stamps the reader's scope on the way
        out -- which is what makes such a leak invisible to every check above.

        Applied on **both** the read and the consolidate paths. On the read path a leak is
        transient; on the consolidate path it is *persisted* under the caller's tenant as a genuine
        row, and then passes this very check forever after.
        """
        foreign = {r.scope.tenant_id for r in records if r.scope.tenant_id != scope.tenant_id}
        if foreign:
            raise MemoryOperationError(
                f"A substrate returned records for {sorted(foreign)} on a call scoped to "
                f"{scope.tenant_id!r}. Refusing: a backend dropped its tenant predicate, and "
                "serving or persisting any of it would leak across tenants.",
                field_name="tenant_id",
            )

    def _assemble(
        self,
        scope: Scope,
        query: MemoryQuery,
        vector_hits: List[MemoryRecord],
        graph_hits: List[MemoryRecord],
        instant: int,
    ) -> MemorySlice:
        fused = self._fuse([vector_hits, graph_hits])
        self._assert_tenant_isolation(scope, fused)
        records = self._visible_at(fused, instant)[: query.limit] if query.limit else []

        by_tier: Dict[MemoryTier, List[MemoryRecord]] = {}
        for record in records:
            by_tier.setdefault(record.tier, []).append(record)

        diagnostics = fusion_diagnostics(
            method=self.config.fusion.get("method"),
            vector_hits=len(vector_hits),
            graph_hits=len(graph_hits),
            fused=len(fused),
            returned=len(records),
            instant=instant,
        )
        if self.config.fusion.get("method") != "rrf":
            diagnostics["vector_weight"] = self.config.fusion.get("vector_weight")
        return MemorySlice(scope=scope, query=query, records=records, by_tier=by_tier, fusion=diagnostics)

    def _guard_read(self, query: MemoryQuery) -> int:
        self._require_wired_substrates(query)
        self._require_validity_authority(query)
        return query.as_of if query.as_of is not None else now_epoch_s()

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        instant = self._guard_read(query)
        probe = self._probe(query)
        vector_hits: List[MemoryRecord] = []
        graph_hits: List[MemoryRecord] = []
        if self.vector is not None and self._requested(query, Substrate.VECTOR):
            vector_hits = list(self.vector.search(scope, probe))
        if self.graph is not None and self._requested(query, Substrate.GRAPH):
            graph_hits = list(self.graph.traverse(scope, probe))
        return self._assemble(scope, query, vector_hits, graph_hits, instant)

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        instant = self._guard_read(query)
        probe = self._probe(query)

        async def _vector() -> List[MemoryRecord]:
            if self.vector is None or not self._requested(query, Substrate.VECTOR):
                return []
            return list(await self.vector.asearch(scope, probe))

        async def _graph() -> List[MemoryRecord]:
            if self.graph is None or not self._requested(query, Substrate.GRAPH):
                return []
            return list(await self.graph.atraverse(scope, probe))

        # Concurrently: the two legs are independent by construction -- that independence is the
        # premise of the whole fusion design -- so awaiting them in series doubled read latency for
        # nothing, and Mode B pays that over a network.
        vector_hits, graph_hits = await asyncio.gather(_vector(), _graph())
        return self._assemble(scope, query, vector_hits, graph_hits, instant)

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        self._require_readable(tier)
        return self.read(scope, replace(query, tiers=[tier])).records

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        self._require_readable(tier)
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
        # Content-ordered, not scan-ordered. `scan` orders on `(created_at, id)` and ids are UUIDs,
        # so grouping in scan order made *which* episode heads a group -- and therefore the order
        # facts are written in -- vary per process. Any downstream consumer that falls back to
        # insertion order (an unscored substrate leg does) then produced a different ranking on
        # every run. Content is the stable key; `created_at` keeps genuine chronology within it.
        for episode in sorted(episodes, key=lambda r: (r.content, r.created_at or 0, r.id)):
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
            source_ids = sorted(member.id for member in group)
            facts.append(
                FactWrite(
                    record=MemoryRecord(
                        # Derived from the source episodes, not minted fresh, so a second pass over
                        # the same episodes upserts the same row. With uuid4 here, consolidate --
                        # documented as the *scheduled* sleep-time lane -- produced one more copy
                        # of every fact per cycle, and fusion's id-dedup could not collapse them
                        # because each copy had a different id. Prompt context degraded linearly
                        # with uptime.
                        #
                        # Idempotent only while group membership is stable: a sixth episode joining
                        # a five-episode group yields a new id and orphans the previous fact rather
                        # than superseding it. Pairing this with an explicit supersede is SM-L3's
                        # (#124), noted in SPEC section 11.3.
                        id=str(uuid5(_FACT_NAMESPACE, "|".join(source_ids))),
                        tier=MemoryTier.SEMANTIC,
                        content=head.content,
                        scope=scope,
                        topics=list(head.topics) if head.topics is not None else None,
                        provenance={"source_episode_ids": source_ids},
                        t_valid=head.t_valid,
                        # The *episodes'* recording time, not this pass's clock. `created_at` is
                        # "when the system recorded it" (`types.py`) and is the key `scan`'s
                        # watermark sorts on -- re-stamping it on every pass would march a stable
                        # fact forward in time and reorder the resumption sequence under it.
                        created_at=max((member.created_at or 0) for member in group) or now,
                    ),
                    source_episode_ids=source_ids,
                    derived_by="co_located.deterministic_dedup",
                )
            )
        return facts, merged

    def _check_consolidatable(self, tiers: Optional[Sequence[MemoryTier]]) -> None:
        """Refuse a consolidation request this pass would not actually perform.

        ``tiers`` accepted and never read meant ``consolidate(tiers=[PROCEDURAL])`` ran the
        episodic pass and reported success -- finding F5's class, which ``config.py`` rejects at
        the boundary. Only episodic-to-semantic exists; per-tier policy is SM-C2's (#90).
        """
        if self.vector is None:
            raise MemoryOperationError(
                "consolidate needs the vector substrate to enumerate episodes; none is wired.",
                field_name="provider",
            )
        if tiers is not None and set(tiers) != {MemoryTier.EPISODIC}:
            requested = sorted(tier.value for tier in tiers)
            raise MemoryOperationError(
                f"consolidate only runs the episodic-to-semantic pass, but was asked for "
                f"{requested}. Pass None or [MemoryTier.EPISODIC]; per-tier consolidation policy "
                "is SM-C2's (#90).",
                field_name="tiers",
            )

    def _report(
        self,
        scope: Scope,
        scanned: int,
        written: int,
        merged: int,
        batches: int,
    ) -> ConsolidationReport:
        return ConsolidationReport(
            scope=scope,
            episodic_scanned=scanned,
            semantic_written=written,
            duplicates_merged=merged,
            # Stays zero by design. Contradiction resolution needs extraction to know two facts
            # disagree, which is SM-L5's (#126). When it lands it routes through the graph's SCD-2
            # invalidate -- one write model, never a second in-place path.
            contradictions_resolved=0,
            details=consolidation_details(derived_by="co_located.deterministic_dedup", batches=batches, complete=True),
        )

    def _persist(self, scope: Scope, facts: Sequence[FactWrite]) -> None:
        if not facts:
            return
        assert self.vector is not None  # narrowed by _check_consolidatable
        # Same protection as `write`. The fan-out is identical, and a bare exception here left the
        # vector substrate holding facts the graph did not while the caller got no report at all.
        receipt = WriteReceipt(
            accepted=len(facts), event_ids=[f.record.id for f in facts], tiers_written=[MemoryTier.SEMANTIC]
        )
        try:
            self.vector.upsert(scope, [fact.record for fact in facts])
            if self.graph is not None:
                for fact in facts:
                    self.graph.upsert_fact(scope, fact)
        except Exception as exc:
            raise PartialWriteError(receipt) from exc

    async def _apersist(self, scope: Scope, facts: Sequence[FactWrite]) -> None:
        if not facts:
            return
        assert self.vector is not None  # narrowed by _check_consolidatable
        receipt = WriteReceipt(
            accepted=len(facts), event_ids=[f.record.id for f in facts], tiers_written=[MemoryTier.SEMANTIC]
        )
        try:
            await self.vector.aupsert(scope, [fact.record for fact in facts])
            if self.graph is not None:
                for fact in facts:
                    await self.graph.aupsert_fact(scope, fact)
        except Exception as exc:
            raise PartialWriteError(receipt) from exc

    @staticmethod
    def _next_cursor(episodes: Sequence[MemoryRecord]) -> Tuple[Optional[int], Optional[str]]:
        """Where the next batch resumes: the last row in scan order, not ``max(created_at)``.

        ``created_at`` is whole seconds and one ``write()`` stamps a single value across its whole
        batch, so a time-only cursor cannot page past a full batch of co-second rows -- it reports
        the value it was given. ``id`` breaks the tie, which is why the port promises that ordering.
        """
        last = episodes[-1] if episodes else None
        return (last.created_at, last.id) if last is not None else (None, None)

    def consolidate(
        self,
        scope: Scope,
        tiers: Optional[Sequence[MemoryTier]] = None,
    ) -> ConsolidationReport:
        """Distil the scope's episodes into semantic facts. **Complete when it returns.**

        Batching is an internal memory bound, not a protocol. An earlier version exposed
        ``since``/``after_id`` and asked the caller to loop on a reported cursor, which put the
        termination condition in the caller's hands: ignore it and episodes past the first batch
        were never consolidated, get it subtly wrong and the scheduler span forever. Neither
        failure is visible from a report that says the pass succeeded.

        So the loop lives here, over a private cursor, and the method is done when it returns.
        Scheduling *when* to call it is still SM-L1's (#122) -- that is a policy question. How to
        walk a tier without exhausting memory is not.
        """
        self._check_consolidatable(tiers)
        assert self.vector is not None  # narrowed by _check_consolidatable

        scanned = written = merged = batches = 0
        since: Optional[int] = None
        after_id: Optional[str] = None
        while batches < _MAX_CONSOLIDATE_BATCHES:
            episodes = list(
                self.vector.scan(scope, MemoryTier.EPISODIC, since=since, after_id=after_id, limit=_CONSOLIDATE_BATCH)
            )
            if not episodes:
                break
            self._assert_tenant_isolation(scope, episodes)
            facts, batch_merged = self._distil(scope, episodes)
            self._persist(scope, facts)
            scanned += len(episodes)
            written += len(facts)
            merged += batch_merged
            batches += 1
            if len(episodes) < _CONSOLIDATE_BATCH:
                break
            since, after_id = self._next_cursor(episodes)
        return self._report(scope, scanned, written, merged, batches)

    async def aconsolidate(
        self,
        scope: Scope,
        tiers: Optional[Sequence[MemoryTier]] = None,
    ) -> ConsolidationReport:
        """Async variant of :meth:`consolidate`. Also complete when it returns."""
        self._check_consolidatable(tiers)
        assert self.vector is not None  # narrowed by _check_consolidatable

        scanned = written = merged = batches = 0
        since: Optional[int] = None
        after_id: Optional[str] = None
        while batches < _MAX_CONSOLIDATE_BATCHES:
            episodes = list(
                await self.vector.ascan(
                    scope, MemoryTier.EPISODIC, since=since, after_id=after_id, limit=_CONSOLIDATE_BATCH
                )
            )
            if not episodes:
                break
            self._assert_tenant_isolation(scope, episodes)
            facts, batch_merged = self._distil(scope, episodes)
            await self._apersist(scope, facts)
            scanned += len(episodes)
            written += len(facts)
            merged += batch_merged
            batches += 1
            if len(episodes) < _CONSOLIDATE_BATCH:
                break
            since, after_id = self._next_cursor(episodes)
        return self._report(scope, scanned, written, merged, batches)


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
        vector=checked_vector(config.options.get("vector_substrate")),
        graph=checked_graph(config.options.get("graph_substrate")),
        config=config,
    )
