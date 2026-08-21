"""Temporal-contiguity retrieval (SM-E8 #115): the +/- n neighbors of a retrieval hit, in a
bounded buffer, ready to blend into ranking (SM-E7's composite, via :mod:`.score`).

EM-LLM (2407.09450, ICLR'25) retrieves hits by k-NN, then feeds each hit through a two-stage
temporal-contiguity buffer that recalls the neighbors of the hit's own position in time --
reproducing the temporal-order recall effect the human free-recall literature measures. This
module is the first stage: given a hit and the ordered set of episodes it was drawn from, name
its +/- n neighbors (:func:`temporal_neighbors`) and accumulate them across a retrieval pass in
a bounded FIFO (:class:`ContiguityBuffer`) -- EM-LLM's own "bounded queue (implicit)"
(``docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md`` table). Turning a buffered neighbor into
a ranking boost is :mod:`.score`'s ``contiguity_bonus``/``blend_contiguity``, the second stage;
wiring either into live retrieval ranking is SM-H5's (#135), the same boundary SM-E7's own module
docstring draws for its composite.

Temporal order here is **valid time** (``occurred_at``, falling back to ``created_at`` when
unset -- the same fallback :func:`~egpt.memory.episodic.decay.episode_decay_score` uses for
``last_accessed_at``, and for the same reason: an episode with no recorded access time is not an
episode with no recorded time), not the vector substrate's ``scan`` order, which is transaction
time (``VectorSubstrate.scan`` docstring: "ascending created_at, then id"). Contiguity means
neighbors *in the story*, and only valid time tells that story consistently across a batched or
replayed write.

Every function here is pure -- no store is read or written, so there are no async twins, the
same construction-only exemption CLAUDE.md gives ``episode_from_event`` and the rest of this
package's scoring modules (:mod:`.decay`, :mod:`.relevance`, :mod:`.score`).

**Not here:** fetching the candidate pool a hit's neighbors are drawn from -- that is a substrate
read (``VectorSubstrate.scan`` or a session-scoped query), and this module takes that pool as a
plain ``Sequence[EpisodicRecord]`` a caller already has in hand, the same boundary
:func:`~egpt.memory.episodic.forget.select_for_forgetting` draws for its own ``episodes``
parameter. Turning a buffered neighbor's signals into a composite score is SM-E7's
``composite_episodic_score`` (:mod:`.score`); blending the contiguity bonus on top of that is
also :mod:`.score`, not this module -- this module only decides *which* episodes are neighbors
and how many a pass may hold onto.
"""

from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from egpt.memory.schemas.episodic import EpisodicRecord
from egpt.memory.types import MemoryOperationError

__all__ = [
    "DEFAULT_CONTIGUITY_BUFFER_CAPACITY",
    "DEFAULT_CONTIGUITY_RADIUS",
    "ContiguityBuffer",
    "ContiguityNeighbor",
    "temporal_neighbors",
    "temporal_order_key",
]

#: EM-LLM's own radius is not restated numerically anywhere reachable (2407.09450 tunes it per
#: benchmark) -- 2 is this module's own default, chosen to match SM-E7's practice of shipping a
#: usable default alongside a fully overridable parameter. A caller wiring this into production
#: (SM-H5) tunes it per tenant like every other threshold in this package (see the
#: ``episodic.forget`` module docstring: "SM-C5 not yet landed").
DEFAULT_CONTIGUITY_RADIUS: int = 2

#: Bounds how many neighbors a single retrieval pass accumulates across every hit it enqueues,
#: not how many any one hit contributes -- EM-LLM's "bounded queue (implicit)".
DEFAULT_CONTIGUITY_BUFFER_CAPACITY: int = 50


