"""A ``MemoryProvider`` composing both substrate ports (SM-C1).

Straight-line composition: fan a write out to the substrates the tier routes to, merge two read
legs into one slice, distil episodes into facts. It holds no backend code.

Two post-conditions are re-checked here rather than trusted to a backend, because both fail
silently and one puts another tenant's data into a prompt: tenant isolation and bi-temporal
validity.

Out of scope, deliberately: rank fusion beyond a score sum (SM-H2 #132), a consolidation scheduler
or resumption cursor (SM-L1 #122), per-tier routing policy (SM-C2 #90).
"""

import asyncio
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Set, Tuple
from uuid import NAMESPACE_OID, uuid4, uuid5

from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError, register_provider
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
from egpt.memory.validation import reject_duplicate_ids, reject_foreign_scopes
from egpt.utils.dttm import now_epoch_s

__all__ = ["CoLocatedMemoryProvider"]

#: Jaccard threshold above which two episodes are treated as the same thing.
#: Deterministic and crude on purpose; real extraction is SM-L3 (#124) / SM-L4 (#125).
_DUPLICATE_THRESHOLD = 0.8

#: Namespace for deriving a fact id from its source episodes, so a re-run upserts the same row.
_FACT_NAMESPACE = uuid5(NAMESPACE_OID, "egpt.memory.consolidate.fact")

#: Safety cap on one consolidation pass. Not a resumption protocol -- when the cap is hit the
#: report says ``complete=False``, and draining the rest is SM-L1's (#122).
_MAX_EPISODES_PER_PASS = 5_000


def _tokens(text: str) -> Set[str]:
    return set(text.lower().split())


