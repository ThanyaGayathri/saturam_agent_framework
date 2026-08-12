"""A ``MemoryProvider`` composing both substrate ports (SM-C1).

Straight-line composition: fan a write out to the substrates the tier routes to, merge two read
legs into one slice, distil episodes into facts. It holds no backend code.

Two post-conditions are re-checked here rather than trusted to a backend, because both fail
silently and one puts another tenant's data into a prompt: tenant isolation and bi-temporal
validity.

Out of scope, deliberately: rank fusion beyond a score sum (SM-H2 #132), a consolidation scheduler
or resumption cursor (SM-L1 #122), **retiring a fact whose source group changed** (SM-L3 #124),
per-tier routing policy (SM-C2 #90). ``consolidate`` here is scan -> filter -> distil -> write, and
the report names what it does not do.
"""

import asyncio
from dataclasses import replace
from typing import Any, Awaitable, Dict, List, Optional, Sequence, Set, Tuple
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

#: Cap on simultaneous backend calls in one gathered write. Unbounded fan-out against a real
#: engine is thousands of concurrent round trips, which exhausts a pool rather than going fast.
_MAX_CONCURRENT_WRITES = 16

#: Safety cap on one consolidation pass. Not a resumption protocol -- when the cap is hit the
#: report says ``complete=False``, and draining the rest is SM-L1's (#122).
_MAX_EPISODES_PER_PASS = 5_000


