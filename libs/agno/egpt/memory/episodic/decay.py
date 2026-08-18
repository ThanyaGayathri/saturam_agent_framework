"""Episodic recency-decay scoring (SM-E4 #111), per SM-R5's ratified decision.

``benchmarks/memory_decay/DECISION.md`` section 1 is the canonical scorer this module
implements -- importance modulates the half-life, not an additive score term::

    recency   = 0.5 ** (age_since_access / half_life)
    half_life = base_half_life * (1 + imp_factor * importance)

``recency_decay`` is the ranking signal SM-E7 (#114) composes with relevance (and, once SM-E5
(#112) ships, importance directly) into the canonical ``alpha * relevance + beta * recency``
score. It takes raw ``importance`` / ``last_accessed_at`` rather than an ``EpisodicRecord`` for
that reason: SM-E7 scores post-fusion ``MemoryRecord``\\ s, which carry those two values in
``metadata`` (``EPISODE_KEYS``, ``egpt.memory.schemas.episodic``), not a full episode row.
``episode_decay_score`` is the ``EpisodicRecord``-level convenience this ticket's own acceptance
criterion ("decay computed per episode") asks for. Blending the composite into live retrieval
ranking is SM-H5's (#135), not this module's.

Reinforcement (DECISION.md section 3) is "confirmed relevant / used", not bare injection into a
top-k list -- deciding what counts as a confirmation signal in production is named there as an
SM-E4 design point, so :func:`reinforce` takes the confirmation as a precondition the caller has
already established and only does the one thing SM-R5 specifies: reset the clock. Persisting that
reset is a write through the seam (a substrate ``upsert``), which no caller of this module does
here -- SM-H5 owns wiring reinforcement into the read path; this module owns what reinforcement
means.

``strength`` is deliberately absent, matching ``Decaying`` (``db/schemas/memory.py``) -- SM-R5
section 5 measured negligible benefit and ADR 0003 section 6 ratifies the omission.

Every function here is pure -- no store is read or written -- so there are no async twins, the
same construction-only exemption CLAUDE.md gives ``episode_from_event``.
"""

import math
from dataclasses import replace
from typing import Optional

from egpt.memory.schemas.episodic import EpisodicRecord
from egpt.memory.types import MemoryOperationError
from egpt.utils.dttm import now_epoch_s

__all__ = [
    "DEFAULT_BASE_HALF_LIFE_S",
    "DEFAULT_IMP_FACTOR",
    "episode_decay_score",
    "half_life_s",
    "recency_decay",
    "reinforce",
]

#: DECISION.md section 1's provisional default ("SM-R1 to ratify") -- 5 days, at importance = 0.
DEFAULT_BASE_HALF_LIFE_S: float = 5.0 * 24 * 3600

#: DECISION.md section 1's provisional default -- importance 1.0 gives a 5x longer half-life.
DEFAULT_IMP_FACTOR: float = 4.0


def half_life_s(
    importance: float,
    base_half_life_s: float = DEFAULT_BASE_HALF_LIFE_S,
    imp_factor: float = DEFAULT_IMP_FACTOR,
) -> float:
    """DECISION.md section 1: ``base_half_life * (1 + imp_factor * importance)``.

    Importance modulates the decay *rate*, not the score -- the measured reason the rejected
    ``exp_recency``/``ebbinghaus`` variants, which add ``+ gamma * importance`` as a second score
    term, are not what this implements (DECISION.md section 1's note to SM-C*).

    ``base_half_life_s`` alone being positive does not make the *computed* half-life positive --
    SM-E7 (#114) will pass caller-supplied hyperparameters through, and a negative ``imp_factor``
    at a high enough ``importance`` drives ``(1 + imp_factor * importance)`` to zero or negative.
    Left unguarded that is a ``ZeroDivisionError`` inside :func:`recency_decay`, or worse, a
    negative half-life that flips the exponent's sign and produces a score above the documented
    ``(0.0, 1.0]`` range -- silently wrong rather than visibly refused.

    Non-finite inputs are the same failure by a different door: under IEEE 754, ``nan <= 0`` and
    ``inf <= 0`` are both ``False``, so a bare ``<= 0`` comparison lets a NaN or infinite
    ``base_half_life_s``/``imp_factor`` through -- a NaN half-life poisons the score, and an
    infinite one produces a memory that never decays. ``math.isfinite`` is checked ahead of the
    positivity comparisons for that reason, on both ``base_half_life_s`` and the computed
    ``half_life`` (which also catches a non-finite ``imp_factor``, since NaN/inf propagate through
    the multiply).
    """
    if not 0.0 <= importance <= 1.0:
        raise MemoryOperationError(
            f"importance must be within [0.0, 1.0], got {importance!r}. EpisodicRecord already "
            "enforces this at construction (Decaying via MemoryRow.__post_init__); a caller "
            "scoring a raw value bypasses that guard.",
            field_name="importance",
        )
    if not math.isfinite(base_half_life_s) or base_half_life_s <= 0:
        raise MemoryOperationError(
            f"base_half_life_s must be a finite positive number, got {base_half_life_s!r}: zero "
            "divides the decay exponent, negative flips its sign, and NaN/inf poison or disable "
            "decay entirely.",
            field_name="base_half_life_s",
        )
    half_life = base_half_life_s * (1.0 + imp_factor * importance)
    if not math.isfinite(half_life) or half_life <= 0:
        raise MemoryOperationError(
            f"imp_factor={imp_factor!r} at importance={importance!r} drives the computed half-life "
            f"to {half_life!r}, which is not finite and positive: zero divides the decay exponent, "
            "negative flips its sign, and NaN/inf poison or disable decay entirely -- the same "
            "failure base_half_life_s is already guarded against above.",
            field_name="imp_factor",
        )
    return half_life


