"""Time-aware blended ranking (SM-H5 #135): temporal filter + SM-E7 composite over the
fused/reranked output.

Fusion (SM-H2 #132) and rerank (SM-H3 #133, not yet built) are relevance-oriented; neither has a
place where the episodic composite (recency + importance + relevance, SM-E7 #114) or an explicit
time window is applied. This module is that place -- a pure function over an already-fused
:class:`~egpt.memory.types.MemoryRecord` list, so it composes after either stage without needing
to know which one ran. Wiring it into ``CoLocatedMemoryProvider.read``/``aread`` is SM-H6's
(#136, "MemorySlice assembly") job, not this module's -- the issue's own "Affected Files" table
names only this module and ``episodic/score.py``, not ``adapters/co_located.py``.

**Order, per the issue's own proposed approach:** filter first, then blend, then sort. Filtering
after blending would score records the query never asked about; blending after sorting would
sort on the wrong key.

**Temporal filter.** ``query.window()`` (``valid_from``/``valid_to``) is applied only when the
caller set it -- ADR 0003 B.1: "recency remains available as an **optional pre-filter** for
explicitly time-scoped queries." A query with no window is not implicitly narrowed to "now": that
default-to-current-instant behaviour already belongs to
``CoLocatedMemoryProvider._slice`` (paired with the current-belief predicate, which this module
does not have the bounds to evaluate), and duplicating a *different* default here would make the
two disagree about what an unscoped read means. **This is not a subset relationship** between what
this module keeps and what ``_slice`` keeps: on an *unscoped* query this module drops nothing,
while ``_slice`` still narrows to "now" downstream -- the two agree only once a window is
explicitly set. Relatedly, an unscoped call here does not check ``is_current_belief()`` either (a
transaction-time predicate ``_slice`` applies unconditionally, windowed or not) -- this module has
no bounds to evaluate it against, so a superseded record can rank above a current one until SM-H6
wires this in ahead of, or alongside, ``_slice``'s own filtering.

**Composite blend.** Episodic-tier records only -- SM-E7's composite is an episodic scoring
device, and a semantic/procedural record carries no ``importance`` for it to consume. The
``relevance`` signal :func:`composite_episodic_score` requires is the record's own fused
``score``, clamped into ``[0.0, 1.0]`` by :func:`_clamp_unit` -- a genuine two-sided clamp, both
directions live: ``RetrievalCandidates``/``VectorSubstrate.search`` promise a single leg's score
is already in ``[0.0, 1.0]``, but ``_merge``'s score-sum default adds two such legs together (up
to ``2.0``, clamped down), and nothing upstream of this module refuses a corrupted or
adversarial negative ``score`` either (clamped up). A non-finite ``score`` (``NaN``/``inf``) is
refused rather than clamped -- silently mapping it into the valid range would either invent a
value or, worse, hand a corrupted signal the *maximum* possible relevance, the opposite of what
"malformed input loses" means everywhere else in this module.

**Not every ``MemoryRecord`` this module accepts is a valid input.** ``importance`` in
``metadata`` is a promise :func:`~egpt.memory.schemas.episodic.episode_to_record` makes for
episodic records that pass through it -- ``CoLocatedMemoryProvider``'s own projection. It is
**not** a promise every ``MemoryProvider`` keeps: ``SeedMemoryProvider._store_records``
(``adapters/seed.py``) builds bare ``MemoryRecord``s with no ``metadata`` kwarg (so it defaults
to ``{}``) for any tier a seed store is bound to, including EPISODIC. Seed-provider output is not
(yet) a valid input to :func:`rank` for that reason -- it is not wired to call this module today,
so this is a documented precondition on a future caller, not a live bug.

**``query.limit`` is not applied here.** Truncating to a page size is ``MemorySlice`` assembly's
job (SM-H6), the same boundary every other scope this module does not enforce (SM-E8, SM-E4,
wiring into the provider) is drawn at.

**Recency signal is always ``0.0``, not read from ``weights``.** ADR 0003 B.1 (citing SM-R8's
measured ``recency_weight`` degradation, 0.8934 -> 0.7265) rules out a per-query additive recency
rescore on the fused hot path -- full stop, not conditionally on the query being time-scoped.
Passing a fixed ``0.0`` *signal* enforces that structurally: whatever ``weights.recency`` a caller
supplies, its contribution is ``weights.recency * 0.0 == 0.0`` unless the weight itself is invalid
(non-finite or negative), which :func:`composite_episodic_score` still refuses. ``weights`` exists
on this module's signature only so a caller may retune ``relevance``/``importance``; it is not a
door back into per-query recency.

Pure -- no store is read or written, so there is no async twin, the same construction-only
exemption CLAUDE.md gives ``episode_from_event`` and this package's other scoring modules
(``episodic/decay.py``, ``episodic/relevance.py``, ``episodic/score.py``,
``retrieval/fuse.py``).

**Not here:** SM-E8's contiguity blend (``score.contiguity_bonus``/``blend_contiguity``). The
issue's own "Proposed Approach" names only the SM-E7 composite, and #135's "Dependencies" section
lists SM-E7 (#114) and SM-H2 (#132) -- not SM-E8 (#115). Both ``episodic/contiguity.py`` and
``episodic/score.py`` describe wiring contiguity into live ranking as "SM-H5's job" in the
abstract, but neither is reachable from this ticket's acceptance criteria or its dependency list;
wiring it in is left to whichever ticket the contiguity buffer's own caller (a session-scoped
neighbor scan, not yet built) turns out to be. Also not here: SM-E4's write-time reinforcement
(``episodic.decay.reinforce``) -- that persists a clock reset through an ``upsert``, a write this
read-side ranking function has no substrate handle to perform, and no acceptance criterion below
asks for it.
"""

