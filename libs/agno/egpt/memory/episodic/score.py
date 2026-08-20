"""Composite episodic scoring (SM-E7 #114): relevance + recency + importance in one score.

``score = a*relevance + b*recency + c*importance``, each signal a finite value in ``[0.0, 1.0]``
(the contract :mod:`.relevance`'s ``cosine_relevance``, :mod:`.decay`'s ``recency_decay``, and
:mod:`.importance`'s ``ImportanceScorer.score`` already promise their callers) and each weight
independently configurable, per SM-E7 #114's Generative-Agents recipe and design decision #2 in
that issue's own "Design Decisions" table ("configurable weights -- tune per use case/tenant").
This module only combines already-computed signals; producing them is SM-E4/SM-E5/SM-E6's job,
and wiring the composite into retrieval ranking is SM-H5's (#135) -- see
``docs/adr/0003-symbiotic-memory.md`` section B.1.

**Default weights are not equal-weighted.** ADR 0003 B.1 ratifies SM-R8's measured finding that
an additive per-query recency rescore on the fused hot path *hurts* retrieval quality
(``recency_weight`` monotonically degrades 0.8934 -> 0.7265, ``benchmarks/memory_retrieval/
DECISION.md`` section 3): recency belongs at write time, as a prior baked into a stored score, not
recomputed per query. :data:`DEFAULT_EPISODIC_SCORE_WEIGHTS` therefore ships ``recency=0.0``. A
caller scoring at write time (SM-E7's own use case, and SM-E9's pruning input) passes an explicit
:class:`EpisodicScoreWeights` with a nonzero recency weight; a caller wiring this into per-query
hot-path ranking (SM-H5) must not, per the ratified guidance above -- this module enforces neither
choice, since it cannot tell which caller it is, but the default is deliberately the safe one.

Pure arithmetic over already-computed floats -- no store is read or written, so there is no async
twin, the same exemption :mod:`.decay` and :mod:`.relevance` document for their pure functions.

Every ``MemoryOperationError`` this module raises uses a bare ``field_name`` (e.g.
``"relevance"``, ``"relevance_weight"``), matching the package-wide convention -- ``decay.py``,
``relevance.py``, ``types.py``, and ``writeback.py`` all raise bare names, and ``writeback.py``
branches on ``exc.field_name`` programmatically, so a caller that matches on a bare name must keep
matching one here too. A signal error and its same-named weight error need distinct field names
(``"relevance"`` vs. ``"relevance_weight"``), since both can legitimately name the same underlying
concept -- appending ``"_weight"`` keeps both bare and dot-free while staying unambiguous, rather
than the ``"weights.<name>"`` dotted path this module used before this was raised in review.
"""

import math
from dataclasses import dataclass

from egpt.memory.types import MemoryOperationError

__all__ = ["DEFAULT_EPISODIC_SCORE_WEIGHTS", "EpisodicScoreWeights", "composite_episodic_score"]


@dataclass(frozen=True)
class EpisodicScoreWeights:
    """The composite's three weights -- ``a``, ``b``, ``c`` in SM-E7 #114's ``score = a*relevance +
    b*recency + c*importance``, named for the signal each multiplies rather than by letter.

    Construction does not validate: a ``nan`` or negative field builds without error.
    Validation runs at :func:`composite_episodic_score` call time, on whatever ``weights`` that
    call receives, not here -- the same deferred-validation choice the rest of this module makes
    for the three signal arguments, which are also plain, unchecked ``float`` until they reach
    that function.
    """

    relevance: float = 1.0
    recency: float = 0.0
    importance: float = 1.0


#: See the module docstring for why ``recency`` defaults to 0 rather than matching ``relevance``
#: and ``importance`` at 1.0 -- ADR 0003 B.1 ratifies write-time-only use of the recency signal.
DEFAULT_EPISODIC_SCORE_WEIGHTS = EpisodicScoreWeights()


