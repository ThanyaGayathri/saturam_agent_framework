"""The two substrate ports behind the single memory seam (SM-C1, implementing SM-R11).

Decision D8 (ADR 0003) makes memory a **neuro-symbolic hybrid**: a vector substrate for fuzzy
associative recall and a temporal knowledge graph for precise, updatable, queryable relations.
Both are first-class; neither is a cache of the other.

Callers never see these. They depend on the one ``MemoryProvider`` seam, which composes both ports.
Splitting the ports underneath is what lets a backend swap per-substrate -- the strength of the
"two separate interfaces" option the spec rejected as the *public* seam, kept where it belongs.

Both are ``runtime_checkable`` **Protocols**, not ABCs, so an existing class can satisfy one
structurally through a thin adapter without inheriting from anything -- notably ``VectorDb``
(``vectordb/base.py``), whose async methods carry the ``async_`` prefix while this seam follows the
``learn`` / ``memory`` ``a``-prefix convention it extends. The adapter bridges the two prefixes.

``egpt/memory/`` is a **services**-layer package (ADR 0001): no concrete backend is imported here
or anywhere under it. pgvector (SM-V1 #95) and the graph engine (SM-G1 #99) are integrations,
resolved by registry key.
"""

from typing import Any, List, Optional, Protocol, Sequence, runtime_checkable

from egpt.memory.tiers import MemoryTier
from egpt.memory.types import FactWrite, MemoryEvent, MemoryQuery, MemoryRecord, Scope

__all__ = ["GraphSubstrate", "VectorSubstrate", "checked_graph", "checked_vector"]


@runtime_checkable
class VectorSubstrate(Protocol):
    """The neural substrate: embedding similarity and full-text, per tenant and tier.

    **Scoping is not advisory, and it is not only ``tenant_id``.** Every method takes a
    :class:`~egpt.memory.types.Scope`, and a backend must apply **every slot the scope sets** as a
    conjunctive predicate: ``tenant_id`` always, plus ``user_id`` / ``agent_id`` / ``team_id`` /
    ``session_id`` whenever they are not ``None``. A ``None`` slot means "any".

    Every slot is spelled out because "tenant predicate" alone leaves four of them to a backend
    author's judgement, and a backend that honours the tenant and ignores the user serves one
    user's memory to another inside the same tenant. SM-C4 (#92) makes this fail-closed at the data
    layer; the provider re-checks the tenant slot as a post-condition, because that one leaks
    across a trust boundary.

    A backend must resolve the tier set exactly as ``MemoryQuery.resolved_tiers`` does. When one
    leg honoured the tier filter and the other did not, the leakage surfaced only in the merged
    result and was hard to trace back (finding F3).
    """

    def upsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        """Insert or replace records by id, within ``scope``.

        **Every record must carry ``scope``**, and a backend should refuse a batch where one does
        not. A mismatch has two defensible readings -- honour the call scope, or honour the
        record's -- and a caller cannot tell which it got. Under the first, a fact derived from one
        user's episodes is filed tenant-wide and every user in the tenant can read it; under the
        second, the call scope is decorative. Batch per scope rather than leaving it to the
        backend.
        """
        ...

    async def aupsert(self, scope: Scope, records: Sequence[MemoryRecord]) -> None:
        """Async variant of :meth:`upsert`."""
        ...

    def search(
        self,
        scope: Scope,
        query: MemoryQuery,
        tier: Optional[MemoryTier] = None,
    ) -> List[MemoryRecord]:
        """Rank records by relevance to ``query.text``.

        ``tier`` narrows to a single tier; when it is ``None`` the backend uses
        ``query.resolved_tiers()``.

        **Return current belief only** (``expired_at IS NULL``), filtered *before* ranking. That
        is a transaction-time property every row carries locally, so both substrates can apply it
        in the same ``WHERE`` clause as the tenant predicate -- and applying ``limit`` before it
        would let a page of superseded rows consume the whole limit.

        **Do not apply ``query.as_of``.** Valid time is enforced once, on the merged set, because it
        is the one predicate that needs *both* legs: under SCD-2 the graph holds a current-belief
        successor row carrying ``t_invalid``, and merging that bound onto the vector leg's stale
        projection of the same id is how an invalidated fact is caught (finding F2). A substrate
        that filtered ``as_of`` itself would withhold exactly the row that carries the evidence.

        Residual, stated rather than mitigated: a page dominated by rows the *graph* has bounded
        can come back short, because the provider drops them after the substrates applied
        ``limit``. Over-fetching by a fixed multiplier does not fix that -- no multiplier is
        correct, and it taxes every read. The fix is for the vector projection not to be stale
        (re-sync on invalidation, or carry the bounds), which is SM-V1 (#95) / SM-G1 (#99) work.
        Recorded in SPEC section 11.3.
        """
        ...

    async def asearch(
        self,
        scope: Scope,
        query: MemoryQuery,
        tier: Optional[MemoryTier] = None,
    ) -> List[MemoryRecord]:
        """Async variant of :meth:`search`."""
        ...

    def scan(self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None) -> List[MemoryRecord]:
        """Enumerate records in insertion order -- no relevance ranking.

        Consolidation needs to walk every episode in a scope, which is not a search. The spec's
        stand-ins expressed this by treating an empty ``query.text`` as "scan all", flagged there
        as a convention for SM-C1 to replace: it hid the operation behind a magic value and gave no
        way to bound the walk.

        Ordering is **ascending ``created_at``, then ``id``**, so a pass processes oldest-first.

        ``limit`` is a bound, not a page: ``limit=0`` returns zero rows, and ``None`` means
        unlimited. There is no cursor here. A resumable walk needs a strict ``(created_at, id)``
        cursor -- ``created_at`` is whole seconds, so one batched ``write()`` puts more rows in a
        single second than a page holds and a time-only cursor never advances -- and a protocol
        with that many failure modes should be designed alongside the scheduler that drives it,
        which is SM-L1's (#122).

        Only the vector substrate carries this. Nothing in SM-C1 enumerates the graph, and
        enumeration over a temporal graph is not obviously one operation (current belief? every
        revision? edges or nodes?) -- SM-G9 (#107) should define it when there is a consumer,
        rather than every graph backend implementing a method to satisfy a Protocol no one calls.
        """
        ...

    async def ascan(self, scope: Scope, tier: MemoryTier, limit: Optional[int] = None) -> List[MemoryRecord]:
        """Async variant of :meth:`scan`."""
        ...


