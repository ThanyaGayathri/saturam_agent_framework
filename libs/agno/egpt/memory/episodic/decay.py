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

from dataclasses import replace
from typing import Optional, cast

from egpt.db.schemas.memory import EpisodicRecord
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
DEFAULT_BASE_HALF_LIFE_S: float = 5 * 24 * 3600

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
    """
    if not 0.0 <= importance <= 1.0:
        raise MemoryOperationError(
            f"importance must be within [0.0, 1.0], got {importance!r}. EpisodicRecord already "
            "enforces this at construction (Decaying via MemoryRow.__post_init__); a caller "
            "scoring a raw value bypasses that guard.",
            field_name="importance",
        )
    if base_half_life_s <= 0:
        raise MemoryOperationError(
            f"base_half_life_s must be positive, got {base_half_life_s!r}: zero divides the decay "
            "exponent and negative flips its sign, so age would increase the score.",
            field_name="base_half_life_s",
        )
    return base_half_life_s * (1.0 + imp_factor * importance)


def recency_decay(
    last_accessed_at: int,
    importance: float,
    now: Optional[int] = None,
    base_half_life_s: float = DEFAULT_BASE_HALF_LIFE_S,
    imp_factor: float = DEFAULT_IMP_FACTOR,
) -> float:
    """DECISION.md section 1: ``0.5 ** (age_since_access / half_life)``.

    Returns a value in ``(0.0, 1.0]``: 1.0 at zero age, approaching (never reaching) zero as age
    grows. ``now`` before ``last_accessed_at`` is refused rather than clamped -- a caller scoring
    against a clock that has not reached the access time has a bug upstream, and clamping to 1.0
    would hide it behind a plausible-looking score.
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
    epoch zero, which would floor every fresh episode's score at (near) zero. The cast is safe,
    not defensive: ``TenantScoped.__post_init__`` always stamps ``created_at`` when it is ``None``
    at construction, so a constructed ``EpisodicRecord`` never has both fields unset.
    """
    last_accessed_at = episode.last_accessed_at
    if last_accessed_at is None:
        last_accessed_at = cast(int, episode.created_at)
    return recency_decay(last_accessed_at, episode.importance, now, base_half_life_s, imp_factor)


def reinforce(episode: EpisodicRecord, at: Optional[int] = None) -> EpisodicRecord:
    """Reset the recency clock on confirmed access (DECISION.md section 3).

    Returns a detached copy -- mutating the caller's row in place would let a reader see a
    reinforced timestamp on a record nothing has actually persisted, the same hazard
    ``MemoryRecord.detached()`` guards against on the read side. Persisting the reset (an
    ``upsert`` through the substrate the episode came from) is the caller's job: this function
    computes what reinforcement means, not how it reaches storage (see module docstring).

    ``at`` is **not** validated against the episode's current ``last_accessed_at``: unlike
    :func:`recency_decay`'s ``now``, going backwards here is a caller replaying an out-of-order
    access, not a clock bug -- SM-R5 defines reinforcement as "set to when the access happened,"
    not "advance only."
    """
    when = now_epoch_s() if at is None else at
    return replace(episode, last_accessed_at=when)
