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
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple
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
    Scope,
    Substrate,
    WriteReceipt,
    fusion_diagnostics,
)
from egpt.utils.dttm import now_epoch_s

__all__ = ["CoLocatedMemoryProvider", "PartialWriteError"]


class PartialWriteError(RuntimeError):
    """A dual-substrate write failed partway, carrying the ids that were stamped.

    Two substrates with no distributed transaction cannot be atomic, so ``write`` is
    **at-least-once**: the vector leg may have committed when the graph leg throws.

    The ids matter more than the failure. ``_prepare`` mints a ``uuid4`` for any event whose ``id``
    is ``None``, so a naive retry writes *fresh* rows for the same logical episodes -- exactly the
    two-ids-for-one-episode failure that finding F1 and the single-id design exist to prevent,
    arriving through the error path. Carrying the receipt lets a caller retry with
    ``MemoryEvent.id`` set, which makes the retry idempotent.
    """

    def __init__(self, receipt: WriteReceipt) -> None:
        super().__init__(
            f"Write partially applied: {receipt.accepted} event(s) were stamped and may be "
            f"persisted in one substrate but not the other. Retry with these ids set on the "
            f"events to stay idempotent: {receipt.event_ids}."
        )
        self.receipt = receipt


#: Jaccard threshold above which two episodes are treated as the same thing during consolidation.
#:
#: Deterministic and deliberately crude. Real extraction and merge -- the mem0-style
#: ADD/UPDATE/DELETE/NOOP backbone SM-R7 ratified -- is SM-L3 (#124) and SM-L4 (#125). This exists
#: so ``consolidate`` is a working, testable pass rather than a stub, not because token overlap is
#: the right similarity measure.
_DUPLICATE_THRESHOLD = 0.8

#: Growth factor between probe rounds. Each round asks the substrates for this many times more
#: than the last, because the post-fusion validity filter can discard everything a narrow probe
#: returned. See ``CoLocatedMemoryProvider._probe_limits``.
_FILTER_OVERFETCH = 3

#: How many widening rounds before giving up and reporting ``truncated``. Four rounds reach 81x the
#: caller's limit, which covers a very lopsided corpus; beyond that the right answer is for the
#: substrate to filter before ranking, not for the provider to keep asking.
_MAX_PROBE_ROUNDS = 4