@runtime_checkable
class GraphSubstrate(Protocol):
    """The symbolic substrate: a temporal knowledge graph -- multi-hop, provenance, bi-temporal.

    **This substrate is the authority for validity.** A contradiction is recorded here and nowhere
    else; a vector copy of a derived semantic fact is a projection that is never decided locally
    and never propagated back (ADR 0003 section B.6).

    That authority is a **precondition**, not a free-standing guarantee. The provider's
    point-in-time filter runs on the merged set, so it can only see bounds some leg supplied: with
    this substrate absent or excluded, the stale vector projection carries no bounds and passes the
    filter. The co-located provider therefore refuses an ``as_of`` read when the graph leg is not
    participating. The remaining case -- graph wired, but the row outside its top-``limit`` -- needs
    a bounds-by-id lookup on this port, which is SM-G1's (#99). SPEC section 11.3.

    **Bi-temporal writes are expire-and-reinsert (SCD-2), never in-place mutation.** SM-R10
    section 6 proved in-place wrong: a later fact clobbers an already-set bound, and belief
    evolution stops reconstructing. A production backend delegates to SM-R10's ``upsert_edge`` /
    ``invalidate_edge`` / ``retract_edge``.
    """

    def upsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        """Insert or replace an episode **by id**, within ``scope``.

        The provider stamps ``event.id`` before calling this and passes the same id to the vector
        substrate: one logical episode has one id across both, because that id is the
        cross-substrate dedup key. Two ids surface as two records for one write (finding F1).

        **By id, like ``VectorSubstrate.upsert``**, and stated because "record" is not specific
        enough. An appending backend breaks two things: a ``PartialWriteError`` retry -- which tells
        the caller to reuse the stamped ids precisely so it is idempotent -- duplicates the graph
        row, and the two substrates end up holding different content under one id, where dedup keeps
        the vector copy and makes the graph row unreachable. The provider's batch-level duplicate
        check cannot help; it spans one call, and this is about repeats across calls.
        """
        ...

    async def aupsert_episode(self, scope: Scope, event: MemoryEvent) -> None:
        """Async variant of :meth:`upsert_episode`."""
        ...

    def upsert_fact(self, scope: Scope, fact: FactWrite) -> None:
        """Record a distilled semantic fact, as an edge when it carries a full triple.

        Separate from :meth:`upsert_episode` because a fact is not an episode: it has SPO
        structure, a confidence, and provenance back to the episodes it came from. Writing one
        through the episode path produced a flat pseudo-episode instead of the entity/relation
        shape SM-R7 intends (finding F9).

        A fact whose ``is_triple()`` is false is still storable -- SPO is optional structure over
        the statement text, matching SM-R12's ``SemanticFact``. What a backend does with an
        untripled fact (node-only, or defer) is its own call.
        """
        ...

    async def aupsert_fact(self, scope: Scope, fact: FactWrite) -> None:
        """Async variant of :meth:`upsert_fact`."""
        ...

    def traverse(
        self,
        scope: Scope,
        query: MemoryQuery,
        tier: Optional[MemoryTier] = None,
    ) -> List[MemoryRecord]:
        """Retrieve by graph traversal, returning **current belief only** (``expired_at IS NULL``).

        Current belief is a transaction-time filter and is always applied. Valid-time filtering
        (``query.as_of``) is not applied here -- it belongs to the merged read path (finding F2),
        for the same reason as :meth:`VectorSubstrate.search`.

        Tier resolution matches ``MemoryQuery.resolved_tiers`` exactly (finding F3).
        """
        ...

    async def atraverse(
        self,
        scope: Scope,
        query: MemoryQuery,
        tier: Optional[MemoryTier] = None,
    ) -> List[MemoryRecord]:
        """Async variant of :meth:`traverse`."""
        ...

    def invalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        """Record that an edge's fact became false -- by expire-and-reinsert, never in place.

        Two times, and they are not interchangeable:

        * ``t_invalid`` -- **valid time**: when the fact became false in the world.
        * ``at`` -- **transaction time**: when the system recorded that. Defaults to now.

        The backend expires the live row on the transaction axis (sets ``expired_at = at``) and
        inserts a bounded copy carrying ``t_invalid``. Nothing is deleted and no bound is mutated,
        so two successive invalidations accumulate rows, leave exactly one current belief, and
        keep the original reconstructible.

        **The bounded copy keeps ``edge_id``.** Textbook SCD-2 gives the successor a fresh surrogate
        key, and ``MemoryRecord.superseded_by`` positively implies one -- but the only thing that
        carries this bound onto the vector leg's stale projection is the merge's dedup **by id**. A
        new id means the two never meet, and the invalidated fact comes back as current on an
        ordinary read. A backend needing a surrogate key internally must still project ``id`` as the
        logical edge id.

        Scope note (ADR 0003 section B.6): this governs **semantic contradiction on graph edges**.
        Episodic **row** lifecycle -- SM-R6 re-segmentation supersede and SM-R5 decay archival --
        is row-level ``expired_at`` on the record, not this primitive. Both are append-only and
        invalidate-not-delete; they are different objects. Do not route episode archival here.
        """
        ...

    async def ainvalidate(self, scope: Scope, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        """Async variant of :meth:`invalidate`."""
        ...


def _check(candidate: Any, port: Any, option: str) -> Any:
    """Verify an injected substrate satisfies its port, at construction rather than at first call.

    Both ports are ``runtime_checkable``, so this costs one structural check and turns "the graph
    leg mysteriously returns nothing", or an ``AttributeError`` three layers into a read, into a
    build-time error naming the option and the missing methods. ``None`` passes through -- a
    provider with only one substrate is a supported configuration (a vector-only tenant, SM-C5 #93).
    """
    if candidate is None:
        return None
    if not isinstance(candidate, port):
        expected = sorted(name for name in vars(port) if not name.startswith("_"))
        missing = [name for name in expected if not callable(getattr(candidate, name, None))]
        raise TypeError(
            f"config.options[{option!r}] is a {type(candidate).__name__}, which does not satisfy "
            f"{port.__name__}. Missing or non-callable: {missing or expected}."
        )
    return candidate


def checked_vector(candidate: Any, option: str = "vector_substrate") -> Optional[VectorSubstrate]:
    """``candidate`` if it satisfies :class:`VectorSubstrate`, ``None`` if unset, else ``TypeError``."""
    return _check(candidate, VectorSubstrate, option)


def checked_graph(candidate: Any, option: str = "graph_substrate") -> Optional[GraphSubstrate]:
    """``candidate`` if it satisfies :class:`GraphSubstrate`, ``None`` if unset, else ``TypeError``."""
    return _check(candidate, GraphSubstrate, option)
