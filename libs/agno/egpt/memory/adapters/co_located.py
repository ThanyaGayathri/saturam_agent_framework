"""A ``MemoryProvider`` composing both substrate ports (SM-C1).

Straight-line composition: fan a write out to the substrates the tier routes to, merge two read
legs into one slice, distil episodes into facts. It holds no backend code.

Two post-conditions are re-checked here rather than trusted to a backend, because both fail
silently and one puts another tenant's data into a prompt: tenant isolation and bi-temporal
validity.

Out of scope, deliberately: rank fusion beyond a score sum (SM-H2 #132), a consolidation scheduler
or resumption cursor (SM-L1 #122), **retiring a fact whose source group changed** (SM-L3 #124).
``consolidate`` here is scan -> filter -> distil -> write, and the report names what it does not do.

Every tier decision this file makes -- which substrates a write reaches, whether a graph write is
an episode or a fact, which tier consolidation reads and which it writes -- is read from
:data:`egpt.memory.tiers.TIER_POLICY` (SM-C2 #90). None of it is decided here.
"""

import asyncio
import inspect
from dataclasses import replace
from typing import Any, Awaitable, Dict, List, Optional, Sequence, Set, Tuple
from uuid import NAMESPACE_OID, uuid4, uuid5

from egpt.memory.config import (
    MemoryProviderConfig,
    MemoryProviderConfigError,
    register_provider,
    resolve_substrate,
)
from egpt.memory.provider import MemoryProvider
from egpt.memory.substrates import GraphSubstrate, VectorSubstrate, checked_graph, checked_vector
from egpt.memory.tiers import (
    CONSOLIDATION_SOURCE,
    CONSOLIDATION_TARGET,
    GraphWriteShape,
    MemoryTier,
    graph_shape_for_tier,
    policy_for_tier,
    routes_to,
    substrates_for_tier,
)
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
    require_scope,
    Substrate,
    WriteReceipt,
    consolidation_details,
    fusion_diagnostics,
)
from egpt.memory.validation import assert_scope_isolation, reject_duplicate_ids, reject_foreign_scopes
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

    def tiers(self) -> Dict[MemoryTier, bool]:
        """A tier is served when a wired substrate stores it, so a vector-only provider serves
        three of the four and a graph-only one serves two."""
        return {tier: bool(substrates_for_tier(tier) & self._wired()) for tier in MemoryTier}

    def close(self) -> None:
        """Close whichever substrates hold something. Duck-typed, because the ports do not name it.

        Adding ``close`` to ``VectorSubstrate`` / ``GraphSubstrate`` would make every in-memory
        double implement an empty method to satisfy a Protocol, so a backend that has nothing to
        release simply does not have the attribute.
        """
        failures: List[BaseException] = []
        for substrate in (self.vector, self.graph):
            closer = getattr(substrate, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception as exc:  # noqa: PERF203 - every substrate gets closed regardless
                    failures.append(exc)
        self._raise_close_failure(failures)

    @staticmethod
    def _raise_close_failure(failures: Sequence[BaseException]) -> None:
        """Close everything, then report. Shared so neither half can stop at the first failure.

        Latent while the vector substrate holds nothing, and exactly the kind of latent that stops
        being latent: SM-V1 (#95) brings a second connection pool, and a raising vector closer would
        otherwise leave the graph pool open with nothing to reopen it.
        """
        if failures:
            raise failures[0]

    async def aclose(self) -> None:
        failures: List[BaseException] = []
        for substrate in (self.vector, self.graph):
            closer = getattr(substrate, "aclose", None) or getattr(substrate, "close", None)
            if not callable(closer):
                continue
            try:
                result = closer()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: PERF203 - as above
                failures.append(exc)
        self._raise_close_failure(failures)

    # -- guards ---------------------------------------------------------------

    def _wired(self) -> Set[str]:
        return {name for name, on in (("vector", self.vector), ("graph", self.graph)) if on is not None}

    def _require_writable(self, tier: MemoryTier) -> None:
        """Refuse a write no *wired* substrate would store, rather than returning a success receipt."""
        policy = policy_for_tier(tier)
        routed = policy.substrates
        if not routed:
            raise MemoryOperationError(
                f"Tier {tier.value!r} has no persistent substrate; building it out is {policy.owner}.",
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
        if query.as_of is not None and not wired[Substrate.GRAPH] and routes_to("graph", query.resolved_tiers()):
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
        graph_backed_tiers = routes_to("graph", query.resolved_tiers())
        if wired[Substrate.GRAPH] and graph_backed_tiers and Substrate.GRAPH not in query.substrates:
            reason = "point-in-time " if query.as_of is not None else ""
            raise MemoryOperationError(
                f"The query excludes the graph from a {reason}read, and the graph is the authority "
                "for bi-temporal validity, so the answer would include facts it has recorded as "
                "false.",
                field_name="substrates",
            )

    @staticmethod
    def _graph_backed(records: Sequence[MemoryRecord]) -> List[str]:
        """Ids whose tier routes to the graph -- the only ones it can have an opinion about.

        A procedural read is vector-only per ``TIER_POLICY``, so asking the graph for its bounds is
        a guaranteed-empty round trip, and a network hop in Mode B.
        """
        return [r.id for r in records if routes_to("graph", [r.tier])]

    def _reconcile_bounds(self, scope: Scope, records: Sequence[MemoryRecord]) -> bool:
        """Overwrite each record's bi-temporal bounds with the graph's, in place.

        Without this the read path serves facts the graph has recorded as **false**. The vector
        projection carries no bounds; under SCD-2 the graph's bound-bearing successor row holds the
        *same content* as the live row, so it scores identically and drops off the graph leg's
        top-``limit`` page as soon as the corpus is larger than the page. Nothing then reaches
        ``_merge`` to tighten against, and the stale row passes ``_valid_at`` labelled current
        belief. Measured: at ``limit=3`` over a six-row corpus, every returned row was invalidated.

        One batched call per read, not per record -- see ``GraphSubstrate.bounds_for_ids``.

        Returns whether every record that *could* carry a graph bound now does. Vacuously ``True``
        when none of them routes to the graph -- a procedural slice is fully authoritative without
        a graph call, and reporting it unreconciled would flag a correct answer as unreliable.
        ``False`` means the opposite: rows the graph might have bounded went unchecked.
        """
        askable = self._graph_backed(records)
        if not askable:
            return True  # vacuous: no returned record routes to the graph, so nothing is stale
        if self.graph is None:
            return False
        bounds = self.graph.bounds_for_ids(scope, askable)
        for record in records:
            found = bounds.get(record.id)
            if found is not None:
                record.apply_bounds(found)
        return True

    async def _areconcile_bounds(self, scope: Scope, records: Sequence[MemoryRecord]) -> bool:
        """Async variant of :meth:`_reconcile_bounds`, returning the same flag."""
        askable = self._graph_backed(records)
        if not askable:
            return True
        if self.graph is None:
            return False
        bounds = await self.graph.abounds_for_ids(scope, askable)
        for record in records:
            found = bounds.get(record.id)
            if found is not None:
                record.apply_bounds(found)
        return True

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
        require_scope(scope, method="write")
        stamped, records = self._prepare(scope, events)
        vector_records = [r for r in records if "vector" in substrates_for_tier(r.tier)]
        receipt = self._receipt(stamped)
        try:
            if self.vector is not None and vector_records:
                self.vector.upsert(scope, vector_records)
            if self.graph is not None:
                # Both verbs are attempted, then failures are raised together -- the sync half of
                # the settle-then-propagate rule `_gather_writes` applies. A refusal is collected
                # like any other failure rather than short-circuiting: an `except
                # MemoryOperationError: raise` arm here left the two halves three graph rows apart
                # for one identical failure, which is the divergence this loop exists to prevent.
                episodes, facts = self._graph_batches(stamped, records)
                failures: List[BaseException] = []

                def attempt(write: Any, batch: Sequence[Any]) -> None:
                    """One try/except, shared by both verbs. Written twice, they drift."""
                    if not batch:
                        return
                    try:
                        write(scope, batch)
                    except Exception as exc:
                        failures.append(exc)

                attempt(self.graph.upsert_episodes, episodes)
                attempt(self.graph.upsert_facts, facts)
                self._raise_write_failure(failures, receipt)
        except (PartialWriteError, MemoryOperationError):
            raise  # already shaped by `_raise_write_failure`; re-wrapping buries the cause
        except Exception as exc:
            raise PartialWriteError(receipt) from exc
        return receipt

    async def awrite(self, scope: Scope, events: Sequence[MemoryEvent]) -> WriteReceipt:
        require_scope(scope, method="awrite")
        stamped, records = self._prepare(scope, events)
        vector_records = [r for r in records if "vector" in substrates_for_tier(r.tier)]
        receipt = self._receipt(stamped)
        try:
            if self.vector is not None and vector_records:
                await self.vector.aupsert(scope, vector_records)
            if self.graph is not None:
                # Parity with the sync half comes from *both* attempting every verb, not from
                # `gather` itself: bare `gather` propagates the first exception without cancelling
                # its siblings, so they commit regardless. What `return_exceptions=True` adds is
                # deterministic selection -- a `MemoryOperationError` among the failures must win
                # over a transport error, or the same batch reports "retry me" on one run and
                # "this will never work" on the next -- and it stops sibling exceptions becoming
                # never-retrieved warnings.
                episodes, facts = self._graph_batches(stamped, records)
                writes: List[Awaitable[Any]] = []
                if episodes:
                    writes.append(self.graph.aupsert_episodes(scope, episodes))
                if facts:
                    writes.append(self.graph.aupsert_facts(scope, facts))
                await self._gather_writes(writes, receipt)
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

    @staticmethod
    def _graph_batches(
        stamped: Sequence[MemoryEvent], records: Sequence[MemoryRecord]
    ) -> Tuple[List[MemoryEvent], List[FactWrite]]:
        """Split one write into the graph's two verbs. Routing lives here, once, for both halves.

        A semantic memory is a fact, not an episode: routing it through the episode verb produces a
        flat pseudo-episode rather than the entity/relation shape SM-R7 intends (finding F9).
        """
        episodes: List[MemoryEvent] = []
        facts: List[FactWrite] = []
        for event, record in zip(stamped, records):
            # Shaped by `TIER_POLICY`, not by an `is SEMANTIC` test. The table guarantees a
            # graph shape exactly when the tier routes to the graph, so this one lookup also
            # decides whether the event reaches the graph at all.
            shape = graph_shape_for_tier(event.tier)
            if shape is GraphWriteShape.FACT:
                facts.append(FactWrite(record=record, derived_by="co_located.direct_write"))
            elif shape is GraphWriteShape.EPISODE:
                episodes.append(event)
        return episodes, facts

    def write_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        # Hoisted, because `_prepare`'s guard is inside the per-event loop: an empty batch never
        # reached it, so `write_tier(WORKING, [])` returned a success receipt for a tier that
        # `write_tier(WORKING, [event])` refuses. `seed.write_tier` already guards up front.
        require_scope(scope, method="write_tier")
        self._require_writable(tier)
        return self.write(scope, [replace(event, tier=tier) for event in events])

    async def awrite_tier(self, scope: Scope, tier: MemoryTier, events: Sequence[MemoryEvent]) -> WriteReceipt:
        require_scope(scope, method="awrite_tier")
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
        bounds_reconciled: bool,
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
                # Reported by the reconciliation itself, not inferred from the wiring. Inferring
                # it claimed `True` for a procedural slice, where `_graph_backed` correctly makes
                # no call at all -- the flag would have said a graph vouched for bounds no graph
                # was asked about. `False` means rows the graph might have bounded went unchecked.
                bounds_reconciled=bounds_reconciled,
                instant=instant,
            ),
        )

    def _merged(
        self,
        scope: Scope,
        query: MemoryQuery,
        legs: Sequence[Sequence[MemoryRecord]],
        *,
        method: str,
    ) -> List[MemoryRecord]:
        """Merge the legs and run every post-condition that does not need a backend call.

        ``method`` is required rather than defaulted: both halves call this, and a default would
        make the async one report the sync verb -- which is the same "names the method it delegated
        to" defect the entry guards exist to avoid, surfacing from the post-condition instead.
        """
        merged = _merge(legs)
        assert_scope_isolation(scope, merged, method=method)
        self._assert_requested_tiers(query, merged)
        return merged

    def read(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        require_scope(scope, method="read")
        return self._read(scope, query, method="read")

    def _read(self, scope: Scope, query: MemoryQuery, *, method: str) -> MemorySlice:
        """The read path both entry points share, carrying the verb that entered it.

        ``read_tier`` delegates here rather than to :meth:`read` so a post-condition failure names
        the verb the caller used. Threading it is what keeps the entry guard and the post-condition
        from disagreeing about which method is running.
        """
        self._require_answerable(query)
        instant = query.as_of if query.as_of is not None else now_epoch_s()
        vector_hits: List[MemoryRecord] = []
        graph_hits: List[MemoryRecord] = []
        if self.vector is not None and self._uses(query, Substrate.VECTOR):
            vector_hits = list(self.vector.search(scope, query))
        if self.graph is not None and self._uses(query, Substrate.GRAPH):
            graph_hits = list(self.graph.traverse(scope, query))
        merged = self._merged(scope, query, [vector_hits, graph_hits], method=method)
        reconciled = self._reconcile_bounds(scope, merged)
        return self._slice(scope, query, merged, vector_hits, graph_hits, instant, reconciled)

    async def aread(self, scope: Scope, query: MemoryQuery) -> MemorySlice:
        require_scope(scope, method="aread")
        return await self._aread(scope, query, method="aread")

    async def _aread(self, scope: Scope, query: MemoryQuery, *, method: str) -> MemorySlice:
        """Async variant of :meth:`_read`, carrying the verb the same way."""
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
        merged = self._merged(scope, query, [vector_hits, graph_hits], method=method)
        reconciled = await self._areconcile_bounds(scope, merged)
        return self._slice(scope, query, merged, vector_hits, graph_hits, instant, reconciled)

    @staticmethod
    def _uses(query: MemoryQuery, substrate: Substrate) -> bool:
        """Whether this read consults ``substrate`` at all.

        Two conditions, and the second is the tier axis doing its job: a leg no requested tier
        routes to cannot hold a matching row, so consulting it is a guaranteed-empty round trip --
        a network hop in Mode B, and on a graph-only provider the difference between a procedural
        read that reports the tier unserved and one that quietly asks the graph anyway.
        ``_graph_backed`` already applies this reasoning to the bounds call.
        """
        if query.substrates is not None and substrate not in query.substrates:
            return False
        return routes_to(substrate, query.resolved_tiers())

    def read_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        """Read one tier. A tier this provider does not serve reads **empty**, it is not refused.

        One rule across both adapters and both entry points. Previously the same logical request
        got three answers -- `co_located.read_tier(WORKING)` refused, `seed.read_tier(WORKING)`
        returned `[]`, and `co_located.read(tiers=[WORKING])` returned an empty slice -- so no
        caller could write one handler. Reads report absence; **writes** refuse it, because writing
        somewhere nothing will store it loses data while reading it simply finds nothing.

        The guard is repeated here rather than left to the ``read`` it delegates to, so the
        refusal names the verb the caller used. Every entry point carries its own for that reason;
        the second call is a type check on an object already in hand.
        """
        require_scope(scope, method="read_tier")
        return self._read(scope, replace(query, tiers=[tier]), method="read_tier").records

    async def aread_tier(self, scope: Scope, tier: MemoryTier, query: MemoryQuery) -> List[MemoryRecord]:
        """Async variant of :meth:`read_tier`, with the same read-empty rule."""
        require_scope(scope, method="aread_tier")
        return (await self._aread(scope, replace(query, tiers=[tier]), method="aread_tier")).records

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
        if tiers is not None and set(tiers) != {CONSOLIDATION_SOURCE}:
            raise MemoryOperationError(
                f"consolidate reads the {CONSOLIDATION_SOURCE.value} tier and writes "
                f"{CONSOLIDATION_TARGET.value}, but was asked for {sorted(t.value for t in tiers)}. "
                "Which tier a pass may name is TIER_POLICY's consolidates_from.",
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
                        tier=CONSOLIDATION_TARGET,
                        content=head.content,
                        scope=head.scope,
                        topics=list(head.topics) if head.topics is not None else None,
                        provenance={"source_episode_ids": source_ids},
                        # The earliest evidence, not the head's. A fact claiming to become true
                        # later than the oldest episode it was distilled from answers a
                        # point-in-time read wrongly for the interval in between.
                        t_valid=min((m.t_valid for m in group if m.t_valid is not None), default=None),
                        # ...and the latest, symmetrically, or the fact **outlives every episode it
                        # came from**. `_visible_episodes` keeps an episode valid *at the pass
                        # instant*, so one carrying a future-dated `t_invalid` passes the filter and
                        # used to be minted with no upper bound at all -- unsuppressable afterwards
                        # by any read, at any limit, with any `as_of`. That is the failure
                        # `_visible_episodes` exists to prevent, one step later in the same method.
                        #
                        # Open-ended if *any* member is: a group with one unbounded episode is
                        # still true for as long as that episode is. And since
                        # `min(t_valid) <= m.t_valid < m.t_invalid <= max(t_invalid)` for every
                        # member, this can never build the empty interval the envelope refuses.
                        t_invalid=(
                            None if any(m.t_invalid is None for m in group) else max(m.t_invalid for m in group)  # type: ignore[type-var]
                        ),
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
        """Which substrates the consolidation target tier routes to, per ``TIER_POLICY``.

        ``write`` filters by routing and ``_graph_batches`` drops a tier that does not reach the
        graph; consolidate did neither, so it wrote wherever a substrate happened to be wired.
        Latent while SEMANTIC routes to both -- and silent the moment SM-C2 (#90), which owns that
        table, changes a row.
        """
        routed = substrates_for_tier(CONSOLIDATION_TARGET) if facts else frozenset()
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

    @staticmethod
    def _check_distilled(facts: Sequence[FactWrite]) -> None:
        """The guard ``_prepare`` applies to a write batch, applied to a consolidated one.

        Called **before** either half writes anything, for two reasons. One function both halves
        call cannot drift -- added to the sync path alone, it was missing from the async one a
        review later. And a refusal before the fan-out must propagate bare: inside the write's
        ``try`` it was reshaped into a ``PartialWriteError`` announcing zero committed ids, which
        tells a caller to retry something that never started.

        ``_distil``'s grouping partitions its input, so no two groups can derive one fact id today
        -- but that is a property of the current strategy, which SM-L3 (#124) replaces, and an
        un-enforced invariant one refactor from reopening a duplicate-row hole is worth one call.
        """
        reject_duplicate_ids([fact.record for fact in facts])

    def _graph_fact_batch(self, fact_scope: Scope, batch: Sequence[FactWrite], receipt: WriteReceipt) -> None:
        """The sync counterpart of ``_gather_writes`` for one batch of facts.

        **A batched write reports one failure, not the best of several.** The per-fact loop this
        replaced could collect a transport error *and* a refusal from one batch and let
        ``_raise_write_failure`` pick the refusal as the cause; a single call raises once, so that
        selection has nothing to choose between here. The trade is deliberate -- one round trip per
        scope group was the point -- and the selection still matters on the `write` path, where the
        two verbs fan out and can fail differently
        (`test_a_refusal_among_concurrent_failures_wins_over_a_transport_error`). The consolidate
        test's own "mixed" case was dropped for exactly this reason, which its docstring records.

        Both halves now make **one** call, so they cannot diverge on how many facts they attempted.
        What still has to be shared is how a failure is *shaped*: ``upsert_facts`` is explicitly not
        required to be atomic, so a batch that raises may have committed part of itself. That is a
        partial write whatever else has happened, which is why it is shaped here rather than left to
        ``consolidate``'s ``committed_any`` arm -- that arm would report a bare refusal, and a bare
        refusal denies the caller the ids its retry needs.
        """
        failures: List[BaseException] = []
        try:
            self.graph.upsert_facts(fact_scope, batch)  # type: ignore[union-attr]  # caller checked
        except Exception as exc:
            failures.append(exc)
        self._raise_write_failure(failures, receipt)

    def consolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        require_scope(scope, method="consolidate")
        vector = self._check_consolidatable(tiers)
        instant = now_epoch_s()
        # Detached before anything touches them: `scan` may hand back the rows the substrate is
        # holding (`search` returns copies, `scan` need not), and `_reconcile_bounds` writes bounds
        # onto what it is given.
        scanned = [r.detached() for r in vector.scan(scope, CONSOLIDATION_SOURCE, limit=self.max_episodes_per_pass)]
        assert_scope_isolation(scope, scanned, method="consolidate")
        episodes = self._visible_episodes(scope, scanned, instant)
        facts, merged = self._distil(episodes)

        if facts:
            self._check_distilled(facts)
            to_vector, to_graph = self._fact_targets(facts)
            # Stamped up front, exactly as `write` does. Advancing counters inside the loop left
            # `accepted=0, event_ids=[]` on every single-scope failure -- the ordinary case -- while
            # rows were already committed, and the error text then reads "retry with these ids: []".
            # Counting only committed rows is the other tempting fix and is ruled out by
            # `PartialWriteError`'s own contract: `event_ids` are the ids a retry must reuse.
            receipt = WriteReceipt(
                accepted=len(facts),
                event_ids=[f.record.id for f in facts],
                tiers_written=[CONSOLIDATION_TARGET],
            )
            committed_any = False
            try:
                for fact_scope, batch in self._by_scope(facts):
                    if to_vector:
                        vector.upsert(fact_scope, [f.record for f in batch])
                        committed_any = True
                    if to_graph and self.graph is not None:
                        self._graph_fact_batch(fact_scope, batch, receipt)
                        committed_any = True
            except PartialWriteError:
                raise
            except MemoryOperationError:
                # A refusal on the *first* write, before anything landed, is just a refusal --
                # matching `write`, where a vector refusal precedes any commit and propagates bare.
                # Once something is committed it is a partial write like any other.
                if not committed_any:
                    raise
                raise PartialWriteError(receipt) from None
            except Exception as exc:
                raise PartialWriteError(receipt) from exc
        return self._report(scope, scanned, episodes, facts, merged)

    async def aconsolidate(self, scope: Scope, tiers: Optional[Sequence[MemoryTier]] = None) -> ConsolidationReport:
        require_scope(scope, method="aconsolidate")
        vector = self._check_consolidatable(tiers)
        instant = now_epoch_s()
        scanned = [
            r.detached() for r in await vector.ascan(scope, CONSOLIDATION_SOURCE, limit=self.max_episodes_per_pass)
        ]
        assert_scope_isolation(scope, scanned, method="aconsolidate")
        episodes = await self._avisible_episodes(scope, scanned, instant)
        # Off the loop: `_distil` is quadratic and measured seconds of pure CPU at the cap, during
        # which no other coroutine in the process runs.
        facts, merged = await asyncio.to_thread(self._distil, episodes)

        if facts:
            self._check_distilled(facts)
            to_vector, to_graph = self._fact_targets(facts)
            receipt = WriteReceipt(
                accepted=len(facts),
                event_ids=[f.record.id for f in facts],
                tiers_written=[CONSOLIDATION_TARGET],
            )
            committed_any = False
            try:
                for fact_scope, batch in self._by_scope(facts):
                    if to_vector:
                        await vector.aupsert(fact_scope, [f.record for f in batch])
                        committed_any = True
                    if to_graph and self.graph is not None:
                        await self._gather_writes([self.graph.aupsert_facts(fact_scope, batch)], receipt)
                        committed_any = True
            except PartialWriteError:
                raise
            except MemoryOperationError:
                if not committed_any:
                    raise
                raise PartialWriteError(receipt) from None
            except Exception as exc:
                raise PartialWriteError(receipt) from exc
        return self._report(scope, scanned, episodes, facts, merged)


@register_provider("co_located")
def _build_co_located(config: MemoryProviderConfig) -> MemoryProvider:
    """The only construction call site, for a real backend and an injected double alike.

    ``config.graph`` resolves through ``egpt.registry.discovery.construct`` (SM-G1 #99);
    ``config.vector`` will do the same once SM-V1 (#95) registers a ``vectordb``-backed substrate,
    and takes the identical path today so the two cannot diverge. ``options`` keeps carrying an
    already-built substrate, which is how a test injects a double.
    """
    return CoLocatedMemoryProvider(
        # `vector` has no registry kind yet: no registered `vectordb` satisfies `VectorSubstrate`,
        # whose async methods carry the `a` prefix rather than `VectorDb`'s `async_`. SM-V1 (#95)
        # owns that bridge; until then the field is refused rather than quietly dropped.
        vector=resolve_substrate(
            "vector", None, config.vector, config.options.get("vector_substrate"), "vector_substrate", checked_vector
        ),
        graph=resolve_substrate(
            "graph", "graphdb", config.graph, config.options.get("graph_substrate"), "graph_substrate", checked_graph
        ),
        config=config,
        max_episodes_per_pass=config.options.get("max_episodes_per_pass", _MAX_EPISODES_PER_PASS),
        max_concurrent_writes=config.options.get("max_concurrent_writes", _MAX_CONCURRENT_WRITES),
    )