def _positive_int(value: Any, option: str) -> int:
    """Validate a bound that reaches a substrate, at construction rather than three frames down.

    Both bounds arrive through ``config.options``, so they are user-supplied and get the same
    boundary treatment as everything else in this package. ``0`` is the case that matters:
    ``max_episodes_per_pass=0`` scans nothing forever while reporting a tier that needs draining,
    so a scheduler retries for eternity and nothing is ever consolidated.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MemoryProviderConfigError(
            f"{option} must be an integer >= 1, got {value!r}. Zero would scan nothing while "
            "reporting the tier as undrained, which a scheduler retries forever.",
            field_name=f"options.{option}",
        )
    return value


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

    Bounds are **not** merged between the legs here. They used to be, but
    ``_reconcile_bounds`` runs straight after and overwrites every id the graph knows from the
    authority itself -- and any id a graph leg contributed is by definition an id the graph knows.
    Peer-merging was unreachable, and an unreachable invariant is one nothing can test.
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
        max_episodes_per_pass: int = _MAX_EPISODES_PER_PASS,
        max_concurrent_writes: int = _MAX_CONCURRENT_WRITES,
    ) -> None:
        if vector is None and graph is None:
            raise MemoryProviderConfigError(
                "CoLocatedMemoryProvider needs at least one substrate; both are unset.",
                field_name="provider",
            )
        self.vector = vector
        self.graph = graph
        self.config = config or MemoryProviderConfig(provider=self.provider_key)
        #: Ceiling on one consolidation pass. Adjustable because the default is a safety bound, not
        #: a policy: a tier larger than this has its tail consolidated *never*, not later.
        self.max_episodes_per_pass = _positive_int(max_episodes_per_pass, "max_episodes_per_pass")
        #: Cap on simultaneous per-record backend calls in one async write.
        self.max_concurrent_writes = _positive_int(max_concurrent_writes, "max_concurrent_writes")

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

    @staticmethod
    def _assert_requested_tiers(query: MemoryQuery, records: Sequence[MemoryRecord]) -> None:
        """A substrate may not return a tier the query did not ask for.

        The provider re-checks scope and validity after the legs return and used to trust tier
        entirely, so a backend ignoring ``resolved_tiers()`` had ``read_tier(SEMANTIC)`` returning
        episodic and procedural rows, faithfully grouped under those tiers in ``by_tier``. The
        existing test asserts the two *legs agree with each other*, which cannot see a failure both
        legs share.
        """
        wanted = set(query.resolved_tiers())
        unwanted = sorted({r.tier.value for r in records if r.tier not in wanted})
        if unwanted:
            raise MemoryOperationError(
                f"A substrate returned tiers {unwanted} that the query did not ask for "
                f"({sorted(t.value for t in wanted)}); a backend ignored the tier filter.",
                field_name="tiers",
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
        if (
            query.as_of is not None
            and not wired[Substrate.GRAPH]
            and any("graph" in substrates_for_tier(t) for t in query.resolved_tiers())
        ):
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
        # Tier-aware: excluding the graph is only wrong for a tier the graph actually stores. A
        # procedural read is vector-only, so the graph has no validity opinion to withhold and
        # refusing it blocks the one read shape where skipping the leg is legitimate.
        graph_backed_tiers = any("graph" in substrates_for_tier(t) for t in query.resolved_tiers())
        if wired[Substrate.GRAPH] and graph_backed_tiers and Substrate.GRAPH not in query.substrates:
            reason = "point-in-time " if query.as_of is not None else ""
            raise MemoryOperationError(
                f"The query excludes the graph from a {reason}read, and the graph is the authority "
                "for bi-temporal validity, so the answer would include facts it has recorded as "
                "false.",
                field_name="substrates",
            )

    @staticmethod
    def _assert_scope_isolation(scope: Scope, records: Sequence[MemoryRecord]) -> None:
        """No record may leave the seam, or be persisted, under a scope that did not ask for it.

        **All five slots, not just the tenant.** The port requires a backend to apply every set
        slot conjunctively, and a backend that honours the tenant and drops the ``user_id``
        predicate -- the exact bug that docstring warns about -- served one user's row to another
        with a tenant-only guard watching. Four more comparisons on a list already in hand.

        A ``None`` slot on the call scope means "any", so it constrains nothing; a set slot must
        match exactly. Applied on the consolidate path too, where a leaked row would otherwise be
        distilled into a genuine fact under the caller's scope and pass every later check.
        """
        # Slot names and a count, never the offending values. This guard fires exactly when a
        # backend has leaked another tenant's rows, so echoing their identifiers puts them in a log
        # line or an API response -- and "which slot, how many" is the whole of what an operator
        # can act on.
        slots: Set[str] = set()
        offenders = 0
        for record in records:
            for slot in ("tenant_id", "user_id", "agent_id", "team_id", "session_id"):
                wanted = getattr(scope, slot)
                if wanted is not None and getattr(record.scope, slot, None) != wanted:
                    slots.add(slot)
                    offenders += 1
                    break
        if offenders:
            raise MemoryOperationError(
                f"A substrate returned {offenders} record(s) outside the call scope; it dropped a "
                f"scope predicate on {sorted(slots)}.",
                field_name="scope",
            )

    @staticmethod
    def _graph_backed(records: Sequence[MemoryRecord]) -> List[str]:
        """Ids whose tier routes to the graph -- the only ones it can have an opinion about.

        A procedural read is vector-only per ``SUBSTRATES_BY_TIER``, so asking the graph for its
        bounds is a guaranteed-empty round trip, and a network hop in Mode B.
        """
        return [r.id for r in records if "graph" in substrates_for_tier(r.tier)]

    def _reconcile_bounds(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        """Overwrite each record's bi-temporal bounds with the graph's, in place.

        Without this the read path serves facts the graph has recorded as **false**. The vector
        projection carries no bounds; under SCD-2 the graph's bound-bearing successor row holds the
        *same content* as the live row, so it scores identically and drops off the graph leg's
        top-``limit`` page as soon as the corpus is larger than the page. Nothing then reaches
        ``_merge`` to tighten against, and the stale row passes ``_valid_at`` labelled current
        belief. Measured: at ``limit=3`` over a six-row corpus, every returned row was invalidated.

        One batched call per read, not per record -- see ``GraphSubstrate.bounds_for_ids``.
        """
        askable = self._graph_backed(records)
        if self.graph is None or not askable:
            return
        bounds = self.graph.bounds_for_ids(scope, askable)
        for record in records:
            found = bounds.get(record.id)
            if found is not None:
                record.apply_bounds(found)

    async def _areconcile_bounds(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        """Async variant of :meth:`_reconcile_bounds`."""
        askable = self._graph_backed(records)
        if self.graph is None or not askable:
            return
        bounds = await self.graph.abounds_for_ids(scope, askable)
        for record in records:
            found = bounds.get(record.id)
            if found is not None:
                record.apply_bounds(found)

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
            event_id = event.id or str(uuid4())
            prepared = replace(
                event,
                id=event_id,
                tier=target,
                occurred_at=occurred_at,
                topics=list(event.topics) if event.topics is not None else None,
                metadata=dict(event.metadata),
            )
            stamped.append(prepared)
            records.append(
                MemoryRecord(
                    id=event_id,
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
        receipt = self._receipt(stamped)
        try:
            if self.vector is not None and vector_records:
                self.vector.upsert(scope, vector_records)
            if self.graph is not None:
                # Every event is attempted, then failures are raised together -- the sync half of
                # the settle-then-propagate rule `_gather_writes` applies. A refusal is collected
                # like any other failure rather than short-circuiting: an `except
                # MemoryOperationError: raise` arm here left the two halves three graph rows apart
                # for one identical failure, which is the divergence this loop exists to prevent.
                failures: List[BaseException] = []
                for event, record in zip(stamped, records):
                    try:
                        self._graph_write(scope, event, record)
                    except Exception as exc:  # noqa: PERF203 - per-event isolation is the point
                        failures.append(exc)
                self._raise_write_failure(failures, receipt)
        except (PartialWriteError, MemoryOperationError):
            raise  # already shaped by `_raise_write_failure`; re-wrapping buries the cause
        except Exception as exc:
            raise PartialWriteError(receipt) from exc
        return receipt

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        stamped, records = self._prepare(scope, events)
        vector_records = [r for r in records if "vector" in substrates_for_tier(r.tier)]
        receipt = self._receipt(stamped)
        try:
            if self.vector is not None and vector_records:
                await self.vector.aupsert(scope, vector_records)
            if self.graph is not None:
                # Gathered, not serial: a batch write is the stronger case for concurrency than the
                # read path, since the serial count is len(events) rather than 2. The port wants
                # `upsert_episodes` / `upsert_facts` eventually -- adding them after SM-G1 ships
                # implementors is a breaking Protocol change, so it is cheapest now.
                #
                # Parity with the sync half comes from *both* attempting every event, not from
                # `gather` itself: bare `gather` propagates the first exception without cancelling
                # its siblings, so they commit regardless. What `return_exceptions=True` adds is
                # deterministic selection -- a `MemoryOperationError` among the failures must win
                # over a transport error, or the same batch reports "retry me" on one run and
                # "this will never work" on the next -- and it stops sibling exceptions becoming
                # never-retrieved warnings.
                await self._gather_writes(
                    [self._agraph_write(scope, e, r) for e, r in zip(stamped, records)],
                    receipt,
                )
        except (PartialWriteError, MemoryOperationError):
            raise  # already shaped by `_raise_write_failure`; re-wrapping buries the cause
        except Exception as exc:
            raise PartialWriteError(receipt) from exc
        return receipt

    @staticmethod
    def _raise_write_failure(failures: Sequence[BaseException], receipt: WriteReceipt) -> None:
        """The one place either half decides what a fan-out failure becomes.

        Shared rather than written twice, because the two halves drifting is precisely the defect
        this keeps producing. Two rules:

        **A refusal wins over a transport error** as the cause, so the same batch does not report
        "retry me" on one run and "this will never work" on the next.

        **It is a ``PartialWriteError`` either way.** By the time a substrate refuses, the vector
        leg has already committed the whole batch -- so "a refusal is not a partial write" was
        false, and raising it bare denied the caller the receipt, whose ids are the only thing
        making the documented retry idempotent. Refusals raised *before* the fan-out, from
        ``_prepare``, still propagate bare: nothing was written, so there is nothing to reconcile.
        The refusal is preserved as ``__cause__``.
        """
        if not failures:
            return
        refusal = next((f for f in failures if isinstance(f, MemoryOperationError)), None)
        raise PartialWriteError(receipt) from (refusal or failures[0])

    async def _gather_writes(self, coroutines: Sequence[Awaitable[Any]], receipt: WriteReceipt) -> None:
        """Run write coroutines concurrently, **settle them all**, then propagate.

        Bounded by a semaphore as well: `batch` is capped only by `max_episodes_per_pass` and
        `events` is caller-supplied, so an unbounded fan-out is thousands of simultaneous round
        trips against a real engine -- which exhausts a connection pool rather than going fast.
        """
        semaphore = asyncio.Semaphore(self.max_concurrent_writes)

        async def bounded(coroutine: Awaitable[Any]) -> Any:
            async with semaphore:
                return await coroutine

        # Settled, so which failure surfaces is a decision rather than a race.
        results = await asyncio.gather(*(bounded(c) for c in coroutines), return_exceptions=True)
        self._raise_write_failure([r for r in results if isinstance(r, BaseException)], receipt)

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
        # Hoisted, because `_prepare`'s guard is inside the per-event loop: an empty batch never
        # reached it, so `write_tier(WORKING, [])` returned a success receipt for a tier that
        # `write_tier(WORKING, [event])` refuses. `seed.write_tier` already guards up front.
        self._require_writable(tier)
        return self.write(scope, [replace(event, tier=tier) for event in events])

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        self._require_writable(tier)
        return await self.awrite(scope, [replace(event, tier=tier) for event in events])

    # -- read -----------------------------------------------------------------

    def _slice(
        self,
        scope: Scope,
        query: MemoryQuery,
        merged: List[MemoryRecord],
        vector_hits: List[MemoryRecord],
        graph_hits: List[MemoryRecord],
        instant: int,
    ) -> MemorySlice:
        """Filter the reconciled set and package it. Bounds must already be authoritative."""
        # Both predicates, always. `is_current_belief` is transaction time only, and under SCD-2
        # the successor row carries a past `t_invalid` with `expired_at = None` -- so checking only
        # current belief let `read()` and `read(as_of=now)` disagree at the same instant.
        current = [r for r in merged if r.is_current_belief()]
        visible = [r for r in current if _valid_at(r, instant)]
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
                # page the validity filter emptied look identical. Precision is handled by
                # reconciliation, so what this counts is the recall shortfall named in
                # `VectorSubstrate.search`.
                # Pre-limit, and **valid time only**. Counting the transaction-time drop here too
                # conflated "the world moved on" with "a newer revision exists", which are
                # different diagnoses -- and `bounds_for_ids` is explicitly allowed to report
                # expired rows, so a conforming backend makes the difference observable.
                dropped_by_validity=len(current) - len(visible),
                dropped_as_superseded=len(merged) - len(current),
                # False when no graph participated, so the bounds in this slice are whatever the
                # vector projection carried. `read()` and `read(as_of=...)` disagree on such a
                # provider -- the explicit instant is refused while the implicit one is served --
                # and a caller cannot otherwise tell a reconciled slice from an unreconciled one.
                bounds_reconciled=self.graph is not None and self._uses(query, Substrate.GRAPH),
                instant=instant,
            ),
        )

    def _merged(self, scope: Scope, query: MemoryQuery, legs: Sequence[Sequence[MemoryRecord]]) -> List[MemoryRecord]:
        """Merge the legs and run every post-condition that does not need a backend call."""
        merged = _merge(legs)
        self._assert_scope_isolation(scope, merged)
        self._assert_requested_tiers(query, merged)
        return merged

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        self._require_answerable(query)
        instant = query.as_of if query.as_of is not None else now_epoch_s()
        vector_hits: List[MemoryRecord] = []
        graph_hits: List[MemoryRecord] = []
        if self.vector is not None and self._uses(query, Substrate.VECTOR):
            vector_hits = list(self.vector.search(scope, query))
        if self.graph is not None and self._uses(query, Substrate.GRAPH):
            graph_hits = list(self.graph.traverse(scope, query))
        merged = self._merged(scope, query, [vector_hits, graph_hits])
        self._reconcile_bounds(scope, merged)
        return self._slice(scope, query, merged, vector_hits, graph_hits, instant)

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
        merged = self._merged(scope, query, [vector_hits, graph_hits])
        await self._areconcile_bounds(scope, merged)
        return self._slice(scope, query, merged, vector_hits, graph_hits, instant)

    @staticmethod
    def _uses(query: MemoryQuery, substrate: Substrate) -> bool:
        return query.substrates is None or substrate in query.substrates

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        """Read one tier. A tier this provider does not serve reads **empty**, it is not refused.

        One rule across both adapters and both entry points. Previously the same logical request
        got three answers -- `co_located.read_tier(WORKING)` refused, `seed.read_tier(WORKING)`
        returned `[]`, and `co_located.read(tiers=[WORKING])` returned an empty slice -- so no
        caller could write one handler. Reads report absence; **writes** refuse it, because writing
        somewhere nothing will store it loses data while reading it simply finds nothing.
        """
        return self.read(scope, replace(query, tiers=[tier])).records

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        """Async variant of :meth:`read_tier`, with the same read-empty rule."""
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

    def _distil(self, episodes: Sequence[MemoryRecord]) -> Tuple[List[FactWrite], int]:
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
        # (scope, head tokens, members). The head's token set is cached rather than recomputed on
        # every comparison -- re-tokenising inside the inner loop dominated the quadratic term and
        # cost 7.5s of pure CPU at the 5,000 cap. Candidates are bucketed by scope first, so a
        # multi-user pass compares only within a user.
        buckets: Dict[Scope, List[Tuple[Set[str], List[MemoryRecord]]]] = {}
        for episode in sorted(episodes, key=lambda r: (r.scope.key(), r.content, r.created_at or 0, r.id)):
            tokens = _tokens(episode.content)
            for head_tokens, members in buckets.setdefault(episode.scope, []):
                if _jaccard(head_tokens, tokens) >= _DUPLICATE_THRESHOLD:
                    members.append(episode)
                    break
            else:
                buckets[episode.scope].append((tokens, [episode]))
        groups: List[List[MemoryRecord]] = [
            members for bucket_scope in sorted(buckets, key=lambda sc: sc.key()) for _, members in buckets[bucket_scope]
        ]

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
                        # The earliest evidence, not the head's. A fact claiming to become true
                        # later than the oldest episode it was distilled from answers a
                        # point-in-time read wrongly for the interval in between.
                        t_valid=min((m.t_valid for m in group if m.t_valid is not None), default=None),
                        created_at=max((m.created_at for m in group if m.created_at is not None), default=now),
                    ),
                    source_episode_ids=source_ids,
                    derived_by="co_located.deterministic_dedup",
                )
            )
        return facts, merged

    def _report(
        self,
        scope: Scope,
        scanned: Sequence[MemoryRecord],
        episodes: Sequence[MemoryRecord],
        facts: Sequence[FactWrite],
        merged: int,
    ) -> ConsolidationReport:
        hit_cap = len(scanned) >= self.max_episodes_per_pass
        return ConsolidationReport(
            scope=scope,
            episodic_scanned=len(scanned),
            semantic_written=len(facts),
            duplicates_merged=merged,
            # Zero by design: knowing two facts *disagree* needs extraction, which is SM-L5's
            # (#126).
            contradictions_resolved=0,
            details=consolidation_details(
                derived_by="co_located.deterministic_dedup",
                # False when the pass hit its cap. **The tail is not merely deferred: nothing marks
                # an episode consumed and `scan` is ordered oldest-first, so every pass re-reads the
                # same first N and episodes past the cap are never consolidated at all.** Raising
                # `max_episodes_per_pass` above the tier size is the only remedy until SM-L1 (#122)
                # brings a cursor.
                #
                # `>=`, deliberately conservative: `scan` returns at most `limit`, so a full page
                # and a truncated one are indistinguishable from here. A tier holding exactly the
                # cap is reported as truncated when it was drained -- the false positive costs one
                # unnecessary cap increase, the false negative costs silent loss of the tail.
                complete=not hit_cap,
                episodes_skipped_as_invalid=len(scanned) - len(episodes),
                tail_unreachable=hit_cap,
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

        Keyed on the frozen ``Scope`` itself, never on ``key()``. ``key()`` is now escaped and
        injective, but grouping identity should not depend on a string function at all -- the
        object is hashable, and an equality bug there is a misfiled write.
        """
        batched: Dict[Scope, List[FactWrite]] = {}
        for fact in facts:
            batched.setdefault(fact.record.scope, []).append(fact)
        return [(sc, batched[sc]) for sc in sorted(batched, key=lambda s: s.key())]

    def _fact_targets(self, facts: Sequence[FactWrite]) -> Tuple[bool, bool]:
        """Which substrates a semantic fact routes to, per ``SUBSTRATES_BY_TIER``.

        ``write`` filters by routing and ``_graph_write`` returns early when a tier does not reach
        the graph; consolidate did neither, so it wrote wherever a substrate happened to be wired.
        Latent while SEMANTIC routes to both -- and silent the moment SM-C2 (#90), which owns that
        table, changes a row.
        """
        routed = substrates_for_tier(MemoryTier.SEMANTIC) if facts else frozenset()
        return "vector" in routed, "graph" in routed

    def _visible_episodes(self, scope: Scope, episodes: List[MemoryRecord], instant: int) -> List[MemoryRecord]:
        """Drop episodes the graph has recorded as false before anything is distilled.

        ``scan`` reads the vector substrate, whose projection is stale with respect to validity, so
        an invalidated episode arrived here looking live and was minted into a **brand-new
        semantic fact with no bounds at all** -- unsuppressable afterwards by any read, at any
        limit, with any ``as_of``. Strictly worse than the read-path version of this bug, which at
        least stops when the corpus shrinks.
        """
        self._reconcile_bounds(scope, episodes)
        return [e for e in episodes if e.is_current_belief() and _valid_at(e, instant)]

    async def _avisible_episodes(self, scope: Scope, episodes: List[MemoryRecord], instant: int) -> List[MemoryRecord]:
        """Async variant of :meth:`_visible_episodes`."""
        await self._areconcile_bounds(scope, episodes)
        return [e for e in episodes if e.is_current_belief() and _valid_at(e, instant)]

    def consolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        vector = self._check_consolidatable(tiers)
        instant = now_epoch_s()
        # Detached before anything touches them: `scan` may hand back the rows the substrate is
        # holding (`search` returns copies, `scan` need not), and `_reconcile_bounds` writes bounds
        # onto what it is given.
        scanned = [r.detached() for r in vector.scan(scope, MemoryTier.EPISODIC, limit=self.max_episodes_per_pass)]
        self._assert_scope_isolation(scope, scanned)
        episodes = self._visible_episodes(scope, scanned, instant)
        facts, merged = self._distil(episodes)

        if facts:
            to_vector, to_graph = self._fact_targets(facts)
            receipt = WriteReceipt(accepted=0, event_ids=[], tiers_written=[MemoryTier.SEMANTIC])
            try:
                for fact_scope, batch in self._by_scope(facts):
                    if to_vector:
                        vector.upsert(fact_scope, [f.record for f in batch])
                    if to_graph and self.graph is not None:
                        for fact in batch:
                            self.graph.upsert_fact(fact_scope, fact)
                    receipt.accepted += len(batch)
                    receipt.event_ids.extend(f.record.id for f in batch)
            except PartialWriteError:
                raise
            except Exception as exc:
                # Same rule as `write`: by the time a substrate refuses, `vector.upsert` has
                # committed facts, so a bare refusal would deny the caller the receipt.
                raise PartialWriteError(receipt) from exc
        return self._report(scope, scanned, episodes, facts, merged)

    async def aconsolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        vector = self._check_consolidatable(tiers)
        instant = now_epoch_s()
        scanned = [
            r.detached() for r in await vector.ascan(scope, MemoryTier.EPISODIC, limit=self.max_episodes_per_pass)
        ]
        self._assert_scope_isolation(scope, scanned)
        episodes = await self._avisible_episodes(scope, scanned, instant)
        # Off the loop: `_distil` is quadratic and measured seconds of pure CPU at the cap, during
        # which no other coroutine in the process runs.
        facts, merged = await asyncio.to_thread(self._distil, episodes)

        if facts:
            to_vector, to_graph = self._fact_targets(facts)
            receipt = WriteReceipt(accepted=0, event_ids=[], tiers_written=[MemoryTier.SEMANTIC])
            try:
                for fact_scope, batch in self._by_scope(facts):
                    if to_vector:
                        await vector.aupsert(fact_scope, [f.record for f in batch])
                    if to_graph and self.graph is not None:
                        await self._gather_writes([self.graph.aupsert_fact(fact_scope, f) for f in batch], receipt)
                    receipt.accepted += len(batch)
                    receipt.event_ids.extend(f.record.id for f in batch)
            except PartialWriteError:
                raise
            except Exception as exc:
                raise PartialWriteError(receipt) from exc
        return self._report(scope, scanned, episodes, facts, merged)


@register_provider("co_located")
def _build_co_located(config: MemoryProviderConfig) -> MemoryProvider:
    """Substrates arrive through ``config.options`` until SM-V1 (#95) / SM-G1 (#99) register real
    backends, at which point ``config.vector``/``config.graph`` resolve through
    ``egpt.registry.discovery.construct`` and this stays the only construction call site."""
    return CoLocatedMemoryProvider(
        vector=checked_vector(config.options.get("vector_substrate")),
        graph=checked_graph(config.options.get("graph_substrate")),
        config=config,
        max_episodes_per_pass=config.options.get("max_episodes_per_pass", _MAX_EPISODES_PER_PASS),
        max_concurrent_writes=config.options.get("max_concurrent_writes", _MAX_CONCURRENT_WRITES),
    )
