"""Episode forgetting / pruning policy (SM-E9 #116), per SM-R5's ratified decision.

``benchmarks/memory_decay/DECISION.md`` section 4 (restated in ADR 0003 section B.1, "SM-E9 owns
pruning under the A.7/SM-R5 recency floor") is the policy this implements: an episode is
prune-eligible when its SM-E4 decay score (:func:`~egpt.memory.episodic.decay.episode_decay_score`)
falls below a recency floor, and that is the *only* gate. DECISION.md section 4 implemented and
measured a stricter three-condition gate (recency floor AND importance-below-floor AND
not-currently-valid) and rejected it: the extra conditions shield ``FOREVER``-valid clutter from
ever being pruned, so retained count stays ~402 of 439 rather than the recency-floor-alone result
of 292 -- too conservative to bound growth. The importance-weighted half-life
(``episodic.decay.half_life_s``) already makes the plain recency floor safe (0 false-forgets
measured), which is what lets this module skip the extra conditions.

**Archive, never hard-delete (DECISION.md section 6, ADR 0003 section B.3).** Pruning here means
row-level ``expired_at`` (:meth:`~egpt.db.schemas.memory.BiTemporal.expire`, SCD-2 /
invalidate-not-delete) -- never :meth:`~egpt.memory.substrates.GraphSubstrate.invalidate`, which is
edge-level semantic contradiction on the graph and, per that method's own docstring, is explicitly
"a different object" ("Do not route episode archival here"). An archived episode drops out of
current-belief reads (``expired_at IS NULL``) but stays reconstructable, exactly like a
consolidation-superseded row.

**Ordering (ADR 0003 section 3, ratified from DECISION.md section 6): decay-pruning must run
*after* the consolidation/reflection pass, never before**, or an episodic row can be archived
before its knowledge is distilled into the semantic tier. This module does not enforce that --
consolidation's own extract/merge/reflect stages are not wired into a scheduled pass yet
(``egpt.memory.consolidation`` package docstring), so there is nothing in this codebase to
sequence against today. Whichever caller schedules a forgetting pass owns running it after
consolidation, the same way ``egpt.memory.adapters.seed.SeedMemoryProvider.consolidate`` already
documents that the *rest* of the consolidation loop is "SM-L1's (#122) onward" rather than
something this layer can wire up.

**Per-tenant config (SM-C5 #93, not yet landed; ``egpt.memory.config`` module docstring).** Every
threshold below is a plain parameter carrying the DECISION.md-ratified default, not a hardcoded
constant, so a per-tenant policy lookup can override it once SM-C5 ships its
``ProviderConfig.lookup``-shaped resolution (``egpt.providers.config``). Resolving *which* value a
given tenant gets is SM-C5's job; this module only defines what a threshold means and what happens
once an episode crosses it.

**Reuses the shape of the seed's ``Curator``, not the class itself.** ``learn/curate.py``'s
``Curator.prune`` / ``_filter_by_age`` / ``_keep_newest`` (SM-E9's own seed facts) establish
filter-a-list-and-return-survivors as the pruning shape :func:`select_for_forgetting` follows here.
They are not imported: ``Curator`` is scoped to ``self.machine.stores["user_profile"]`` and a list
of plain dicts (its own docstring: "Currently supports user_profile store only"), and
``egpt.learn`` sits *below* ``egpt.memory`` in the dependency direction ADR 0003 section 2 ratifies
(``MemoryProvider`` wraps the seed's ``LearningStore``, not the reverse) -- importing
``EpisodicRecord``/decay scoring into ``learn/curate.py`` would invert that. This is the same
judgment call ``egpt.memory.types.normalize_entity_name`` already made for
``Curator._normalize`` ("restated rather than imported because that method is private on a class
needing a wired ``LearningMachine``") and ``SeedMemoryProvider.consolidate`` made for
``Curator.deduplicate`` ("narrower than ADR 0003 section 2.1's 'REUSE inside consolidate' implies
... wired as the dedup hook it can serve").

Like :mod:`egpt.memory.episodic.decay`, no function here reads or writes a store, so a caller
with an archived episode persists it through the substrate seam it already owns
(``GraphSubstrate.upsert_episodes`` / ``VectorSubstrate.upsert``) -- the same language
``episodic.decay``'s own module docstring uses, since "pure" would overstate it:
:func:`select_for_forgetting` reads the system clock (``now_epoch_s()``) when ``now`` is unset.
No async twins, the same construction-only exemption CLAUDE.md gives ``episode_from_event``.
"""

import math
from dataclasses import replace
from typing import List, Optional, Sequence

from egpt.memory.episodic.decay import (
    DEFAULT_BASE_HALF_LIFE_S,
    DEFAULT_IMP_FACTOR,
    episode_decay_score,
)
from egpt.memory.schemas.episodic import EpisodicRecord
from egpt.memory.types import MemoryOperationError
from egpt.utils.dttm import now_epoch_s