def _jaccard(left: Set[str], right: Set[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


def _valid_at(record: MemoryRecord, at: int) -> bool:
    """``t_valid <= at < t_invalid``, with ``None`` open at either end.

    Both bounds: checking only ``t_invalid`` lets a future-dated record answer about the past.
    """
    if record.t_valid is not None and record.t_valid > at:
        return False
    if record.t_invalid is not None and record.t_invalid <= at:
        return False
    return True


def _merge(legs: Sequence[Sequence[MemoryRecord]]) -> List[MemoryRecord]:
    """One ranked list from two legs: dedup by id, sum scores, rank deterministically.

    A score sum, not a fusion strategy. Weighted and RRF, their knobs and their diagnostics belong
    to SM-H2 (#132), which owns the measurement that would justify picking one.

    Ties break on content, then tier, then id -- never on arrival order, which is driven by UUID
    record ids and made identical corpora rank differently across processes.
    """
    merged: Dict[str, MemoryRecord] = {}
    for records in legs:
        seen: Set[str] = set()
        for record in records:
            if record.id in seen:
                continue  # one leg contributes one score per id
            seen.add(record.id)
            existing = merged.get(record.id)
            if existing is None:
                merged[record.id] = record.detached()
                continue
            existing.score = (existing.score or 0.0) + (record.score or 0.0)
            existing.substrate = None  # no longer from one place
            existing.tighten_bounds(record)
    return sorted(merged.values(), key=lambda r: (-(r.score or 0.0), r.content, r.tier.value, r.id))


class CoLocatedMemoryProvider(MemoryProvider):
    """A provider over co-located vector and graph substrates.

    Either substrate may be absent -- a vector-only tenant is supported (SM-C5 #93) and
    ``substrates()`` reports which are wired. Both absent fails at construction.
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
                "CoLocatedMemoryProvider needs at least one substrate; both are unset.",
                field_name="provider",
            )
        self.vector = vector
        self.graph = graph
        self.config = config or MemoryProviderConfig(provider=self.provider_key)

    def substrates(self) -> Dict[Substrate, bool]:
        return {Substrate.VECTOR: self.vector is not None, Substrate.GRAPH: self.graph is not None}

    # -- guards ---------------------------------------------------------------

    def _wired(self) -> Set[str]:
        return {name for name, on in (("vector", self.vector), ("graph", self.graph)) if on is not None}

    def _require_writable(self, tier: MemoryTier) -> None:
        """Refuse a write no *wired* substrate would store, rather than returning a success receipt."""
        routed = substrates_for_tier(tier)
        if not routed:
            raise MemoryOperationError(
                f"Tier {tier.value!r} has no persistent substrate. The working buffer is SM-C6's (#94).",
                field_name="tier",
            )
        if not (routed & self._wired()):
            raise MemoryOperationError(
                f"No wired substrate serves the {tier.value!r} tier: it routes to {sorted(routed)} "
                f"and this provider has {sorted(self._wired()) or 'none'}.",
                field_name="tier",
            )

    def _require_readable(self, tier: MemoryTier) -> None:
        """Asking for one tier by name is a claim it exists; a multi-tier filter is not."""
        if not substrates_for_tier(tier):
            raise MemoryOperationError(
                f"Tier {tier.value!r} has no persistent substrate, so there is nothing to read.",
                field_name="tier",
            )

    def _require_answerable(self, query: MemoryQuery) -> None:
        """Refuse a read this provider cannot answer correctly.

        Three cases, all refusals rather than empty results: naming a substrate that is not wired,
        excluding the graph when one *is* wired, and asking for a point in time with no graph at
        all. The last two are one rule -- the graph is the authority for validity, so an answer
        assembled without it contains facts the graph has recorded as false.

        The ``as_of`` check is outside the ``substrates`` branch on purpose: a vector-only provider
        names no substrates and still cannot answer a point-in-time read.
        """
        wired = self.substrates()
        if query.as_of is not None and not wired[Substrate.GRAPH]:
            raise MemoryOperationError(
                "A point-in-time read needs the graph substrate, which is the authority for "
                "bi-temporal validity; none is wired, and the vector projection carries no bounds "
                "so it would return invalidated facts as true.",
                field_name="as_of",
            )
        if query.substrates is None:
            return
        if not query.substrates:
            # Unlike `tiers=[]`, which is a coherent "search no tiers", an empty substrate set is a
            # request nothing could ever answer. Left to fall through it produced three behaviours
            # for one input -- refused with a graph wired, silently empty on a vector-only provider,
            # and refused on the seed -- so the same query meant different things per deployment.
            raise MemoryOperationError(
                "MemoryQuery.substrates is empty, so no substrate would be consulted and the read "
                "could only ever return nothing. Pass None for every wired substrate, or name the "
                "ones you want.",
                field_name="substrates",
            )
        missing = sorted(s.value for s in query.substrates if not wired[s])
        if missing:
            raise MemoryOperationError(
                f"This provider does not have the {missing} substrate(s) the query names.",
                field_name="substrates",
            )
        if wired[Substrate.GRAPH] and Substrate.GRAPH not in query.substrates:
            reason = "point-in-time " if query.as_of is not None else ""
            raise MemoryOperationError(
                f"The query excludes the graph from a {reason}read, and the graph is the authority "
                "for bi-temporal validity, so the answer would include facts it has recorded as "
                "false.",
                field_name="substrates",
            )

    @staticmethod
    def _assert_tenant_isolation(scope: Scope, records: Sequence[MemoryRecord]) -> None:
        """No record may leave the seam, or be persisted, under a tenant that did not ask for it.

        Substrates are required to apply the tenant predicate; this declines to take that on faith.
        Applied on the consolidate path too, where a leaked row would otherwise be written back as
        a genuine row under the caller's tenant and pass every later check.
        """
        foreign = {r.scope.tenant_id for r in records if r.scope.tenant_id != scope.tenant_id}
        if foreign:
            raise MemoryOperationError(
                f"A substrate returned records for {sorted(foreign)} on a call scoped to "
                f"{scope.tenant_id!r}; a backend dropped its tenant predicate.",
                field_name="tenant_id",
            )

    # -- write ----------------------------------------------------------------

    def _prepare(self, scope: Scope, events: Sequence[MemoryEvent]) -> Tuple[List[MemoryEvent], List[MemoryRecord]]:
        """Stamp one id per event and project each into the record the vector leg stores.

        The id is stamped once and reused across substrates: it is the cross-substrate dedup key,
        so a second id turns one episode into two results.
        """
        reject_foreign_scopes(scope, events)
        reject_duplicate_ids(events)

        now = now_epoch_s()
        stamped: List[MemoryEvent] = []
        records: List[MemoryRecord] = []
        for event in events:
            target = event.tier
            self._require_writable(target)
            occurred_at = event.occurred_at if event.occurred_at is not None else now
            prepared = replace(
                event,
                id=event.id or str(uuid4()),
                tier=target,
                occurred_at=occurred_at,
                topics=list(event.topics) if event.topics is not None else None,
                metadata=dict(event.metadata),
            )
            stamped.append(prepared)
            records.append(
                MemoryRecord(
                    id=prepared.id,  # type: ignore[arg-type]  # stamped above
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
        stamped, records = self._prepare(scope, events)
        vector_records = [r for r in records if "vector" in substrates_for_tier(r.tier)]
        try:
            if self.vector is not None and vector_records:
                self.vector.upsert(scope, vector_records)
            if self.graph is not None:
                for event, record in zip(stamped, records):
                    self._graph_write(scope, event, record)
        except Exception as exc:
            raise PartialWriteError(self._receipt(stamped)) from exc
        return self._receipt(stamped)

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        stamped, records = self._prepare(scope, events)
        vector_records = [r for r in records if "vector" in substrates_for_tier(r.tier)]
        try:
            if self.vector is not None and vector_records:
                await self.vector.aupsert(scope, vector_records)
            if self.graph is not None:
                for event, record in zip(stamped, records):
                    await self._agraph_write(scope, event, record)
        except Exception as exc:
            raise PartialWriteError(self._receipt(stamped)) from exc
        return self._receipt(stamped)

    def _graph_write(self, scope: Scope, event: MemoryEvent, record: MemoryRecord) -> None:
        """Route by tier: a semantic memory is a fact, not an episode."""
        if "graph" not in substrates_for_tier(event.tier) or self.graph is None:
            return
        if event.tier is MemoryTier.SEMANTIC:
            self.graph.upsert_fact(scope, FactWrite(record=record, derived_by="co_located.direct_write"))
        else:
            self.graph.upsert_episode(scope, event)

    async def _agraph_write(self, scope: Scope, event: MemoryEvent, record: MemoryRecord) -> None:
        if "graph" not in substrates_for_tier(event.tier) or self.graph is None:
            return
        if event.tier is MemoryTier.SEMANTIC:
            await self.graph.aupsert_fact(scope, FactWrite(record=record, derived_by="co_located.direct_write"))
        else:
            await self.graph.aupsert_episode(scope, event)

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return self.write(scope, [replace(event, tier=tier) for event in events])

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        return await self.awrite(scope, [replace(event, tier=tier) for event in events])

    # -- read -----------------------------------------------------------------

    def _slice(
        self,
        scope: Scope,
        query: MemoryQuery,
        vector_hits: List[MemoryRecord],
        graph_hits: List[MemoryRecord],
        instant: int,
    ) -> MemorySlice:
        merged = _merge([vector_hits, graph_hits])
        self._assert_tenant_isolation(scope, merged)

        # Both predicates, always. `is_current_belief` is transaction time only, and under SCD-2
        # the successor row carries a past `t_invalid` with `expired_at = None` -- so checking only
        # current belief let `read()` and `read(as_of=now)` disagree at the same instant.
        visible = [r for r in merged if r.is_current_belief() and _valid_at(r, instant)]
        records = visible[: query.limit] if query.limit else []

        by_tier: Dict[MemoryTier, List[MemoryRecord]] = {}
        for record in records:
            by_tier.setdefault(record.tier, []).append(record)

        return MemorySlice(
            scope=scope,
            query=query,
            records=records,
            by_tier=by_tier,
            fusion=fusion_diagnostics(
                method="score_sum",
                vector_hits=len(vector_hits),
                graph_hits=len(graph_hits),
                fused=len(merged),
                returned=len(records),
                # A short result with non-zero hits is ambiguous otherwise: an empty corpus and a
                # page the validity filter emptied look identical. Substrates filter transaction
                # time before ranking, so what reaches here is the valid-time discard.
                dropped_by_validity=len(merged) - len(visible),
                instant=instant,
            ),
        )

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        self._require_answerable(query)
        instant = query.as_of if query.as_of is not None else now_epoch_s()
        vector_hits: List[MemoryRecord] = []
        graph_hits: List[MemoryRecord] = []
        if self.vector is not None and self._uses(query, Substrate.VECTOR):
            vector_hits = list(self.vector.search(scope, query))
        if self.graph is not None and self._uses(query, Substrate.GRAPH):
            graph_hits = list(self.graph.traverse(scope, query))
        return self._slice(scope, query, vector_hits, graph_hits, instant)

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        self._require_answerable(query)
        instant = query.as_of if query.as_of is not None else now_epoch_s()

        async def vector_leg() -> List[MemoryRecord]:
            if self.vector is None or not self._uses(query, Substrate.VECTOR):
                return []
            return list(await self.vector.asearch(scope, query))

        async def graph_leg() -> List[MemoryRecord]:
            if self.graph is None or not self._uses(query, Substrate.GRAPH):
                return []
            return list(await self.graph.atraverse(scope, query))

        # Concurrent: the legs are independent by construction, and Mode B pays for series over a
        # network.
        vector_hits, graph_hits = await asyncio.gather(vector_leg(), graph_leg())
        return self._slice(scope, query, vector_hits, graph_hits, instant)

    @staticmethod
    def _uses(query: MemoryQuery, substrate: Substrate) -> bool:
        return query.substrates is None or substrate in query.substrates

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        self._require_readable(tier)
        return self.read(scope, replace(query, tiers=[tier])).records

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        self._require_readable(tier)
        return (await self.aread(scope, replace(query, tiers=[tier]))).records

    # -- consolidate ----------------------------------------------------------

    def _check_consolidatable(self, tiers: Optional[Sequence[MemoryTier]]) -> VectorSubstrate:
        """Refuse an unrunnable pass, and hand back the substrate that will run it.

        Returning it rather than re-reading ``self.vector`` is what lets the callers narrow the
        Optional without an ``assert`` -- which ``-O`` strips, and which the seed adapter's own
        comment rejects for exactly that reason.
        """
        if self.vector is None:
            raise MemoryOperationError(
                "consolidate needs the vector substrate to enumerate episodes; none is wired.",
                field_name="provider",
            )
        if tiers is not None and set(tiers) != {MemoryTier.EPISODIC}:
            raise MemoryOperationError(
                f"consolidate only runs the episodic-to-semantic pass, but was asked for "
                f"{sorted(t.value for t in tiers)}. Per-tier policy is SM-C2's (#90).",
                field_name="tiers",
            )
        return self.vector

    def _distil(self, scope: Scope, episodes: Sequence[MemoryRecord]) -> Tuple[List[FactWrite], int]:
        """Group near-duplicate episodes and emit one fact per group.

        Deterministic token overlap, so the pass is testable end to end. This is the *shape*, not
        the strategy -- SM-R7 ratified mem0-style extract-and-merge, which is SM-L3's (#124).

        Grouping walks content order, not scan order: scan orders on ``(created_at, id)`` with UUID
        ids, so scan order made which episode heads a group vary per process.

        **Two episodes never group across scopes, even inside one tenant.** A pass scoped to a
        tenant (``Scope(tenant_id="acme")``, no ``user_id``) legitimately scans every user's
        episodes, and merging on content alone put one user's episode into a fact attributed to
        another -- who then read it back as their own. ``_assert_tenant_isolation`` cannot see this:
        both rows are the same tenant. Which user the fact landed on was decided by the content
        tiebreak, so the attribution was arbitrary as well as wrong.
        """
        now = now_epoch_s()
        groups: List[List[MemoryRecord]] = []
        for episode in sorted(episodes, key=lambda r: (r.scope.key(), r.content, r.created_at or 0, r.id)):
            tokens = _tokens(episode.content)
            for group in groups:
                if group[0].scope != episode.scope:
                    continue
                if _jaccard(_tokens(group[0].content), tokens) >= _DUPLICATE_THRESHOLD:
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
                    # Derived from the sources, so a re-run upserts the same row rather than
                    # accumulating a copy per pass.
                    record=MemoryRecord(
                        id=str(uuid5(_FACT_NAMESPACE, "|".join(source_ids))),
                        tier=MemoryTier.SEMANTIC,
                        content=head.content,
                        scope=head.scope,
                        topics=list(head.topics) if head.topics is not None else None,
                        provenance={"source_episode_ids": source_ids},
                        t_valid=head.t_valid,
                        created_at=max((m.created_at or 0) for m in group) or now,
                    ),
                    source_episode_ids=source_ids,
                    derived_by="co_located.deterministic_dedup",
                )
            )
        return facts, merged

    def _report(
        self, scope: Scope, episodes: Sequence[MemoryRecord], facts: Sequence[FactWrite], merged: int
    ) -> ConsolidationReport:
        return ConsolidationReport(
            scope=scope,
            episodic_scanned=len(episodes),
            semantic_written=len(facts),
            duplicates_merged=merged,
            # Zero by design: knowing two facts disagree needs extraction, which is SM-L5's (#126).
            contradictions_resolved=0,
            details=consolidation_details(
                derived_by="co_located.deterministic_dedup",
                # False when the pass hit its cap, so a caller can tell a drained tier from a
                # truncated one. Draining the remainder is SM-L1's (#122).
                complete=len(episodes) < _MAX_EPISODES_PER_PASS,
            ),
        )

    @staticmethod
    def _by_scope(facts: Sequence[FactWrite]) -> List[Tuple[Scope, List[FactWrite]]]:
        """Batch facts by their own scope, because that is the scope they must be written under.

        ``VectorSubstrate.upsert(scope, records)`` stores *within* the call scope, so passing the
        pass-wide scope would file a fact derived from one user's episodes under the whole tenant
        -- readable by every user in it. On the ``write`` path the two always agree, because
        ``_prepare`` stamps the call scope onto every record; consolidate is the only path where a
        record carries a narrower scope than the call.
        """
        batched: Dict[str, Tuple[Scope, List[FactWrite]]] = {}
        for fact in facts:
            batched.setdefault(fact.record.scope.key(), (fact.record.scope, []))[1].append(fact)
        return [batched[key] for key in sorted(batched)]

    def consolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        vector = self._check_consolidatable(tiers)
        episodes = list(vector.scan(scope, MemoryTier.EPISODIC, limit=_MAX_EPISODES_PER_PASS))
        self._assert_tenant_isolation(scope, episodes)
        facts, merged = self._distil(scope, episodes)
        if facts:
            receipt = WriteReceipt(accepted=len(facts), event_ids=[f.record.id for f in facts])
            try:
                for fact_scope, batch in self._by_scope(facts):
                    vector.upsert(fact_scope, [f.record for f in batch])
                    if self.graph is not None:
                        for fact in batch:
                            self.graph.upsert_fact(fact_scope, fact)
            except Exception as exc:
                raise PartialWriteError(receipt) from exc
        return self._report(scope, episodes, facts, merged)

    async def aconsolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        vector = self._check_consolidatable(tiers)
        episodes = list(await vector.ascan(scope, MemoryTier.EPISODIC, limit=_MAX_EPISODES_PER_PASS))
        self._assert_tenant_isolation(scope, episodes)
        facts, merged = self._distil(scope, episodes)
        if facts:
            receipt = WriteReceipt(accepted=len(facts), event_ids=[f.record.id for f in facts])
            try:
                for fact_scope, batch in self._by_scope(facts):
                    await vector.aupsert(fact_scope, [f.record for f in batch])
                    if self.graph is not None:
                        for fact in batch:
                            await self.graph.aupsert_fact(fact_scope, fact)
            except Exception as exc:
                raise PartialWriteError(receipt) from exc
        return self._report(scope, episodes, facts, merged)


@register_provider("co_located")
def _build_co_located(config: MemoryProviderConfig) -> MemoryProvider:
    """Substrates arrive through ``config.options`` until SM-V1 (#95) / SM-G1 (#99) register real
    backends, at which point ``config.vector``/``config.graph`` resolve through
    ``egpt.registry.discovery.construct`` and this stays the only construction call site."""
    return CoLocatedMemoryProvider(
        vector=checked_vector(config.options.get("vector_substrate")),
        graph=checked_graph(config.options.get("graph_substrate")),
        config=config,
    )
