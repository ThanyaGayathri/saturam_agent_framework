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

from copy import deepcopy
from typing import Dict, List, Sequence, Set

from egpt.memory.types import MemoryRecord

__all__ = ["fuse_rrf", "fuse_weighted", "merge_tightest_bounds"]


def _positional_scores(records: Sequence[MemoryRecord]) -> List[float]:
    """A leg's raw scores, falling back to rank when a substrate returns none.

    A backend that ranks but does not score is legitimate -- graph traversal often has an order and
    no meaningful magnitude. Treating a missing score as 0.0 would silently sink that whole leg, so
    rank position stands in: first place scores 1.0, last approaches 0.

    The fallback is **all-or-nothing per leg**. Mixing real magnitudes with rank proxies inside one
    leg puts them on incomparable scales: with ``[0.95, None, 0.10]`` the rank proxy for the middle
    record is 0.67, so an *unscored* record outranks one the backend scored 0.10. A leg that scores
    some records and not others is malformed, and the ranking it supplied is the only signal that
    survives normalization -- so use that for the whole leg rather than interleaving two scales.
    """
    total = len(records)
    if any(record.score is None for record in records):
        return [1.0 - (i / total) for i in range(total)]
    return [record.score for record in records if record.score is not None]


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


def _detached(record: MemoryRecord, score: float) -> MemoryRecord:
    """A copy of ``record`` that shares no mutable state with the substrate's stored row.

    ``MemoryRecord(**vars(record))`` copies *references*, so ``provenance``, ``metadata``, and
    ``topics`` would stay the same objects the backend is holding. A consumer annotating a returned
    record (``slice_.records[0].metadata["seen_by"] = ...``) would then be writing into the store,
    and two consumers handed the same slice would see each other's edits. For an in-process backend
    that is silent cross-contamination in both directions.

    ``provenance`` and ``metadata`` are **deep**-copied, not one level. ``dict()`` left nested
    values aliased, and the most likely real instance is a consumer appending to
    ``provenance["source_episode_ids"]`` -- the exact shape consolidation writes, and the one
    ``FactWrite`` documents as load-bearing for the fact-to-episode audit trail. ``topics`` is
    ``List[str]`` by type, so a shallow ``list()`` is complete there.
    """
    return MemoryRecord(
        **{
            **vars(record),
            "score": score,
            "provenance": deepcopy(record.provenance),
            "metadata": deepcopy(record.metadata),
            "topics": list(record.topics) if record.topics is not None else None,
        }
    )


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
        # Deep-copied for the same reason as `_detached`, and this is the *other* aliasing path:
        # `incoming` is the raw substrate record, and this runs on the seen-by-both-legs path --
        # the one fusion exists for. Copying only the outer dict left the graph leg's stored
        # provenance reachable through the returned record.
        merged = deepcopy(incoming.provenance)
        merged.update(deepcopy(kept.provenance))
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
        fused[record.id] = _detached(record, score)
        order[record.id] = len(order)
        return
    existing.score = (existing.score or 0.0) + score
    existing.substrate = None
    merge_tightest_bounds(existing, record)


def _ranked(fused: Dict[str, MemoryRecord], order: Dict[str, int]) -> List[MemoryRecord]:
    """Highest score first; ties break on **content**, then id.

    Ties are common -- two equally good hits, or a flat leg where every score normalizes to 1.0 --
    so what breaks them is a real ranking decision rather than a detail.

    **Content, because it is the only stable key.** Deviation D8 originally broke ties on first-seen
    order, reasoning that the substrate's own ordering carries information the fuser lacks. That is
    true in principle and was unusable in practice: first-seen order is only as stable as what
    produced it, and what produced it was UUID ids feeding ``scan``'s ``(created_at, id)`` ordering.
    The observable effect was the cookbook printing three different orderings across eight runs,
    and Mode A and Mode B disagreeing inside a single process.

    The trade is explicit: for exactly-equal scores a backend's own ordering is discarded in favour
    of one that is reproducible across runs, workers, and deployments. A backend returning real
    score magnitudes rarely reaches this tie-break at all.

    ``tier`` comes next, and it is not decorative: consolidation stores a distilled fact with the
    head episode's content verbatim, so the episodic and semantic copies tie on *both* score and
    content and would fall through to ``id`` -- which is where the UUID instability came back. Tier
    is a stable enum, so those two order the same way everywhere.

    ``id`` is the last resort, for records identical in score, content and tier -- at which point
    the order genuinely is arbitrary. ``order`` is kept in the signature because ``_fold``
    maintains it and a future scorer may want it; it no longer participates in the sort.
    """
    return sorted(fused.values(), key=lambda r: (-(r.score or 0.0), r.content, r.tier.value, r.id))


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
        seen: Set[str] = set()
        for record, norm in zip(records, _minmax(_positional_scores(records))):
            # One leg contributes one score per id. Folding twice would accumulate, so a substrate
            # returning a duplicate would outrank a record found by both legs -- inverting the
            # signal fusion is built on.
            if record.id in seen:
                continue
            seen.add(record.id)
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
        seen: Set[str] = set()
        for rank, record in enumerate(records, start=1):
            if record.id in seen:
                continue
            seen.add(record.id)
            _fold(fused, order, record, 1.0 / (k + rank))
    return _ranked(fused, order)