__all__ = [
    "DEFAULT_PRUNE_THRESHOLD",
    "forget",
    "is_prune_eligible",
    "select_for_forgetting",
]

#: DECISION.md section 1 / section 4's ratified recency-floor prune gate, ADR 0003 section A.7:
#: "RATIFIED as defaults". Below this decay score an episode is prune-eligible.
DEFAULT_PRUNE_THRESHOLD: float = 0.05


def is_prune_eligible(
    episode: EpisodicRecord,
    now: Optional[int] = None,
    prune_threshold: float = DEFAULT_PRUNE_THRESHOLD,
    base_half_life_s: float = DEFAULT_BASE_HALF_LIFE_S,
    imp_factor: float = DEFAULT_IMP_FACTOR,
) -> bool:
    """DECISION.md section 4: ``episode_decay_score(episode) < prune_threshold``, and only that.

    An already-archived episode (``expired_at`` set) is never eligible again -- re-selecting it on
    a later pass would double-report an episode this policy already decided about, even though
    :meth:`~egpt.db.schemas.memory.BiTemporal.expire` itself no-ops on a replay. Checked ahead of
    scoring so a caller filtering a mixed batch of live and already-archived rows never pays for
    (or is misled by) a decay score on a row this function will refuse regardless of it.

    Checked *before* ``prune_threshold`` is validated: an already-archived episode is refused
    regardless of what ``prune_threshold`` is, so there is no reason to reject it over a bad
    threshold value it would have been exempt from anyway.

    ``prune_threshold`` must be in ``(0.0, 1.0)``. A NaN threshold makes ``score <
    prune_threshold`` false for every score under IEEE 754 comparison rules -- silently turning
    pruning off rather than raising. A negative threshold is finite but has the same effect,
    since :func:`~egpt.memory.episodic.decay.recency_decay` never returns a value at or below 0.0
    in practice (its own docstring: "the practical range is (0.0, 1.0]"), so ``score <
    prune_threshold`` is false for every episode there too. A threshold ``>= 1.0`` is the mirror
    failure: ``score`` never exceeds 1.0, so every episode becomes eligible, archiving a whole
    batch. All three are the same class of silent-wrong-answer :func:`~egpt.memory.episodic.decay.
    half_life_s` already guards ``base_half_life_s``/``imp_factor`` against -- reached here through
    a parameter that (per the module docstring) SM-C5's not-yet-landed per-tenant config is
    expected to supply, so a bad tenant value must raise, not silently disable or maximise pruning.
    """
    if episode.expired_at is not None:
        return False
    if not math.isfinite(prune_threshold) or prune_threshold <= 0.0 or prune_threshold >= 1.0:
        raise MemoryOperationError(
            f"prune_threshold must be in (0.0, 1.0), got {prune_threshold!r}: outside that range "
            "it silently disables pruning (<=0 or NaN) or archives every episode (>=1.0) instead "
            "of raising.",
            field_name="prune_threshold",
        )
    score = episode_decay_score(episode, now, base_half_life_s, imp_factor)
    return score < prune_threshold


