"""Reciprocal Rank Fusion over the orchestrator's legs (SM-H2 #132).

``egpt.memory.adapters.co_located._merge`` sums scores across the vector and graph legs and
refuses the lexical one -- ADR 0003 section A.6 rules that out as an unmeasured default for the
2-leg (dense+BM25) pairing, let alone the 3-leg (+graph) one SM-R8 never measured. RRF sidesteps
that: it reads each leg's *rank*, not its score, so it never needs the legs' scales to agree in
the first place. That is what makes it safe to fuse all three legs -- vector, lexical, graph --
starting here, with no measurement precondition to satisfy.

**This does not make RRF the provider's default.** ADR 0003 A.6 directs SM-H2/H3 to measure 3-leg
weighted-vs-RRF against a real graph retriever before either is ratified -- this module discharges
the *wiring* half of that: both strategies exist, are N-leg-capable, and are opt-in via
``MemoryProviderConfig(fusion={"method": ..., "k": ...})``, so the eventual measurement has
something real to run against. It does not discharge the *measurement* itself, which A.6's own
follow-up ruling (the "Related, and now MIS-owned" note) assigns specifically for the vector+graph
pairing to SM-X7 (#143) -- superseding an earlier, unworkable assignment to SM-R13. Until that
measurement lands, ``score_sum`` stays ``CoLocatedMemoryProvider``'s default.
"""

from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Hashable,
    List,
    Mapping,
    Sequence,
    Set,
    Tuple,
    TypeVar,
)

from egpt.memory.types import MemoryRecord

__all__ = [
    "DEFAULT_RRF_K",
    "FusedItem",
    "deterministic_rank_key",
    "rrf_fuse",
    "rrf_rank",
]

T = TypeVar("T")

#: SM-R8's ~60, per the issue and ADR 0003 A.6 (``fusion.RRFFusion(k=60)``). Large enough that one
#: leg's rank-1 hit does not drown out a hit near the top of every other leg.
DEFAULT_RRF_K = 60


def deterministic_rank_key(record: MemoryRecord) -> Tuple[float, str, str, str]:
    """The tie-break every fusion strategy in this package sorts by.

    Shared rather than duplicated per strategy (``_merge`` and :func:`rrf_fuse` both use it) --
    two independently written tie-breaks are two chances for one of them to fall back to arrival
    order, which is driven by UUID record ids and made identical corpora rank differently across
    processes.
    """
    return (-(record.score or 0.0), record.content, record.tier.value, record.id)


@dataclass(frozen=True)
class FusedItem(Generic[T]):
    """One item after fusion: what it was, what RRF scored it, and how many legs had it.

    ``legs`` is carried because it is the one thing a fused score cannot be read backwards
    into. A hit at rank 3 of one leg and a hit at rank 3 of four legs are different findings
    -- the second is corroborated -- and a caller wanting to say so on screen has no way to
    recover the count from the score alone.
    """

    item: T
    score: float
    legs: int
    #: The names of the legs that had it, in fan-out order. The count alone cannot say *which*
    #: stores agreed, and "the prose and the ontology both say this" is a different claim on a
    #: screen from "two legs said this".
    sources: Tuple[str, ...] = ()


