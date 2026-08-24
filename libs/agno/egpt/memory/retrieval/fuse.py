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

from typing import Dict, List, Mapping, Sequence, Set, Tuple

from egpt.memory.types import MemoryRecord

__all__ = ["DEFAULT_RRF_K", "deterministic_rank_key", "rrf_fuse"]

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


def rrf_fuse(legs: Mapping[str, Sequence[MemoryRecord]], *, k: int = DEFAULT_RRF_K) -> List[MemoryRecord]:
    """Fuse any number of ranked legs by Reciprocal Rank Fusion: ``RRF(d) = sum_r 1/(k + rank_r(d))``.

    ``legs`` is keyed by leg name (``RetrievalLegs.legs()``'s own shape) but the keys are not read
    -- only the count and order of legs matters, so this takes a graph-only pairing, a vector-only
    one, or any future fourth leg without change. Each leg is assumed already ranked best-first,
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
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError(f"RRF k must be a positive integer, got {k!r}.")

    scores: Dict[str, float] = {}
    hit_legs: Dict[str, int] = {}
    fused: Dict[str, MemoryRecord] = {}
    for records in legs.values():
        seen: Set[str] = set()
        for rank, record in enumerate(records, start=1):
            if record.id in seen:
                continue  # one leg contributes one rank per id, at its best position
            seen.add(record.id)
            scores[record.id] = scores.get(record.id, 0.0) + 1.0 / (k + rank)
            hit_legs[record.id] = hit_legs.get(record.id, 0) + 1
            if record.id not in fused:
                fused[record.id] = record.detached()

    for record_id, record in fused.items():
        record.score = scores[record_id]
        if hit_legs[record_id] > 1:
            record.substrate = None  # no longer from one place

    return sorted(fused.values(), key=deterministic_rank_key)