#: Absolute ceiling on a single probe, so a caller passing ``limit=10**8`` cannot ask a backend for
#: three hundred million rows.
_MAX_PROBE_LIMIT = 10_000

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
#: A pass that fills its batch reports ``next_since`` and ``batch_full`` so the caller can resume;
#: passing that value back as ``since`` advances through the tier. Without it the bound was a
#: dead end -- ``scan`` is ordered ascending ``created_at``, so every cycle re-read the same oldest
#: rows forever and episodes past the first batch were never consolidated at all.
_CONSOLIDATE_BATCH = 500


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
    def _probe(query: MemoryQuery, limit: int) -> MemoryQuery:
        """The query the substrates actually see: no ``as_of``, and a widened ``limit``.

        The ports say a backend must not apply ``as_of``, but stripping it is cheap insurance: a
        backend that filtered anyway would produce a subtly narrow result the fused filter could
        never widen back, and the symptom would look like a ranking problem.

        ``limit`` is supplied by :meth:`_probe_limits` rather than derived here, because one fixed
        multiplier is not enough -- see that method.
        """
        return replace(query, as_of=None, limit=limit)

    @staticmethod
    def _probe_limits(wanted: int) -> Iterator[int]:
        """Successively wider fetch sizes, until the ceiling.

        The validity predicates run after fusion, so records the substrates already counted against
        ``limit`` can be discarded afterwards. A single ``xN`` over-fetch only moves the threshold:
        with 50 invalidated rows outranking 10 live ones, ``x3`` returned zero records at every
        limit tried. Retrying with a wider window actually reaches the live rows.

        The **first** round asks for exactly what the caller wanted. Over-fetching up front would
        tax every read -- most corpora have nothing to discard -- and the retry makes it
        unnecessary: widening happens only when the filter actually ate the page.

        Bounded on purpose. The loop stops when the caller's ``limit`` is satisfied, when the
        substrates return less than they were asked for (the corpus is exhausted), or at
        ``_MAX_PROBE_ROUNDS`` -- and when it stops short it says so in the diagnostics rather than
        letting a truncated answer look like an empty corpus.

        ``limit=0`` yields ``0`` once: asking for nothing must not fetch anything, and a real
        backend distinguishes ``LIMIT 0`` from an omitted limit.
        """
        if wanted <= 0:
            yield 0
            return
        for round_ in range(_MAX_PROBE_ROUNDS):
            yield min(wanted * (_FILTER_OVERFETCH**round_), _MAX_PROBE_LIMIT)  # x1, x3, x9, x27

    def _reject_unanswerable_as_of(self, query: MemoryQuery) -> None:
        """Refuse a *historical* point-in-time read the wired substrates cannot answer honestly.

        Validity lives on the graph. The vector substrate holds a projection with no bounds
        (``substrates.py``), so with the graph absent or excluded an explicit ``as_of`` would be
        answered from rows that cannot contradict it.

        This does not apply to a default read. Since :meth:`_assemble` now evaluates validity at
        *now* whether or not ``as_of`` was passed, a vector-only tenant still has a query that is
        both accepted and correct -- records without bounds pass the predicate unchanged. What is
        refused is the narrower claim: "tell me what was true at time T", which needs an authority.

        It does not close the whole hole either: if the graph is wired but its top-``limit`` misses
        a row the vector leg returned, that row still has no bounds to check. Closing that needs a
        bounds-by-id lookup on ``GraphSubstrate``, recorded for SM-G1 (#99) in SPEC section 11.3.
        """
        if query.as_of is None:
            return
        if self.graph is None or not self._requested(query, Substrate.GRAPH):
            raise MemoryOperationError(
                "A point-in-time read (as_of) needs the graph substrate, which is the authority "
                "for bi-temporal validity. The vector substrate holds a projection with no bounds, "
                "so answering from it alone would return invalidated facts as current truth. "
                "Wire a graph substrate, or drop as_of from the query.",
                field_name="as_of",
            )

    def _fuse(self, legs: Sequence[Sequence[MemoryRecord]]) -> List[MemoryRecord]:
        fusion = self.config.fusion
        if fusion.get("method") == "rrf":
            return fuse_rrf(legs, k=int(fusion.get("k", 60)))
        vector_weight = float(fusion.get("vector_weight", 0.5))
        return fuse_weighted(legs, weights=[vector_weight, 1.0 - vector_weight])

    @staticmethod
    def _visible_at(fused: Sequence[MemoryRecord], instant: int) -> List[MemoryRecord]:
        """The records both currently believed and valid at ``instant``.

        **Both predicates, always.** ``is_current_belief()`` is transaction-time only
        (``expired_at IS NULL``), and under SCD-2 the successor row carries a past ``t_invalid``
        *with* ``expired_at = None`` -- so checking only current belief returned facts the graph had
        explicitly recorded as false. ``read()`` and ``read(as_of=now)`` disagreed at the same
        instant, and the empty one was right.

        So valid time is evaluated on every read; ``as_of`` chooses *which* instant rather than
        deciding whether the question is asked. Records with no bounds pass unchanged, which is why
        this does not narrow a vector-only tenant's results.
        """
        return [record for record in fused if record.is_current_belief() and _valid_at(record, instant)]

    def _assemble(
        self,
        scope: Scope,
        query: MemoryQuery,
        vector_hits: List[MemoryRecord],
        graph_hits: List[MemoryRecord],
        instant: int,
        probe_limit: int,
        exhausted: bool,
    ) -> MemorySlice:
        """Fuse, apply validity, cap, and group -- the order matters and is the point of F2."""
        fused = self._fuse([vector_hits, graph_hits])
        visible = self._visible_at(fused, instant)
        records = visible[: query.limit] if query.limit else []

        by_tier: Dict[MemoryTier, List[MemoryRecord]] = {}
        for record in records:
            by_tier.setdefault(record.tier, []).append(record)

        # `truncated` distinguishes "the corpus is empty" from "I hit the fetch ceiling with the
        # caller's limit unmet". Both look like a short result with non-zero hit counts, and only
        # one of them means the caller should widen the query or the backend should filter earlier.
        truncated = bool(query.limit) and len(records) < query.limit and not exhausted
        diagnostics = fusion_diagnostics(
            method=self.config.fusion.get("method"),
            vector_hits=len(vector_hits),
            graph_hits=len(graph_hits),
            fused=len(fused),
            returned=len(records),
            # The hit counts above are of the *probe*, not of the caller's limit, so they move with
            # the over-fetch. Reporting the probe limit is what makes them interpretable.
            probe_limit=probe_limit,
            truncated=truncated,
            instant=instant,
        )
        if self.config.fusion.get("method") != "rrf":
            diagnostics["vector_weight"] = self.config.fusion.get("vector_weight")

        return MemorySlice(scope=scope, query=query, records=records, by_tier=by_tier, fusion=diagnostics)

    def _legs(
        self, scope: Scope, probe: MemoryQuery, query: MemoryQuery
    ) -> Tuple[List[MemoryRecord], List[MemoryRecord]]:
        vector_hits: List[MemoryRecord] = []
        graph_hits: List[MemoryRecord] = []
        if self.vector is not None and self._requested(query, Substrate.VECTOR):
            vector_hits = list(self.vector.search(scope, probe))
        if self.graph is not None and self._requested(query, Substrate.GRAPH):
            graph_hits = list(self.graph.traverse(scope, probe))
        return vector_hits, graph_hits

    async def _alegs(
        self, scope: Scope, probe: MemoryQuery, query: MemoryQuery
    ) -> Tuple[List[MemoryRecord], List[MemoryRecord]]:
        vector_hits: List[MemoryRecord] = []
        graph_hits: List[MemoryRecord] = []
        if self.vector is not None and self._requested(query, Substrate.VECTOR):
            vector_hits = list(await self.vector.asearch(scope, probe))
        if self.graph is not None and self._requested(query, Substrate.GRAPH):
            graph_hits = list(await self.graph.atraverse(scope, probe))
        return vector_hits, graph_hits

    @staticmethod
    def _enough(
        vector_hits: Sequence[MemoryRecord],
        graph_hits: Sequence[MemoryRecord],
        instant: int,
        query: MemoryQuery,
        probe_limit: int,
        fused_visible: int,
    ) -> Tuple[bool, bool]:
        """``(satisfied, exhausted)`` for one probe round."""
        exhausted = probe_limit == 0 or (len(vector_hits) < probe_limit and len(graph_hits) < probe_limit)
        satisfied = fused_visible >= query.limit or exhausted
        return satisfied, exhausted

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        self._reject_unanswerable_as_of(query)
        instant = query.as_of if query.as_of is not None else now_epoch_s()

        vector_hits: List[MemoryRecord] = []
        graph_hits: List[MemoryRecord] = []
        probe_limit = 0
        exhausted = True
        for probe_limit in self._probe_limits(query.limit):
            vector_hits, graph_hits = self._legs(scope, self._probe(query, probe_limit), query)
            visible = len(self._visible_at(self._fuse([vector_hits, graph_hits]), instant))
            satisfied, exhausted = self._enough(vector_hits, graph_hits, instant, query, probe_limit, visible)
            if satisfied:
                break
        return self._assemble(scope, query, vector_hits, graph_hits, instant, probe_limit, exhausted)

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        self._reject_unanswerable_as_of(query)
        instant = query.as_of if query.as_of is not None else now_epoch_s()

        vector_hits: List[MemoryRecord] = []
        graph_hits: List[MemoryRecord] = []
        probe_limit = 0
        exhausted = True
        for probe_limit in self._probe_limits(query.limit):
            vector_hits, graph_hits = await self._alegs(scope, self._probe(query, probe_limit), query)
            visible = len(self._visible_at(self._fuse([vector_hits, graph_hits]), instant))
            satisfied, exhausted = self._enough(vector_hits, graph_hits, instant, query, probe_limit, visible)
            if satisfied:
                break
        return self._assemble(scope, query, vector_hits, graph_hits, instant, probe_limit, exhausted)

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

    def _report(
        self,
        scope: Scope,
        episodes: Sequence[MemoryRecord],
        facts: Sequence[FactWrite],
        merged: int,
    ) -> ConsolidationReport:
        """The pass result, including where the next pass should resume from.

        ``batch_full`` and ``next_since`` are what make the batch bound a pause rather than a
        ceiling. ``scan`` is ordered ascending ``created_at``, so a caller that keeps passing the
        reported ``next_since`` walks the whole tier; one that ignores it re-reads the oldest batch
        forever, which is what the bound did before it reported anything.

        ``next_since`` is the highest ``created_at`` seen, and ``scan``'s ``since`` is **inclusive**,
        so the boundary second is re-scanned. That is deliberate -- ``created_at`` is whole seconds
        and an exclusive bound would skip co-second episodes -- and the re-scan is harmless here
        because fact ids are derived from their source episodes, so a repeat upserts the same row.
        """
        batch_full = len(episodes) >= _CONSOLIDATE_BATCH
        next_since = max((record.created_at or 0) for record in episodes) if episodes else None
        return ConsolidationReport(
            scope=scope,
            episodic_scanned=len(episodes),
            semantic_written=len(facts),
            duplicates_merged=merged,
            # Stays zero by design. Contradiction resolution needs extraction to know two facts
            # disagree, which is SM-L5's (#126). When it lands it routes through the graph's
            # SCD-2 invalidate -- one write model, never a second in-place path.
            contradictions_resolved=0,
            details={
                "derived_by": "co_located.deterministic_dedup",
                "batch_full": batch_full,
                "next_since": next_since,
            },
        )

    def _check_consolidatable(self, tiers: Optional[Sequence[MemoryTier]]) -> None:
        """Refuse a consolidation request this pass would not actually perform.

        ``tiers`` was accepted and then never read, so ``consolidate(tiers=[PROCEDURAL])`` ran the
        episodic pass and returned a report saying so, with no mismatch signalled. That is finding
        F5's failure class -- a setting accepted and ignored -- which ``config.py`` rejects at the
        boundary for fusion knobs. Same rigour, one module over.

        Only episodic-to-semantic exists today. Per-tier consolidation policy is SM-C2's (#90).
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

    def consolidate(
        self,
        scope: Scope,
        tiers: Optional[Sequence[MemoryTier]] = None,
        since: Optional[int] = None,
    ) -> ConsolidationReport:
        """Distil one bounded batch of episodes, starting at ``since``.

        ``since`` is the resumption point -- pass the previous report's ``details["next_since"]``
        to advance. The scheduled lane (SM-L1 #122) owns looping until ``batch_full`` is false.
        """
        self._check_consolidatable(tiers)
        assert self.vector is not None  # narrowed by _check_consolidatable
        episodes = list(self.vector.scan(scope, MemoryTier.EPISODIC, since=since, limit=_CONSOLIDATE_BATCH))
        facts, merged = self._distil(scope, episodes)

        # One batched upsert rather than one call per fact: `upsert` takes a Sequence precisely so
        # a backend can round-trip once instead of N times.
        if facts:
            self.vector.upsert(scope, [fact.record for fact in facts])
        if self.graph is not None:
            for fact in facts:
                self.graph.upsert_fact(scope, fact)
        return self._report(scope, episodes, facts, merged)

    async def aconsolidate(
        self,
        scope: Scope,
        tiers: Optional[Sequence[MemoryTier]] = None,
        since: Optional[int] = None,
    ) -> ConsolidationReport:
        """Async variant of :meth:`consolidate`, with the same resumption contract."""
        self._check_consolidatable(tiers)
        assert self.vector is not None  # narrowed by _check_consolidatable
        episodes = list(await self.vector.ascan(scope, MemoryTier.EPISODIC, since=since, limit=_CONSOLIDATE_BATCH))
        facts, merged = self._distil(scope, episodes)

        if facts:
            await self.vector.aupsert(scope, [fact.record for fact in facts])
        if self.graph is not None:
            for fact in facts:
                await self.graph.aupsert_fact(scope, fact)
        return self._report(scope, episodes, facts, merged)


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
