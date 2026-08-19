"""In-memory substrate doubles for the memory seam tests (SM-C1).

These live in the test tree, not the package. A shipped in-memory substrate would be an
integration (like ``egpt/integrations/db/in_memory/``), issue #89 scopes concrete stores out to
SM-V1/SM-G1, and a non-persistent memory store that is easy to import is a thing someone
eventually runs in production.

They are deliberately faithful on the two behaviours the provider depends on:

* Tenant scoping is real -- records are bucketed by ``scope.tenant_id``, so a cross-tenant leak
  would show up here rather than being assumed away.
* Every scope-taking method calls ``require_scope`` first (SM-C4 #92), because a double must be
  **stricter than the loosest real backend** and the one real substrate in the tree refuses an
  unscoped call at its statement layer. A double that answered one would be modelling a backend
  weaker than any that exists, and ``CoLocatedMemoryProvider.vector`` / ``.graph`` are public, so
  a caller can reach these directly.
* ``FakeGraph.invalidate`` does genuine **expire-and-reinsert (SCD-2)**: it expires the live row
  on the transaction axis and inserts a bounded copy. Nothing is mutated in place, because
  SM-R10 section 6 proved in-place wrong and a double that cheated would hide it.
"""

import asyncio
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from uuid import uuid4

from egpt.memory.graph.episodic import (
    entity_node_id_for,
    normalized_mentions,
    require_recall_filters,
    require_recall_limit,
    require_time_window,
)
from egpt.memory.substrates import GraphSubstrate, VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    SCOPE_SLOTS,
    EntityMerge,
    EntityWrite,
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    ScanCursor,
    Scope,
    Substrate,
    TemporalBounds,
    ValidWindow,
    require_scope,
)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Raw cosine similarity in ``[-1.0, 1.0]``.

    A pure function rather than a numpy dependency: the double's whole point is to be readable
    without one, and two 768-float lists compared per candidate is not where a test suite's time
    goes. Callers matching Neo4j's own vector index must normalise the result themselves -- see
    ``FakeGraph._match_entities``, which does not use this raw value directly.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _yield() -> None:
    """A real suspension point in every ``a*`` double.

    Without one, ``asyncio.gather`` in the provider never actually interleaves and the async paths
    are exercised as thinly disguised sync ones -- which is a contributing reason the ``awrite``
    failure-path divergence went unnoticed for six rounds.
    """
    await asyncio.sleep(0)


def scoped(record_scope: Scope, call_scope: Scope) -> bool:
    """Every slot the call scope sets must match -- not only ``tenant_id``.

    A double that partitions on tenant alone cannot observe a per-user leak, so no test could see
    one. Modelling the full conjunctive predicate is what makes the port's scoping contract
    testable at all.
    """
    for slot in SCOPE_SLOTS:
        wanted = getattr(call_scope, slot)
        if wanted is not None and getattr(record_scope, slot) != wanted:
            return False
    return True


def reject_scope_mismatch(call_scope: Scope, record: MemoryRecord) -> None:
    """A write's records must carry the scope the write was made under.

    The port says a record is stored *within* the call scope, so a record arriving with a different
    one is ambiguous -- store it under the call scope and a per-user fact is filed tenant-wide;
    store it under its own and the call scope was decorative. A real backend resolves that ambiguity
    silently and per-implementation, so the double refuses it instead.

    Not enforcing this is what made a provider bug undetectable: the double bucketed by the call
    scope but filtered on ``record.scope``, so writing a narrow record under a broad scope round-
    tripped correctly here and would have widened it in Postgres.
    """
    if record.scope != call_scope:
        raise ValueError(
            f"Record {record.id!r} carries scope {record.scope.key()} but is being written under "
            f"{call_scope.key()}. Batch per scope; the two must agree."
        )


def current_belief(record: MemoryRecord) -> bool:
    """Transaction time only, per the port: ``expired_at IS NULL``, filtered before ranking.

    Deliberately *not* valid time. A substrate that filtered ``as_of`` would withhold the SCD-2
    successor row carrying ``t_invalid`` -- which is the evidence the provider merges onto the
    vector leg's stale projection to catch an invalidated fact (finding F2).
    """
    return record.expired_at is None