import math
from typing import List, Optional, Sequence

from egpt.memory.episodic.score import (
    DEFAULT_EPISODIC_SCORE_WEIGHTS,
    EpisodicScoreWeights,
    composite_episodic_score,
)
from egpt.memory.retrieval.fuse import deterministic_rank_key
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryOperationError, MemoryQuery, MemoryRecord, ValidWindow

__all__ = ["rank"]


def _temporal_filter(records: Sequence[MemoryRecord], window: Optional[ValidWindow]) -> List[MemoryRecord]:
    if window is None:
        return list(records)
    return [record for record in records if window.overlaps(record.t_valid, record.t_invalid)]


def _clamp_unit(value: Optional[float], *, record_id: str) -> float:
    """``value`` (or ``0.0`` when unset) clamped into ``[0.0, 1.0]``, both directions live. See the
    module docstring for why. A non-finite ``value`` is refused rather than silently mapped to a
    bound -- ``max(0.0, min(1.0, float("nan")))`` and the ``+inf`` case both evaluate to ``1.0``
    under plain ``min``/``max``, which would hand a corrupted score the *maximum* relevance."""
    if value is not None and not math.isfinite(value):
        raise MemoryOperationError(
            f"Episodic record {record_id!r} carries a non-finite fused score ({value!r}), which "
            "cannot be clamped into a relevance signal. A NaN or infinite score is corrupted "
            "upstream input, not a valid extreme.",
            field_name="score",
        )
    return max(0.0, min(1.0, value if value is not None else 0.0))


def _blended_score(record: MemoryRecord, weights: EpisodicScoreWeights) -> float:
    if record.tier is not MemoryTier.EPISODIC:
        return record.score if record.score is not None else 0.0
    importance = record.metadata.get("importance")
    if importance is None:
        # `episode_to_record`'s projection floors every episodic `MemoryRecord.metadata` with
        # `importance` via `episode_details`'s own default -- a record reaching this function
        # without one did not come through that projection (see the module docstring:
        # `SeedMemoryProvider` output does not, today). `field_name` is deliberately
        # `"importance_metadata"`, not `"importance"` -- `composite_episodic_score` below raises
        # `field_name="importance"` for a *present but out-of-range* value, and a caller branching
        # on `exc.field_name` needs to tell "not recorded" from "recorded but invalid" apart.
        raise MemoryOperationError(
            f"Episodic record {record.id!r} carries no 'importance' in metadata, so its composite "
            "score cannot be computed. Every episodic MemoryRecord CoLocatedMemoryProvider "
            "returns carries one (egpt.memory.schemas.episodic.episode_to_record); this record "
            "did not come through that projection.",
            field_name="importance_metadata",
        )
    if isinstance(importance, bool) or not isinstance(importance, (int, float)):
        # `bool` is an `int` subclass, excluded for the reason every other epoch/weight guard in
        # this package excludes it: `True` would silently become `importance=1`. A non-numeric
        # value (a string, most plausibly) would otherwise reach `composite_episodic_score`'s
        # `math.isfinite()` and raise a raw `TypeError`, breaking this module's "refuse cleanly at
        # the boundary" convention -- `MemoryOperationError` here keeps every refusal this module
        # raises the same class, not just the missing-key one above. `field_name` is distinct from
        # both the missing-key case above (`"importance_metadata"`) and
        # `composite_episodic_score`'s own out-of-range case (`"importance"`) -- three
        # distinguishable failure modes, three names.
        raise MemoryOperationError(
            f"Episodic record {record.id!r} carries importance={importance!r} in metadata, which "
            f"is a {type(importance).__name__}, not a number. Composite scoring needs a finite "
            "value in [0.0, 1.0].",
            field_name="importance_metadata_type",
        )
    relevance = _clamp_unit(record.score, record_id=record.id)
    return composite_episodic_score(relevance, recency=0.0, importance=importance, weights=weights)


def rank(
    records: Sequence[MemoryRecord],
    query: MemoryQuery,
    *,
    weights: EpisodicScoreWeights = DEFAULT_EPISODIC_SCORE_WEIGHTS,
) -> List[MemoryRecord]:
    """Temporal-filter, then blend the SM-E7 composite into, ``records`` -- already fused (SM-H2)
    and optionally reranked (SM-H3) -- for final ranking (SM-H6 assembles the ``MemorySlice``).

    Returns detached copies, the same "safe to rank, annotate, and hand out" contract
    ``MemoryRecord.detached()`` documents and ``rrf_fuse``/``_merge`` already honour -- a caller's
    own record is never mutated in place. A non-episodic record's ``score`` passes through
    unchanged; an episodic one's is replaced by its composite (see the module docstring for both).

    Ties break via :func:`~egpt.memory.retrieval.fuse.deterministic_rank_key`, the same tie-break
    every fusion strategy in this package uses, so two calls over the same input produce the same
    order regardless of dict/set iteration order upstream.

    ``query.limit`` is not applied -- see the module docstring.

    Raises:
        MemoryOperationError: an episodic record in ``records`` carries no ``importance`` in
            ``metadata`` (``field_name="importance_metadata"``), a non-numeric one
            (``field_name="importance_metadata_type"``), or a non-finite fused ``score``
            (``field_name="score"``); or is propagated from
            :func:`~egpt.memory.episodic.score.composite_episodic_score` for an invalid ``weights``
            field or an out-of-range (but present and numeric) ``importance``
            (``field_name="importance"``) -- four distinguishable failure modes, four names.
    """
    filtered = _temporal_filter(records, query.window())
    scored: List[MemoryRecord] = []
    for record in filtered:
        detached = record.detached()
        detached.score = _blended_score(record, weights)
        scored.append(detached)
    return sorted(scored, key=deterministic_rank_key)