def rrf_rank(
    legs: Mapping[str, Sequence[T]],
    *,
    identity: Callable[[T], Hashable],
    tie_break: Callable[[T], Tuple[Any, ...]] = lambda item: (),
    k: int = DEFAULT_RRF_K,
) -> List["FusedItem[T]"]:
    """Reciprocal Rank Fusion over ranked legs of **anything**: ``RRF(d) = sum_r 1/(k + rank_r(d))``.

    The generic core of :func:`rrf_fuse`, extracted so the same ranking serves items that are
    not :class:`~egpt.memory.types.MemoryRecord`. That was the gap: this package's RRF was the
    only rank-based fusion in the codebase and it was typed to one store's record class, so
    every other retrieval path -- the knowledge base above all -- either re-implemented fusion
    or, as ``unified_read`` did, declined to rank across stores at all. Two implementations of
    a ranking rule are two chances to disagree about it, which on a fused result set is
    invisible: the order is merely different, never obviously wrong.

    ``identity`` says when two items from different legs are the same thing. ``tie_break``
    orders items whose fused scores are equal and must be total, or the sort falls back to
    insertion order -- which is arrival order, which is not stable across processes.

    Everything else matches :func:`rrf_fuse`'s documented behaviour, because it is now the same
    code: an item missing from a leg contributes nothing for that leg, a repeated id within one
    leg counts once at its best position, and the ranks are positions rather than scores, so
    incomparable score scales never enter the computation.
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError(f"RRF k must be a positive integer, got {k!r}.")

    scores: Dict[Hashable, float] = {}
    hit_legs: Dict[Hashable, List[str]] = {}
    first: Dict[Hashable, T] = {}
    for name, items in legs.items():
        seen: Set[Hashable] = set()
        for rank, item in enumerate(items, start=1):
            key = identity(item)
            if key in seen:
                continue  # one leg contributes one rank per id, at its best position
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            hit_legs.setdefault(key, []).append(name)
            first.setdefault(key, item)

    fused = [
        FusedItem(
            item=item,
            score=scores[key],
            legs=len(hit_legs[key]),
            sources=tuple(hit_legs[key]),
        )
        for key, item in first.items()
    ]
    return sorted(fused, key=lambda f: (-f.score, *tie_break(f.item)))


def rrf_fuse(legs: Mapping[str, Sequence[MemoryRecord]], *, k: int = DEFAULT_RRF_K) -> List[MemoryRecord]:
    """Fuse any number of ranked legs by Reciprocal Rank Fusion: ``RRF(d) = sum_r 1/(k + rank_r(d))``.

    ``legs`` is keyed by leg name (``RetrievalLegs.legs()``'s own shape). Only the count and
    order of legs affect the *ranking*, so this takes a graph-only pairing, a vector-only one,
    or any future fourth leg without change; the keys are read only to record which legs had
    each item (:attr:`FusedItem.sources`), which changes no score. Each leg is assumed already ranked best-first,
    the same contract :class:`~egpt.memory.types.RetrievalCandidates` and
    :class:`~egpt.memory.retrieval.orchestrator.RetrievalLegs` both document for their lists;
    ``rank_r(d)`` is a leg's 1-indexed position in that list, not its score, which is the whole
    point -- an incomparable score scale never enters the computation.

    A record absent from a leg contributes nothing for that leg, exactly as absence from a
    ``sum()`` contributes nothing -- there is no rank to invert. Within one leg, a repeated id
    counts once, at its first (best) position, matching ``_merge``'s "one leg contributes one
    score per id" rule so the two strategies cannot silently diverge on it.

    Deterministic: two calls with the same legs produce the same order, via
    :func:`deterministic_rank_key`. ``k`` must be a positive integer -- ``k <= 0`` inflates an
    arbitrarily low rank into a dominating score, defeating the entire premise.

    A record fused from more than one leg's copy of an id -- ``content``, ``tier``, and every
    other non-score field -- keeps whichever leg's copy was seen first, in ``legs.values()``
    iteration order; only ``score`` (recomputed here) and ``substrate`` (cleared once a second leg
    contributes) are ever touched after that first copy. Matches ``_merge``'s existing behavior
    for the same case, not a new risk this function introduces.
    """
    ranked = rrf_rank(
        legs,
        identity=lambda record: record.id,
        tie_break=lambda record: (record.content, record.tier.value, record.id),
        k=k,
    )
    fused: List[MemoryRecord] = []
    for entry in ranked:
        record = entry.item.detached()
        record.score = entry.score
        if entry.legs > 1:
            record.substrate = None  # no longer from one place
        fused.append(record)
    return fused