def overlap(query: str, content: str) -> float:
    """Crude lexical similarity, standing in for embeddings. Deterministic by design."""
    q, c = set(query.lower().split()), set(content.lower().split())
    return 0.0 if not q or not c else len(q & c) / len(q | c)


def _ranked(hits: List[MemoryRecord], limit: Optional[int] = None) -> List[MemoryRecord]:
    """Drop non-matches, sort by score, and honour ``limit`` -- as a real backend does.

    Three details that a lazier double got wrong, each of which hid a provider bug:

    * **``limit`` is applied here.** Both in-repo doubles ignored it, which is the structural
      reason the post-fusion filter could return zero records while five valid ones existed and no
      test could observe it. A double more permissive than every real backend cannot catch a bug
      that only appears when the backend is strict.
    * **Non-matches are dropped on ``> 0``, explicitly.** Zero overlap means no match, which is
      what a search excludes -- but ``if hit.score`` would also discard a *deliberate* 0.0 and read
      as an accident.
    * **Ties keep insertion order.** Python's sort is stable, so equal scores come back in write
      order, which is what a real ranking supplies. Breaking ties on ``id`` would order by UUID and
      make the double's output differ run to run.
    """
    matches = [hit for hit in hits if hit.score is not None and hit.score > 0.0]
    ranked = sorted(matches, key=lambda r: -(r.score or 0.0))
    # `limit=0` returns nothing; only `None` means unlimited. See the note in `FakeVector.scan`.
    return ranked if limit is None else ranked[:limit]