def temporal_order_key(episode: EpisodicRecord) -> Tuple[int, str]:
    """``(when it happened, id)`` -- the composite key temporal-contiguity sorts and locates by.

    Falls back to ``created_at`` when ``occurred_at`` is unset, matching
    :func:`~egpt.memory.episodic.decay.episode_decay_score`'s ``last_accessed_at`` fallback: an
    episode always has a recorded transaction time (``TenantScoped.__post_init__`` stamps it), so
    a missing valid time still orders relative to the rest of the batch instead of raising or
    sorting arbitrarily. ``id`` breaks ties the same way ``VectorSubstrate.scan``'s cursor does,
    for the same reason: a batched write can put more than one episode at the same whole-second
    timestamp.

    Raises:
        MemoryOperationError: both ``occurred_at`` and ``created_at`` are unset -- the same
            "neither timestamp" failure :func:`~egpt.memory.episodic.decay.episode_decay_score`
            refuses, for the same reason: there is no time to order the episode by.
    """
    when = episode.occurred_at if episode.occurred_at is not None else episode.created_at
    if when is None:
        raise MemoryOperationError(
            f"Episode {episode.id!r} has neither occurred_at nor created_at set, so it cannot be "
            "placed in temporal order.",
            field_name="created_at",
        )
    return (when, episode.id)


@dataclass(frozen=True)
class ContiguityNeighbor:
    """One episode pulled into the buffer because it neighbors a hit in temporal order.

    ``distance`` is signed: negative is *before* the hit, positive is *after*. Never ``0`` --
    :func:`temporal_neighbors` never treats a hit as its own neighbor. ``hit_id`` names the
    retrieval hit whose lookup produced this neighbor; when the same episode neighbors more than
    one hit in a pass, :class:`ContiguityBuffer` keeps whichever enqueued it first.
    """

    episode: EpisodicRecord
    distance: int
    hit_id: str


def temporal_neighbors(
    hit: EpisodicRecord,
    episodes: Sequence[EpisodicRecord],
    radius: int = DEFAULT_CONTIGUITY_RADIUS,
) -> List[ContiguityNeighbor]:
    """The episodes within ``radius`` positions of ``hit`` in ``episodes``' temporal order.

    ``episodes`` need not already be sorted -- this function sorts its own copy by
    :func:`temporal_order_key` rather than trusting the caller's ordering, the same boundary
    choice :func:`~egpt.memory.episodic.forget.select_for_forgetting` makes for its own
    ``episodes`` parameter: a caller forwarding a substrate ``scan`` page (transaction-time
    order) or an unordered candidate list would otherwise get neighbors that are silently wrong
    rather than refused.

    ``hit`` must appear in ``episodes`` (compared by ``id``), and every episode in ``episodes``
    must carry a distinct ``id`` -- a neighbor is a position relative to the hit *within this
    candidate pool*, so a hit that never made it into the pool has no position to be relative
    to, and a duplicated id makes "the hit's position" ambiguous (it would silently resolve to
    whichever copy sorts last).

    Returns up to ``2 * radius`` neighbors (fewer at either edge of ``episodes``), ordered
    nearest-to-farthest by absolute distance and, at each distance, before-then-after -- the
    order EM-LLM's own buffer fills a hit's neighbors in. Never includes ``hit`` itself.

    Raises:
        MemoryOperationError: ``radius`` is negative (``field_name="radius"``); two or more
            episodes in ``episodes`` share an ``id`` (``field_name="episodes"``); or ``hit.id``
            does not appear in ``episodes`` (``field_name="hit"``).
    """
    if radius < 0:
        raise MemoryOperationError(
            f"radius must be non-negative, got {radius!r}: a negative radius names neighbors on "
            "neither side of the hit, which is not a bound this function defines.",
            field_name="radius",
        )
    if radius == 0:
        # A hit still has to be *in* the pool to have a position -- checked below, at the same
        # cost regardless of radius -- but there is nothing to sort or index for zero neighbors,
        # and building `index_by_id` first would raise on a duplicate id no radius-0 caller asked
        # this function to reconcile.
        if not any(episode.id == hit.id for episode in episodes):
            raise MemoryOperationError(
                f"hit {hit.id!r} does not appear in episodes: a neighbor is a position relative "
                "to the hit within this candidate pool, so a hit missing from the pool has no "
                "position to be relative to.",
                field_name="hit",
            )
        return []
    ordered = sorted(episodes, key=temporal_order_key)
    index_by_id: Dict[str, int] = {}
    for position, episode in enumerate(ordered):
        if episode.id in index_by_id:
            raise MemoryOperationError(
                f"episodes contains two entries with id {episode.id!r}: a neighbor is a position "
                "in this candidate pool, and a duplicated id makes the hit's own position "
                "ambiguous.",
                field_name="episodes",
            )
        index_by_id[episode.id] = position
    if hit.id not in index_by_id:
        raise MemoryOperationError(
            f"hit {hit.id!r} does not appear in episodes: a neighbor is a position relative to "
            "the hit within this candidate pool, so a hit missing from the pool has no position "
            "to be relative to.",
            field_name="hit",
        )
    hit_index = index_by_id[hit.id]
    neighbors: List[ContiguityNeighbor] = []
    for offset in range(1, radius + 1):
        before_index = hit_index - offset
        if before_index >= 0:
            neighbors.append(ContiguityNeighbor(episode=ordered[before_index], distance=-offset, hit_id=hit.id))
        after_index = hit_index + offset
        if after_index < len(ordered):
            neighbors.append(ContiguityNeighbor(episode=ordered[after_index], distance=offset, hit_id=hit.id))
    return neighbors