def _require_unit_signal(value: float, field_name: str) -> float:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise MemoryOperationError(
            f"{field_name} must be a finite value within [0.0, 1.0], got {value!r}. Every "
            "episodic signal (recency_decay, ImportanceScorer.score, cosine_relevance) already "
            "guarantees this range to its own callers; a value outside it means a caller passed a "
            "raw or mis-scaled number instead of one of those signals' output.",
            field_name=field_name,
        )
    return value


def _require_nonnegative_finite_weight(value: float, field_name: str) -> float:
    if not math.isfinite(value) or value < 0.0:
        raise MemoryOperationError(
            f"The {field_name} weight must be a finite, non-negative value, got {value!r}: NaN "
            "or infinite poisons the composite, and a negative weight inverts that signal's "
            "contribution -- a more relevant/recent/important episode would score lower.",
            field_name=f"{field_name}_weight",
        )
    return value


def composite_episodic_score(
    relevance: float,
    *,
    recency: float,
    importance: float,
    weights: EpisodicScoreWeights = DEFAULT_EPISODIC_SCORE_WEIGHTS,
) -> float:
    """``a*relevance + b*recency + c*importance`` for one episode against one query.

    ``recency`` and ``importance`` are keyword-only: all three signals are plain ``float``, so
    nothing but the call itself would catch a transposed pair (``composite_episodic_score(v,
    importance_val, recency_val)`` type-checks and silently scores the wrong thing). Keyword-only
    forces every call to spell out which value is which; ``relevance`` stays positional-or-keyword
    since a single leading value has nothing to transpose against.

    Not itself renormalized to ``[0.0, 1.0]`` -- DECISION.md's ratified two-term canonical scorer
    (``alpha * relevance + beta * recency`` at ``alpha=beta=1.0``) is not either, and dividing by
    the weight sum here would silently rescale it, changing what a caller who tuned weights against
    an un-normalized score gets back. Each of the three *signals* is normalized; the composite is a
    weighted sum of normalized signals, not a normalized value itself.

    All-zero weights (every field of ``weights`` at ``0.0``) is valid arithmetic, not refused: it
    collapses every episode's score to ``0.0`` regardless of its signals, destroying ranking
    discrimination. That is almost certainly a misconfiguration if all three land at zero
    together -- unlike the single-zero default on ``recency`` alone, which is deliberate (see the
    module docstring) -- but the composite has no way to distinguish "misconfigured" from "a
    caller who genuinely wants every episode tied", so it computes the (degenerate) arithmetic
    rather than guessing.

    Returns:
        A finite float in ``[0.0, weights.relevance + weights.recency + weights.importance]``
        (reached at all-zero signals and at all-``1.0`` signals respectively, given the
        non-negative weight guarantee :func:`_require_nonnegative_finite_weight` enforces below).
        Not renormalized to ``[0.0, 1.0]`` -- see above.

    Raises:
        MemoryOperationError: any signal is outside ``[0.0, 1.0]`` or non-finite (``field_name``
            names the offending signal: ``"relevance"``, ``"recency"``, or ``"importance"``), or
            any weight is negative or non-finite (``field_name`` is ``"relevance_weight"``,
            ``"recency_weight"``, or ``"importance_weight"`` -- a bare, dot-free name distinct
            from its signal counterpart, matching the package-wide ``field_name`` convention
            every other ``MemoryOperationError`` raise in this package uses; see the module
            docstring for why this module does not use a dotted ``"weights.<name>"`` path).
    """
    _require_unit_signal(relevance, "relevance")
    _require_unit_signal(recency, "recency")
    _require_unit_signal(importance, "importance")
    _require_nonnegative_finite_weight(weights.relevance, "relevance")
    _require_nonnegative_finite_weight(weights.recency, "recency")
    _require_nonnegative_finite_weight(weights.importance, "importance")
    return weights.relevance * relevance + weights.recency * recency + weights.importance * importance