class FakeVector:
    """An in-memory VectorSubstrate keyed by tenant."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, MemoryRecord]] = {}
        self.calls: List[str] = []
        #: Every ``limit`` this substrate was *asked* for, in order. Asserting on the request
        #: rather than the response is what makes an over-fetch or a batch bound testable at all:
        #: with `limit=0` the result is empty whatever was requested, so a result-only assertion
        #: is vacuous.
        self.search_limits: List[Optional[int]] = []
        self.scan_limits: List[Optional[int]] = []
        #: Every ``after`` cursor this substrate was *asked* for, in order -- same rationale as
        #: ``scan_limits``: a provider that drops the cursor still returns the right rows on a
        #: corpus small enough to fit one pass, so only the request distinguishes resuming from
        #: rescanning.
        self.scan_afters: List[Optional[ScanCursor]] = []
        #: Every valid-time window this substrate was *asked* to narrow to, in order. Recorded for
        #: the same reason as ``search_limits``: a provider that dropped the window on the way down
        #: still returns the right rows on a corpus that happens to sit inside it, so only the
        #: request distinguishes filtering in-search from filtering afterwards.
        self.search_windows: List[Optional[ValidWindow]] = []

    def _bucket(self, scope: Scope) -> Dict[str, MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, {})

    def _matching(self, scope: Scope) -> List[MemoryRecord]:
        """Rows this scope may see -- every set slot, not just the tenant."""
        return [r for r in self._bucket(scope).values() if scoped(r.scope, scope)]

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="upsert")
        self.calls.append("upsert")
        bucket = self._bucket(scope)
        for record in records:
            reject_scope_mismatch(scope, record)
            bucket[record.id] = replace(record, substrate=Substrate.VECTOR)

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        require_scope(scope, method="aupsert")
        await _yield()
        self.calls.append("aupsert")
        self.upsert(scope, records)

    def search(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        require_scope(scope, method="search")
        self.calls.append("search")
        self.search_limits.append(query.limit)
        window = query.window()
        self.search_windows.append(window)
        tiers = {tier} if tier else set(query.resolved_tiers())
        hits = [
            replace(record, score=overlap(query.text, record.content))
            for record in self._matching(scope)
            # Validity and the window filtered BEFORE ranking, as the port requires: applying
            # `limit` first and either second lets a page of excluded rows consume the whole limit.
            if record.tier in tiers
            and current_belief(record)
            and (window is None or window.overlaps(record.t_valid, record.t_invalid))
        ]
        return _ranked(hits, query.limit)

    async def asearch(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        require_scope(scope, method="asearch")
        await _yield()
        self.calls.append("asearch")
        return self.search(scope, query, tier)

    def scan(
        self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None, after: Optional[ScanCursor] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="scan")
        self.calls.append("scan")
        self.scan_limits.append(limit)
        self.scan_afters.append(after)
        # Copies, like `search` returns. `scan` handing back live objects made a provider that
        # mutates what it scanned unobservable -- and the consolidate path did exactly that.
        rows = [replace(r) for r in self._matching(scope) if r.tier is tier]
        rows.sort(key=lambda r: ((r.created_at or 0), r.id))
        if after is not None:
            # Strict composite bound, matching pgvector's row comparison -- a same-second sibling
            # at the cursor's exact `id` is excluded, everything past it is not.
            rows = [r for r in rows if ((r.created_at or 0), r.id) > after]
        # `limit=0` means zero rows, not "unlimited". Every real backend distinguishes `LIMIT 0`
        # from an omitted limit, and conflating them here would hide a provider that fetches
        # everything when it asked for nothing.
        return rows if limit is None else rows[:limit]

    async def ascan(
        self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None, after: Optional[ScanCursor] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="ascan")
        await _yield()
        self.calls.append("ascan")
        return self.scan(scope, tier, limit, after)


class FakeGraph:
    """An in-memory GraphSubstrate with real SCD-2 invalidation.

    Rows accumulate: an invalidation never deletes and never edits a bound, so the full belief
    history stays reconstructible and ``traverse`` filters to current belief.
    """

    def __init__(self) -> None:
        self.rows: Dict[str, List[MemoryRecord]] = {}
        self.facts: List[FactWrite] = []
        self.calls: List[str] = []
        self.traverse_limits: List[Optional[int]] = []
        #: Every id set the provider asked bounds for -- asserting on the *request* is what makes a
        #: missing reconciliation visible, since a result-only assertion cannot see one.
        self.bounds_requests: List[List[str]] = []
        #: One entry per write call. A result-only assertion cannot tell one batched round trip
        #: from `len(events)` of them, which is the property the batched verbs exist for.
        self.episode_batch_sizes: List[int] = []
        self.fact_batch_sizes: List[int] = []
        #: Every recall the substrate was *asked* for. With `limit=0` the result is empty whatever
        #: the filters were, so a result-only assertion cannot tell a honoured filter from a
        #: dropped one.
        self.recall_requests: List[Dict[str, Any]] = []
        self.entity_batch_sizes: List[int] = []
        #: Keyed on ``(scope.key(), name)``, which is the identity the Neo4j backend MERGEs on --
        #: keying on the normalised ``key`` instead would model a backend that had already done
        #: SM-G8's (#106) resolution, and no test could then see that it has not happened.
        self.entities: Dict[Tuple[str, str], EntityWrite] = {}

    def _bucket(self, scope: Scope) -> List[MemoryRecord]:
        return self.rows.setdefault(scope.tenant_id, [])

    def _matching(self, scope: Scope) -> List[MemoryRecord]:
        return [r for r in self._bucket(scope) if scoped(r.scope, scope)]

    def upsert_episodes(self, scope: Scope, events: Sequence[MemoryEvent]) -> None:
        """Upsert by id, as the port requires. Appending let a repeat write look idempotent."""
        require_scope(scope, method="upsert_episodes")
        self.calls.append("upsert_episodes")
        self.episode_batch_sizes.append(len(events))
        for event in events:
            self._upsert_episode(scope, event)

    def _upsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        # Never `event.id or ""`: `types.py` rejects an empty id precisely because it is the
        # dedup key, and this file is copied as reference by anyone building a double. The
        # provider always stamps one, so this branch is unreachable in production -- which is
        # exactly why it has to be right here rather than relied on there.
        episode_id = event.id or str(uuid4())
        row = MemoryRecord(
            id=episode_id,
            tier=event.tier,
            content=event.content,
            scope=scope,
            substrate=Substrate.GRAPH,
            topics=list(event.topics) if event.topics is not None else None,
            # Replaced with the episode, never unioned into it: the real backend clears the
            # `MENTIONS` edges before relinking, so a double that accumulated them would let a
            # dropped entity keep passing every mention filter.
            provenance={
                "entity_ids": [
                    entity_node_id_for(scope, name)
                    for name in normalized_mentions(event.entities, episode_id=episode_id)
                ]
            },
            t_valid=event.occurred_at,
            created_at=event.occurred_at,
        )
        bucket = self._bucket(scope)
        for index, existing in enumerate(bucket):
            # `existing.scope`, not only `existing.id`: `_bucket` keys on `tenant_id` alone, so two
            # users of one tenant share a bucket, and an id collision between them (unlikely with
            # uuid4 stamping, but valid per the port -- `upsert_episodes` is by id, not by (id,
            # scope)) silently overwrote the first with the second. Neo4j's MERGE key is
            # `(scope_key, id, LIVE)`; a double weaker than that is a double every provider test
            # written against it would pass on a bug the engine does not have (round-1 review, PR
            # #90).
            if existing.id == row.id and existing.scope == row.scope and existing.is_current_belief():
                bucket[index] = row
                return
        bucket.append(row)

    async def aupsert_episodes(self, scope: Scope, events: Sequence[MemoryEvent]) -> None:
        require_scope(scope, method="aupsert_episodes")
        # Suspends, then delegates to the sync name. The delegation is load-bearing for tests that
        # patch one verb to fail: patching only the sync name and having the async half reimplement
        # it is how the two halves get compared against different code.
        await _yield()
        self.calls.append("aupsert_episodes")
        self.upsert_episodes(scope, events)

    def upsert_facts(self, scope: Scope, facts: Sequence[FactWrite]) -> None:
        """Upsert by id, as the port name says -- appending would let a re-run look idempotent."""
        require_scope(scope, method="upsert_facts")
        self.calls.append("upsert_facts")
        self.fact_batch_sizes.append(len(facts))
        for fact in facts:
            self._upsert_fact(scope, fact)

    def _upsert_fact(self, scope: Scope, fact: FactWrite) -> None:
        reject_scope_mismatch(scope, fact.record)
        self.facts.append(fact)
        bucket = self._bucket(scope)
        row = replace(fact.record, substrate=Substrate.GRAPH)
        for index, existing in enumerate(bucket):
            # Same class of defect as `_upsert_episode`'s dedup, and the same fix: `existing.scope`
            # must join `existing.id`, or two users' facts sharing an id collapse into one inside
            # the shared per-tenant bucket.
            if existing.id == row.id and existing.scope == row.scope and existing.is_current_belief():
                bucket[index] = row
                return
        bucket.append(row)

    async def aupsert_facts(self, scope: Scope, facts: Sequence[FactWrite]) -> None:
        require_scope(scope, method="aupsert_facts")
        await _yield()
        self.calls.append("aupsert_facts")
        self.upsert_facts(scope, facts)

    def upsert_entities(self, scope: Scope, entities: Sequence[EntityWrite]) -> None:
        """Merge by ``(scope, name)``, unioning provenance and aliases as the port requires."""
        require_scope(scope, method="upsert_entities")
        self.calls.append("upsert_entities")
        self.entity_batch_sizes.append(len(entities))
        for entity in entities:
            self._upsert_entity(scope, entity)

    def _upsert_entity(self, scope: Scope, entity: EntityWrite) -> None:
        if entity.scope != scope:
            # `MemoryOperationError`, matching `Neo4jGraphSubstrate._reject_foreign_scope`: a
            # caller catching that type specifically would not be exercised against a double that
            # raised a bare `ValueError` for the same refusal.
            raise MemoryOperationError(
                f"Entity {entity.name!r} carries scope {entity.scope.key()} but is being written "
                f"under {scope.key()}. Batch per scope; the two must agree.",
                field_name="scope",
            )
        stored = self.entities.get((scope.key(), entity.name))
        # The entity's own surface form is never its own alias, matching
        # `Neo4jGraphSubstrate._entity_rows` -- carrying it in both places would make a candidate
        # look like it was seen under two spellings when it was seen under one.
        new_aliases = [alias for alias in entity.aliases if alias != entity.name]
        # Ordered `[stored not already in new] + new`, matching `cypher._union` exactly (review
        # finding on PR #89, round 2) rather than `new + stored`. `EntityWrite.__post_init__`
        # `sorted(set(...))`s both fields on every construction, including this one, so the two
        # orderings produce byte-identical output today -- this is matching the real statement for
        # its own sake, in case a caller ever reads pre-`EntityWrite` intermediate state or the
        # dedup ever stops being unconditional.
        stored_aliases = [a for a in (stored.aliases if stored else []) if a not in new_aliases]
        new_episode_ids = list(entity.source_episode_ids)
        stored_episode_ids = [e for e in (stored.source_episode_ids if stored else []) if e not in new_episode_ids]
        # A fresh instance rather than a mutation: the caller keeps its own ``EntityWrite``, and a
        # double that stored the object handed to it would let a later mutation rewrite history --
        # the "return copies" rule, applied on the write side where the aliasing starts.
        # Review finding on PR #96 (round 3): an incoming entity with no embedding must not erase
        # one the node already has -- matching `Neo4jGraphSubstrate._entity_rows`'s conditional
        # omit, which round 1's finding on this same PR fixed on the real backend. A double that
        # still erased it here would model a backend weaker than the one it stands in for, which
        # is the one thing this module's own docstring says a double must never do. Reachable both
        # through a plain `upsert_entities` re-write and through `merge_entities`' canonical write
        # (whose `EntityWrite` never carries an embedding when it came from `_merge_for`'s match
        # leg -- see `EntityResolutionPipeline._canonical`).
        stored_embedding = list(stored.embedding) if stored and stored.embedding is not None else None
        embedding = list(entity.embedding) if entity.embedding is not None else stored_embedding
        embedding_version = (
            entity.embedding_version if entity.embedding is not None else (stored.embedding_version if stored else None)
        )
        self.entities[(scope.key(), entity.name)] = EntityWrite(
            name=entity.name,
            entity_type=entity.entity_type,
            scope=entity.scope,
            description=entity.description,
            aliases=stored_aliases + new_aliases,
            properties=dict(entity.properties),
            source_episode_ids=stored_episode_ids + new_episode_ids,
            embedding=embedding,
            embedding_version=embedding_version,
            confidence=entity.confidence,
            derived_by=entity.derived_by,
        )

    async def aupsert_entities(self, scope: Scope, entities: Sequence[EntityWrite]) -> None:
        require_scope(scope, method="aupsert_entities")
        await _yield()
        self.calls.append("aupsert_entities")
        # Not `self.upsert_entities(...)`: that appends `"upsert_entities"` too, so one async call
        # would leave both verbs in `self.calls` (review finding on PR #89, round 3). The per-entity
        # write logic is called directly instead, matching what the sync method does internally.
        self.entity_batch_sizes.append(len(entities))
        for entity in entities:
            self._upsert_entity(scope, entity)

    # -- entity resolution (SM-G8 #106) ----------------------------------------

    def _match_entities(
        self, scope: Scope, candidate: EntityWrite, limit: int, min_score: float
    ) -> List[Tuple[EntityWrite, float]]:
        if candidate.embedding is None:
            raise MemoryOperationError(
                f"match_entities was given {candidate.name!r} with no embedding. The similarity leg "
                "has nothing to compare against, and skipping it silently would report a resolution "
                "pass that checked nothing as one that checked everything.",
                field_name="embedding",
            )
        best: Dict[str, float] = {}
        for (scope_key, name), stored in self.entities.items():
            if scope_key != scope.key() or name == candidate.name:
                continue
            score = None
            if stored.key == candidate.key:
                score = 1.0
            elif stored.embedding is not None:
                # `(1 + cosine) / 2`, matching Neo4j's own vector index normalisation exactly --
                # not a clamp of raw cosine. A double scoring orthogonal vectors 0.0 where the real
                # backend scores them 0.5 would accept and refuse a different set of candidates than
                # the engine it stands in for, which is the one thing a double must never do.
                similarity = (1.0 + _cosine(candidate.embedding, stored.embedding)) / 2.0
                if similarity >= min_score:
                    score = similarity
            if score is not None and score > best.get(name, -1.0):
                best[name] = score
        ordered = sorted(best, key=lambda name: (-best[name], name))[:limit]
        # Copies, not the stored objects themselves: `Neo4jGraphSubstrate._entity_from_row` always
        # constructs a fresh `EntityWrite` from what the engine returned, so a caller mutating a
        # match result (via `dataclasses.replace`, say) can never reach this dict's own values --
        # the "return copies" rule this module states in its own docstring, applied on the read
        # side where a match is the first place a caller gets to hold one.
        return [(replace(self.entities[(scope.key(), name)]), best[name]) for name in ordered]

    def match_entities(
        self, scope: Scope, candidate: EntityWrite, limit: int = 5, min_score: float = 0.92
    ) -> List[Tuple[EntityWrite, float]]:
        require_scope(scope, method="match_entities")
        self.calls.append("match_entities")
        return self._match_entities(scope, candidate, limit, min_score)

    async def amatch_entities(
        self, scope: Scope, candidate: EntityWrite, limit: int = 5, min_score: float = 0.92
    ) -> List[Tuple[EntityWrite, float]]:
        require_scope(scope, method="amatch_entities")
        await _yield()
        self.calls.append("amatch_entities")
        return self._match_entities(scope, candidate, limit, min_score)

    @staticmethod
    def _reject_overlapping_merges(merges: Sequence[EntityMerge]) -> None:
        """Refuse a batch where one merge's canonical is another's duplicate, or the same
        duplicate name appears in two merges -- matching ``Neo4jGraphSubstrate``'s guard exactly
        (review finding on PR #96, round 3: this double had neither check, so a batch Neo4j
        refuses was silently accepted here, corrupting state a caller relying on the double for
        tests would never see coming)."""
        canonical_names = {merge.canonical.name for merge in merges}
        duplicate_names = {name for merge in merges for name in merge.duplicate_names}
        overlap = canonical_names & duplicate_names
        if overlap:
            raise MemoryOperationError(
                f"merge_entities was given a batch where {sorted(overlap)} names both a canonical "
                "entity in one merge and a duplicate in another. Resolving that requires deciding "
                "an order between the two merges, which this batch does not state -- split it into "
                "two calls, ordered.",
                field_name="merges",
            )
        seen_duplicates: Set[str] = set()
        for merge in merges:
            for name in merge.duplicate_names:
                if name in seen_duplicates:
                    raise MemoryOperationError(
                        f"merge_entities was given a batch where {name!r} is a duplicate in more "
                        "than one merge. Each name can fold into exactly one canonical per batch; "
                        "split it into two calls, ordered.",
                        field_name="merges",
                    )
                seen_duplicates.add(name)

    def _merge_entities(self, scope: Scope, merges: Sequence[EntityMerge]) -> None:
        self._reject_overlapping_merges(merges)
        canonical_by_duplicate: Dict[str, str] = {}
        for merge in merges:
            if merge.canonical.scope != scope:
                raise MemoryOperationError(
                    f"EntityMerge.canonical {merge.canonical.name!r} carries scope "
                    f"{merge.canonical.scope.key()} but is being merged under {scope.key()}. Batch "
                    "per scope; the two must agree.",
                    field_name="scope",
                )
            self._upsert_entity(scope, merge.canonical)
            for name in merge.duplicate_names:
                canonical_by_duplicate[name] = merge.canonical.name
        if not canonical_by_duplicate:
            return
        # Node-id lookup built once, not re-derived per linked id per row: `entity_node_id_for` is
        # pure in `(scope, name)`, so computing it |duplicate_names| times up front makes the
        # re-point below O(ids) instead of O(ids * duplicates).
        id_for_duplicate = {
            entity_node_id_for(scope, duplicate_name): entity_node_id_for(scope, canonical_name)
            for duplicate_name, canonical_name in canonical_by_duplicate.items()
        }
        # Re-point every episode's mention edges from a duplicate's node id to the canonical's,
        # matching `link_mentions_statement`'s MERGE semantics: a row already mentioning the
        # canonical is not duplicated.
        for row in self._bucket(scope):
            if row.scope != scope or row.tier is not MemoryTier.EPISODIC:
                continue
            linked = list(row.provenance.get("entity_ids") or [])
            if not linked:
                continue
            repointed = []
            seen = set()
            for entity_id in linked:
                new_id = id_for_duplicate.get(entity_id, entity_id)
                if new_id not in seen:
                    seen.add(new_id)
                    repointed.append(new_id)
            if repointed != linked:
                row.provenance = {**row.provenance, "entity_ids": repointed}
        for duplicate_name in canonical_by_duplicate:
            self.entities.pop((scope.key(), duplicate_name), None)

    def merge_entities(self, scope: Scope, merges: Sequence[EntityMerge]) -> None:
        require_scope(scope, method="merge_entities")
        self.calls.append("merge_entities")
        if not merges:
            return
        self._merge_entities(scope, merges)

    async def amerge_entities(self, scope: Scope, merges: Sequence[EntityMerge]) -> None:
        require_scope(scope, method="amerge_entities")
        await _yield()
        self.calls.append("amerge_entities")
        if not merges:
            return
        self._merge_entities(scope, merges)

    def traverse(self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None) -> List[MemoryRecord]:
        require_scope(scope, method="traverse")
        self.calls.append("traverse")
        self.traverse_limits.append(query.limit)
        tiers = {tier} if tier else set(query.resolved_tiers())
        hits = [
            replace(row, score=overlap(query.text, row.content))
            for row in self._matching(scope)
            if row.tier in tiers and current_belief(row)  # transaction time, before ranking
        ]
        return _ranked(hits, query.limit)

    async def atraverse(
        self, scope: Scope, query: MemoryQuery, tier: Optional[MemoryTier] = None
    ) -> List[MemoryRecord]:
        require_scope(scope, method="atraverse")
        await _yield()
        self.calls.append("atraverse")
        return self.traverse(scope, query, tier)

    def recall_episodes(
        self,
        scope: Scope,
        entities: Optional[Sequence[str]] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        """Episodes by half-open valid-time window and mention link, oldest first (SM-G6 #104).

        The guards come from ``memory.graph.episodic``, not from a copy here: a double that refused
        a different set of requests from the real backend is a double the provider tests would agree
        with and the engine would not.

        Two things this deliberately does the awkward way, because the loose version passes:

        * an entity is matched by deriving its id under **the row's own scope**, not the call
          scope. A read that leaves ``user_id`` unset spans several write scopes, and deriving under
          the caller's would match none of them -- while the real backend, which reaches the entity
          through the episode's edge, matches all of them.
        * a ``None`` ``t_valid`` fails **either** bound, matching Cypher's three-valued comparison.
          Treating it as 0 or as unbounded would make the double agree with a backend that does not
          exist.
        """
        require_scope(scope, method="recall_episodes")
        require_recall_filters(entities, since, until)
        require_time_window(since, until)
        require_recall_limit(limit)
        self.calls.append("recall_episodes")
        self.recall_requests.append({"entities": entities, "since": since, "until": until, "limit": limit})

        wanted = None if entities is None else set(entities)
        hits = []
        for row in self._matching(scope):
            if row.tier is not MemoryTier.EPISODIC or not current_belief(row):
                continue
            if since is not None and (row.t_valid is None or row.t_valid < since):
                continue
            if until is not None and (row.t_valid is None or row.t_valid >= until):
                continue
            if wanted is not None:
                linked = set(row.provenance.get("entity_ids") or [])
                if not any(entity_node_id_for(row.scope, name) in linked for name in wanted):
                    continue
            hits.append(replace(row))
        # `t_valid is None` last, as Neo4j orders NULL last on ASC. Never a uuid tiebreak before
        # content, so identical episodes do not reorder between runs.
        hits.sort(key=lambda r: (r.t_valid is None, r.t_valid or 0, r.created_at or 0, r.content, r.id))
        # `limit=0` means zero rows, not "unlimited" -- the distinction every real backend makes.
        return hits if limit is None else hits[:limit]

    async def arecall_episodes(
        self,
        scope: Scope,
        entities: Optional[Sequence[str]] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        require_scope(scope, method="arecall_episodes")
        await _yield()
        self.calls.append("arecall_episodes")
        return self.recall_episodes(scope, entities, since, until, limit)

    def bounds_for_ids(self, scope: Scope, ids: Sequence[str]) -> Dict[str, TemporalBounds]:
        """Unranked, unlimited lookup by id -- the whole point is to see what ranking hid.

        Reads the same rows ``traverse`` does but ignores score, ``limit``, and current belief, and
        reports the **tightest** bound across every revision of an id. Reporting only the current
        row would miss nothing today, but a double that answered from the ranked page would make
        the provider's reconciliation untestable, which is the failure this method exists to catch.
        """
        require_scope(scope, method="bounds_for_ids")
        self.calls.append("bounds_for_ids")
        self.bounds_requests.append(list(ids))
        wanted = set(ids)
        found: Dict[str, TemporalBounds] = {}
        for row in self._matching(scope):
            if row.id not in wanted:
                continue
            seen = found.get(row.id)
            if seen is None:
                found[row.id] = TemporalBounds(row.t_valid, row.t_invalid, row.expired_at, row.superseded_by)
                continue
            # `filter(None, ...)` would drop 0 as well as None -- epoch 0 is a legitimate bound,
            # and dropping the tighter one leaves a false fact current.
            candidates = [b for b in (seen.t_invalid, row.t_invalid) if b is not None]
            tightest = min(candidates) if candidates else None
            # `expired_at=None` wins: the live revision is the one that decides current belief.
            expired = None if (seen.expired_at is None or row.expired_at is None) else seen.expired_at
            found[row.id] = TemporalBounds(seen.t_valid, tightest, expired, seen.superseded_by or row.superseded_by)
        return found

    async def abounds_for_ids(self, scope: Scope, ids: Sequence[str]) -> Dict[str, TemporalBounds]:
        require_scope(scope, method="abounds_for_ids")
        await _yield()
        self.calls.append("abounds_for_ids")
        return self.bounds_for_ids(scope, ids)

    def invalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        """Expire the live row on the transaction axis, then insert a bounded copy."""
        require_scope(scope, method="invalidate")
        self.calls.append("invalidate")
        recorded_at = at if at is not None else t_invalid
        bucket = self._bucket(scope)
        for row in list(bucket):
            # Scope-filtered, so an invalidation issued under the wrong scope is observable.
            if row.id != edge_id or not row.is_current_belief() or not scoped(row.scope, scope):
                continue
            row.expired_at = recorded_at  # transaction time out -- not a valid-time edit
            tightest = t_invalid if row.t_invalid is None else min(row.t_invalid, t_invalid)
            bucket.append(replace(row, t_invalid=tightest, expired_at=None, created_at=recorded_at))

    async def ainvalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        require_scope(scope, method="ainvalidate")
        await _yield()
        self.calls.append("ainvalidate")
        self.invalidate(scope, edge_id, t_invalid, at)


def assert_ports_satisfied() -> None:
    """The doubles must satisfy the real ports, or these tests prove nothing about backends."""
    assert isinstance(FakeVector(), VectorSubstrate)
    assert isinstance(FakeGraph(), GraphSubstrate)