class ContiguityBuffer:
    """A bounded FIFO of :class:`ContiguityNeighbor`, deduplicated by episode id, accumulated
    across a retrieval pass -- EM-LLM's "bounded queue (implicit)".

    Capacity bounds the whole buffer, not any one hit's contribution: enqueuing past capacity
    evicts the *earliest-enqueued* neighbor still present (FIFO) -- so a buffer that has been
    filling across many hits favors what was pulled in most recently, matching EM-LLM's framing
    of the buffer as recency-biased working context rather than a permanent index.

    A neighbor whose episode id is already buffered is dropped on a later :meth:`enqueue` (the
    first hit that pulled it in, and its distance from that hit, is kept) rather than replacing
    or re-ordering it -- two different hits commonly share a neighbor (radius 2 on adjacent hits
    overlaps), and re-inserting it would evict something else to make room for information the
    buffer already has.
    """

    def __init__(self, capacity: int = DEFAULT_CONTIGUITY_BUFFER_CAPACITY) -> None:
        if capacity <= 0:
            raise MemoryOperationError(
                f"capacity must be positive, got {capacity!r}: a buffer that holds nothing is not "
                "a bound, it is a refusal to buffer at all.",
                field_name="capacity",
            )
        self._capacity = capacity
        self._by_id: "OrderedDict[str, ContiguityNeighbor]" = OrderedDict()

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, episode_id: object) -> bool:
        """Whether ``episode_id`` (an episode's ``.id`` string, e.g. ``"ep-1" in buffer``) is
        buffered. Typed ``object``, matching Python's ``__contains__`` protocol, rather than
        ``str``: a caller holding an ``object``-typed value (or checking membership of something
        that is not a string at all) must get ``False``, not a narrower signature it violates.
        """
        return isinstance(episode_id, str) and episode_id in self._by_id

    def neighbors(self) -> List[ContiguityNeighbor]:
        """Every buffered neighbor, oldest-enqueued first."""
        return list(self._by_id.values())

    def enqueue(self, neighbors: Iterable[ContiguityNeighbor]) -> List[ContiguityNeighbor]:
        """Add ``neighbors``, evicting the oldest entries past ``capacity``.

        Returns the neighbors evicted by this call, oldest-evicted first -- empty when nothing
        was evicted (every id was new and the buffer stayed within capacity), and equally empty
        when every id already appears (a caller re-enqueueing overlapping neighbors from a second
        hit pays no eviction for entries it did not add).
        """
        evicted: List[ContiguityNeighbor] = []
        for neighbor in neighbors:
            if neighbor.episode.id in self._by_id:
                continue
            self._by_id[neighbor.episode.id] = neighbor
            if len(self._by_id) > self._capacity:
                _, dropped = self._by_id.popitem(last=False)
                evicted.append(dropped)
        return evicted
