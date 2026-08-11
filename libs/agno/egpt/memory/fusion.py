"""Rank fusion for the memory read path (SM-C1, per SM-R8 / SM-R9).

Two legs come back from a hybrid read -- a vector ranking and a graph ranking -- and one list has
to go into the prompt. These are the two ways to combine them.

**Weighted / relative-score fusion is the default** (SM-R8, ratified): normalize each leg's scores
into a common range, then sum them by weight. It beat RRF on all four datasets in SM-R8's 2-leg
measurement, and its edge *widened* in the synthetic 3-leg test rather than flipping.

**RRF is selectable and reserved** for the SM-G9-gated graph-join case, where it stays provisional.

Honest caveat, from SPEC section 7 and finding F9: SM-R8 measured **dense + BM25** and
**dense + BM25 + graph**. It never measured this seam's **vector + graph** pairing. The default here
is a mechanism-based transfer, not a measured result for this exact combination -- SM-R13 (#19)
owns measuring it once real backends exist. Keep it config-selectable; do not harden it.
"""

from typing import Dict, List, Optional, Sequence

from egpt.memory.types import MemoryRecord, Substrate

__all__ = ["fuse_rrf", "fuse_weighted", "merge_tightest_bounds"]


def _positional_scores(records: Sequence[MemoryRecord]) -> List[float]:
    """A leg's raw scores, falling back to rank when a substrate returns none.

    A backend that ranks but does not score is legitimate -- graph traversal often has an order and
    no meaningful magnitude. Treating a missing score as 0.0 would silently sink that whole leg, so
    rank position stands in: first place scores 1.0, last approaches 0.
    """
    total = len(records)
    return [record.score if record.score is not None else 1.0 - (i / total) for i, record in enumerate(records)]


def _minmax(values: Sequence[float]) -> List[float]:
    """Normalize to [0, 1]. A flat leg maps to all-1.0, not all-0.0.

    Flat means every hit is equally good, which is a reason to keep them, not to discard the leg.
    """
    if not values:
        return []
    low, high = min(values), max(values)
    if high == low:
        return [1.0 for _ in values]
    span = high - low
    return [(value - low) / span for value in values]


def merge_tightest_bounds(kept: MemoryRecord, incoming: MemoryRecord) -> None:
    """Fold ``incoming``'s bi-temporal bounds into ``kept``, keeping the most restrictive.

    The same record can arrive from both legs, and only the graph is authoritative for validity
    (finding F2) -- so the vector copy typically carries no bounds at all. Taking the tightest of
    each bound means the authoritative answer wins without the fuser needing to know which leg it
    came from: the later ``t_valid`` and the earlier ``t_invalid`` are always the graph's when the
    graph has an opinion, and the other leg's ``None`` never widens the window.
    """
    if incoming.t_valid is not None:
        kept.t_valid = incoming.t_valid if kept.t_valid is None else max(kept.t_valid, incoming.t_valid)
    if incoming.t_invalid is not None:
        kept.t_invalid = incoming.t_invalid if kept.t_invalid is None else min(kept.t_invalid, incoming.t_invalid)
    if incoming.expired_at is not None and kept.expired_at is None:
        kept.expired_at = incoming.expired_at
    if incoming.superseded_by is not None and kept.superseded_by is None:
        kept.superseded_by = incoming.superseded_by
    if incoming.provenance:
        merged = dict(incoming.provenance)
        merged.update(kept.provenance)
        kept.provenance = merged


def _fold(
    fused: Dict[str, MemoryRecord],
    order: Dict[str, int],
    record: MemoryRecord,
    score: float,
) -> None:
    """Accumulate one leg's hit into the fused set, adding scores for records seen twice.

    Dedup is by ``id``, which is why one logical episode must carry one id across both substrates
    (finding F1). A record found by both legs is *more* relevant, so the scores sum -- and its
    ``substrate`` is cleared, because the fused record no longer comes from one place.

    ``order`` remembers when each id was first seen, which is what breaks ties below.
    """
    existing = fused.get(record.id)
    if existing is None:
        fused[record.id] = MemoryRecord(**{**vars(record), "score": score})
        order[record.id] = len(order)
        return
    existing.score = (existing.score or 0.0) + score
    existing.substrate = None
    merge_tightest_bounds(existing, record)


def _ranked(fused: Dict[str, MemoryRecord], order: Dict[str, int]) -> List[MemoryRecord]:
    """Highest score first; ties break on **first-seen order**, not on id.

    Ties are common -- two equally good hits, or a flat leg where every score normalizes to 1.0 --
    so what breaks them is a real ranking decision rather than a detail.

    First-seen order means the substrates' own ranking survives: the leg returned them in an order
    it considered meaningful, and fusion has no better information when the scores agree. Sorting
    by ``id`` instead looks stable but is not: ids are UUIDs, so equal-scoring records would come
    back in a different order for every fresh set of writes, and two deployments holding identical
    content would rank it differently.
    """
    return sorted(fused.values(), key=lambda r: (-(r.score or 0.0), order[r.id]))


def fuse_weighted(
    legs: Sequence[Sequence[MemoryRecord]],
    weights: Sequence[float],
) -> List[MemoryRecord]:
    """Min-max normalize each leg, then sum by weight. SM-R8's ratified default.

    ``legs`` and ``weights`` are positional and must be the same length. A leg that returned
    nothing contributes nothing -- it does not shift the others' normalization, because each leg is
    normalized against itself.
    """
    if len(legs) != len(weights):
        raise ValueError(f"fuse_weighted got {len(legs)} legs and {len(weights)} weights; they must match.")

    fused: Dict[str, MemoryRecord] = {}
    order: Dict[str, int] = {}
    for records, weight in zip(legs, weights):
        for record, norm in zip(records, _minmax(_positional_scores(records))):
            _fold(fused, order, record, weight * norm)
    return _ranked(fused, order)


def fuse_rrf(legs: Sequence[Sequence[MemoryRecord]], k: int = 60) -> List[MemoryRecord]:
    """Reciprocal rank fusion: sum ``1 / (k + rank)`` across legs, rank 1-based.

    Rank-based, so it ignores score magnitudes entirely -- which is its appeal when legs are not
    comparably scaled, and its weakness when they are. Reserved for the SM-G9 graph-join case.
    """
    fused: Dict[str, MemoryRecord] = {}
    order: Dict[str, int] = {}
    for records in legs:
        for rank, record in enumerate(records, start=1):
            _fold(fused, order, record, 1.0 / (k + rank))
    return _ranked(fused, order)


def substrate_of(records: Sequence[MemoryRecord]) -> Optional[Substrate]:
    """The single substrate a leg came from, or ``None`` if it is empty or mixed."""
    origins = {record.substrate for record in records if record.substrate is not None}
    return origins.pop() if len(origins) == 1 else None