def recency_decay(
    last_accessed_at: int,
    importance: float,
    now: Optional[int] = None,
    base_half_life_s: float = DEFAULT_BASE_HALF_LIFE_S,
    imp_factor: float = DEFAULT_IMP_FACTOR,
) -> float:
    """DECISION.md section 1: ``0.5 ** (age_since_access / half_life)``.

    Returns a value in ``[0.0, 1.0]``: 1.0 at zero age, and -- at ages within any half-life this
    module's parameters produce -- effectively never zero. Float underflow reaches exact ``0.0``
    at an age of roughly 1075 half-lives (``2 ** -1074`` is the smallest representable positive
    ``float``, and ``0.5 ** 1075`` underflows past it) -- about 14.7 years at the default 5-day
    half-life, or 73.6 years at the longest half-life the default parameters produce (importance
    1.0). So the practical range is ``(0.0, 1.0]``; the formal one is closed at both ends. ``now``
    before ``last_accessed_at`` is refused rather than clamped -- a caller scoring against a clock
    that has not reached the access time has a bug upstream, and clamping to 1.0 would hide it
    behind a plausible-looking score.
    """
    at = now_epoch_s() if now is None else now
    if at < last_accessed_at:
        raise MemoryOperationError(
            f"now ({at}) precedes last_accessed_at ({last_accessed_at}): the age would be "
            "negative, which is not a decay this model defines.",
            field_name="now",
        )
    age = at - last_accessed_at
    half_life = half_life_s(importance, base_half_life_s, imp_factor)
    return 0.5 ** (age / half_life)


def episode_decay_score(
    episode: EpisodicRecord,
    now: Optional[int] = None,
    base_half_life_s: float = DEFAULT_BASE_HALF_LIFE_S,
    imp_factor: float = DEFAULT_IMP_FACTOR,
) -> float:
    """:func:`recency_decay` read off an ``EpisodicRecord``.

    ``last_accessed_at`` falls back to ``created_at`` when unset (DECISION.md section 5:
    "Default: created_at") -- a never-accessed episode decays from when it was written, not from
    epoch zero, which would floor every fresh episode's score at (near) zero. Both being unset
    should not happen -- ``TenantScoped.__post_init__`` stamps ``created_at`` whenever it is
    ``None`` at construction -- but ``EpisodicRecord`` is not frozen, so a caller mutating
    ``created_at`` to ``None`` after construction is refused here with a field name rather than
    reaching ``recency_decay`` and failing there with an unrelated ``TypeError``.
    """
    last_accessed_at = episode.last_accessed_at
    if last_accessed_at is None:
        if episode.created_at is None:
            raise MemoryOperationError(
                f"Episode {episode.id!r} has neither last_accessed_at nor created_at set, so its "
                "age cannot be computed.",
                field_name="created_at",
            )
        last_accessed_at = episode.created_at
    return recency_decay(last_accessed_at, episode.importance, now, base_half_life_s, imp_factor)


def reinforce(episode: EpisodicRecord, at: Optional[int] = None) -> EpisodicRecord:
    """Reset the recency clock on confirmed access (DECISION.md section 3).

    Returns a detached copy -- mutating the caller's row in place would let a reader see a
    reinforced timestamp on a record nothing has actually persisted, the same hazard
    ``MemoryRecord.detached()`` guards against on the read side. The isolation ``dataclasses.
    replace`` alone would not give: it shares mutable fields (``entity_ids``, ``supersedes``,
    ``embedding``, ...) between the original and the copy unless something re-copies them, which
    is what ``MemoryRow.__post_init__`` does on every field of list type. Persisting the reset (an
    ``upsert`` through the substrate the episode came from) is the caller's job: this function
    computes what reinforcement means, not how it reaches storage (see module docstring).

    ``at`` is **not** validated against the episode's current ``last_accessed_at``: unlike
    :func:`recency_decay`'s ``now``, going backwards here is a caller replaying an out-of-order
    access, not a clock bug -- SM-R5 defines reinforcement as "set to when the access happened,"
    not "advance only."
    """
    when = now_epoch_s() if at is None else at
    return replace(episode, last_accessed_at=when)