def forget(
    episode: EpisodicRecord,
    at: Optional[float] = None,
    t_invalid: Optional[float] = None,
) -> EpisodicRecord:
    """Archive one episode: row-level ``expired_at``, never delete (DECISION.md section 6).

    ``at``/``t_invalid`` are ``float``, not ``int``: ``to_epoch_s`` (``egpt.utils.dttm``), what
    :meth:`~egpt.db.schemas.memory.BiTemporal.expire` ultimately calls, already accepts
    ``Union[int, float, str, datetime]`` and truncates -- a caller passing ``time.time()``'s
    ``float`` is ordinary usage, not a type violation to reject.

    Returns a detached, already-archived copy -- the same non-mutation contract
    :func:`~egpt.memory.episodic.decay.reinforce` documents, and for the same reason: mutating the
    caller's row in place would let a reader see an archived episode nothing has actually
    persisted. ``dataclasses.replace`` (rather than mutating ``episode`` directly) re-runs
    ``MemoryRow.__post_init__``, which re-copies every list-typed field, so the returned copy
    shares no top-level mutable state with ``episode`` for any mutation :meth:`expire` performs
    (it only ever touches ``expired_at``/``t_invalid``) -- ``metadata`` is a shallow copy, so a
    caller that mutates a nested object *inside* a metadata value would see that mutation on both
    copies, same as :func:`~egpt.memory.episodic.decay.reinforce`.

    Unconditional: this does not check :func:`is_prune_eligible`. A caller that already decided
    (batch selection is :func:`select_for_forgetting`'s job) or an explicit forget request is not
    re-gated on the recency floor here -- refusing it would make a deliberate action fail silently
    for a reason the caller did not ask about. An already-archived episode passed here comes back
    unchanged: :meth:`~egpt.db.schemas.memory.BiTemporal.expire` no-ops on a replayed archive,
    matching its documented behaviour for every other caller of that method.

    ``at`` defaults to this module's own clock (``now_epoch_s()``), resolved *before* validation
    rather than left to :meth:`~egpt.db.schemas.memory.BiTemporal.expire`'s own default. Two
    reasons: an unresolved ``at=None`` would skip the ``created_at``-ordering guard below entirely
    (there would be no concrete value to compare), silently falling through to ``expire``'s own
    bare ``ValueError`` for an episode with a future ``created_at`` -- the same error-boundary gap
    ``at``/``t_invalid`` validation exists to close; and resolving it here, rather than through
    ``BiTemporal``'s internal default, gives this module one clock for both its default-at path and
    :func:`select_for_forgetting`'s explicit one, instead of two call sites reaching
    ``now_epoch_s`` through different imports.

    ``at``/``t_invalid`` are validated here rather than left to
    :meth:`~egpt.db.schemas.memory.BiTemporal.expire`/``to_epoch_s``, which raise bare
    ``ValueError``/``OverflowError`` (a non-finite epoch) or a bare ``ValueError`` (``at`` before
    ``episode.created_at``) -- neither is a :class:`~egpt.memory.types.MemoryOperationError`, so a
    caller catching this module's own error type would not catch either. Raising it here, rather
    than through ``expire``, is what lets :func:`select_for_forgetting` propagate one error type
    for every failure mode this module defines.
    """
    resolved_at = now_epoch_s() if at is None else at
    for field_name, value in (("at", resolved_at), ("t_invalid", t_invalid)):
        if value is not None and not math.isfinite(value):
            raise MemoryOperationError(
                f"{field_name} must be a finite epoch-second, got {value!r}.",
                field_name=field_name,
            )
    if episode.created_at is not None and resolved_at < episode.created_at:
        raise MemoryOperationError(
            f"at ({resolved_at}) precedes episode.created_at ({episode.created_at}): an episode "
            "cannot be archived before it was created.",
            field_name="at",
        )
    archived = replace(episode)
    # `int(...)` here, not a bare pass-through: `BiTemporal.expire` is typed `Optional[int]`,
    # narrower than what its own `to_epoch_s` call actually accepts (`Union[int, float, str,
    # datetime]`) -- truncating here matches what `to_epoch_s` would do internally, so accepting
    # `float` above changes what callers may pass without changing what gets stored.
    archived.expire(
        expired_at=int(resolved_at),
        t_invalid=int(t_invalid) if t_invalid is not None else None,
    )
    return archived


def select_for_forgetting(
    episodes: Sequence[EpisodicRecord],
    now: Optional[int] = None,
    prune_threshold: float = DEFAULT_PRUNE_THRESHOLD,
    base_half_life_s: float = DEFAULT_BASE_HALF_LIFE_S,
    imp_factor: float = DEFAULT_IMP_FACTOR,
    at: Optional[float] = None,
    t_invalid: Optional[float] = None,
) -> List[EpisodicRecord]:
    """Archive every prune-eligible episode in ``episodes``, in the shape ``Curator.prune``
    establishes: filter a list, return only what the filter selected.

    One clock (``now``) shared across the whole batch, exactly as
    ``MemoryProvider.consolidate`` stamps one ``instant`` up front rather than calling
    ``now_epoch_s()`` per row -- two episodes at the same recency must be judged against the same
    instant, not drift apart across a long scan. ``at`` (the transaction-time stamp
    :func:`forget` writes onto ``expired_at``) defaults from the same clock unless a caller passes
    a different one, so a replayed pass can archive under the instant it actually ran, not the
    instant it is scoring against.

    ``t_invalid`` closes the valid-time axis on every archived row in the batch, the same single
    value for all of them -- there is no per-episode override here, matching ``at``'s one-clock
    treatment: a caller needing different ``t_invalid`` values per episode is expressing something
    :func:`forget` already supports one call at a time, not a gap in this batch API.

    Episodes that are not prune-eligible are simply absent from the result -- there is no "kept"
    half to return, unlike ``Curator._filter_by_age``, because nothing here removes a row from
    anywhere for a caller to reconcile against; an episode not returned is untouched.

    Raises :class:`~egpt.memory.types.MemoryOperationError` -- propagated from
    :func:`is_prune_eligible` when ``prune_threshold`` is out of range, or from :func:`forget`
    when ``at``/``t_invalid`` is non-finite or ``at`` precedes an episode's ``created_at``. Either
    fires on whichever episode in ``episodes`` triggers it first; the surrounding comprehension
    then raises before its ``return`` runs, so the caller gets no list at all, not a partial one --
    there is nothing to roll back either way, since nothing in this module persists anything.
    """
    instant = now_epoch_s() if now is None else now
    stamp = instant if at is None else at
    return [
        forget(episode, at=stamp, t_invalid=t_invalid)
        for episode in episodes
        if is_prune_eligible(episode, instant, prune_threshold, base_half_life_s, imp_factor)
    ]
