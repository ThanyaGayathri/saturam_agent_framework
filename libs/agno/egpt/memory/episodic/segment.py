"""Inline event segmentation: the SM-R6 v1 prior (time-gap + turn cap), applied at write-back
(SM-E3 #110).

``benchmarks/memory_event_segmentation/DECISION.md`` section 1 splits segmentation into two
lanes. This module is the **inline** one only: ``time_gap`` (default 900s) plus a hard
``max_turns_per_episode`` cap (default 40), suppressed below ``min_turns_per_episode`` (default 3)
so a user's mid-topic pause does not fragment one episode into several micro-episodes. It is O(1),
deterministic, and makes no model or embedding call -- ADR 0003's write flow: *"Inline segmentation
prior only... no model call (SM-R6 section 4)."*

**Not built here, by the same section's own hand-off:** the async re-segmentation lane
(``embedding_drift`` / ``surprise_em_llm`` / ``llm_boundary``, plus the withdrawn hybrid) runs on
the consolidation lane against a real embedder or model, under the stable-id + supersede/revision
identity contract DECISION.md section 4 defines -- that is SM-R7's, not this ticket's: it needs a
production embedder/model call to validate against, which this ticket's acceptance criteria do not
give it (a benchmark double is not a real backend). ``revision``/``supersedes``/``superseded_by``
stay at their row defaults here for the same reason: nothing has re-cut anything yet.

**"Turn" here means one write-back :class:`~egpt.memory.types.MemoryEvent`** -- the seam's own
atomic write-back unit -- not a conversational turn. :func:`~egpt.memory.writeback.events_from_run`
can emit several events for one real turn (a message plus each tool result), so
``max_turns_per_episode`` undercounts true turns when a turn is chatty. DECISION.md does not
specify a finer mapping, and inventing per-turn grouping on top of write-back's flat event list is
its own feature, not a documented approximation of this one -- flagged here rather than silently
assumed.

A boundary is decided **per scope** (tenant/user/session -- :meth:`~egpt.memory.types.Scope.key`):
segmenting a batch that mixes scopes would compare one conversation's idle gaps against another's,
which is not what either input represents. :func:`segment_events` refuses a mixed-scope batch
rather than silently segmenting only the first scope it sees.
"""

import math
from dataclasses import dataclass, replace
from typing import Final, List, Optional, Sequence, Tuple

from egpt.memory.schemas.episodic import EpisodicRecord
from egpt.memory.types import MemoryEvent, MemoryOperationError

__all__ = [
    "DEFAULT_MAX_TURNS_PER_EPISODE",
    "DEFAULT_MIN_TURNS_PER_EPISODE",
    "DEFAULT_TIME_GAP_S",
    "TIME_GAP_METHOD",
    "TURN_CAP_METHOD",
    "BoundaryDecision",
    "SegmentState",
    "segment_events",
    "segmenter_version",
    "stamp_boundaries",
]

#: DECISION.md section 1's provisional defaults ("SM-R1 to ratify"; ADR 0003's table ratifies
#: them as-is).
DEFAULT_TIME_GAP_S: float = 900.0
DEFAULT_MAX_TURNS_PER_EPISODE: int = 40
DEFAULT_MIN_TURNS_PER_EPISODE: int = 3

#: The two ``boundary_method`` values this module ever writes -- the other four in DECISION.md
#: section 5's enum (``embedding_drift`` / ``em_llm`` / ``llm`` / ``manual``) belong to the async
#: lane or a human, not the inline prior.
TIME_GAP_METHOD: Final[str] = "time_gap"
TURN_CAP_METHOD: Final[str] = "turn_cap"


@dataclass(frozen=True)
class BoundaryDecision:
    """What :func:`segment_events` decided for one event, aligned 1:1 with its input.

    ``method``/``trigger``/``confidence`` are ``None`` on a continuation (``is_boundary=False``):
    nothing opened, so there is nothing to attribute the non-event to. :func:`stamp_boundaries`
    writes this pair straight onto ``EpisodicRecord.boundary_*``, which is nullable for exactly
    this reason.
    """

    is_boundary: bool
    method: Optional[str]
    trigger: Optional[float]
    confidence: Optional[float]


@dataclass(frozen=True)
class SegmentState:
    """The carry between write-back batches a scope's segmentation needs.

    A single write-back call usually shares one ``occurred_at`` across its whole batch (see
    :func:`~egpt.memory.writeback.events_from_run`), so a time gap only ever shows up **between**
    calls -- this is what lets the next call see it. ``turns_since_boundary`` is the count of
    events written since the last boundary (inclusive of the boundary event itself), which is what
    ``max_turns_per_episode`` compares against.

    ``last_occurred_at=None`` together with ``turns_since_boundary > 0`` is a state
    :func:`segment_events` never produces but does not reject either: with no previous timestamp to
    compare against, the next event always takes the "no prior state" branch (a fresh boundary),
    which silently discards the carried count rather than using it. The default ``SegmentState()``
    (both fields at their zero value) is always consistent; a hand-built or deserialized state
    should keep the two fields in step with each other.
    """

    last_occurred_at: Optional[int] = None
    turns_since_boundary: int = 0


def segmenter_version(
    gap_s: float = DEFAULT_TIME_GAP_S,
    max_turns: int = DEFAULT_MAX_TURNS_PER_EPISODE,
    min_turns: int = DEFAULT_MIN_TURNS_PER_EPISODE,
) -> str:
    """The ``segmenter_version`` DECISION.md section 5 asks every episode to carry.

    Derived from the actual parameters rather than a bare literal -- a hard-coded string here and
    a changed default in :func:`segment_events` is the same drift SM-V1 shipped with a hard-coded
    port number: two places that can say different things about one run eventually do.
    """
    return f"time_gap_turn_cap-v1:gap_s={gap_s}:max_turns={max_turns}:min_turns={min_turns}"


def _validate_params(gap_s: float, max_turns: int, min_turns: int) -> None:
    if not math.isfinite(gap_s) or gap_s <= 0:
        raise MemoryOperationError(
            f"gap_s must be a finite positive number, got {gap_s!r}: zero or negative would call "
            "every event its own episode regardless of the actual idle time, and infinite (or "
            "NaN) would silently disable time-gap detection -- `not gap_s > 0` alone lets "
            "`float('inf')` through, the same class of guard decay.py's half_life_s applies to "
            "base_half_life_s.",
            field_name="gap_s",
        )
    if max_turns < 1:
        raise MemoryOperationError(
            f"max_turns must be at least 1, got {max_turns!r}: an episode needs at least the event that opens it.",
            field_name="max_turns",
        )
    if min_turns < 1:
        raise MemoryOperationError(
            f"min_turns must be at least 1, got {min_turns!r}: the boundary event itself is "
            "always the first turn of its episode.",
            field_name="min_turns",
        )
    if min_turns > max_turns:
        raise MemoryOperationError(
            f"min_turns ({min_turns!r}) exceeds max_turns ({max_turns!r}): the cap would fire "
            "before a suppressed time-gap boundary is ever allowed to.",
            field_name="min_turns",
        )


def segment_events(
    events: Sequence[MemoryEvent],
    *,
    state: Optional[SegmentState] = None,
    gap_s: float = DEFAULT_TIME_GAP_S,
    max_turns: int = DEFAULT_MAX_TURNS_PER_EPISODE,
    min_turns: int = DEFAULT_MIN_TURNS_PER_EPISODE,
) -> Tuple[List[BoundaryDecision], SegmentState]:
    """Apply the time-gap + turn-cap prior to one scope's ordered events.

    ``events`` must already be in non-decreasing ``occurred_at`` order and share one
    :meth:`~egpt.memory.types.Scope.key` -- both refused rather than silently re-sorted or
    partitioned, because either would mean this function decided something about the caller's data
    the caller did not ask it to. Every ``occurred_at`` must be resolved (non-``None``): a time-gap
    prior cannot compare against an unstamped timestamp, and resolving one "now" per unstamped
    event here (rather than refusing) would make the gap depend on wall-clock jitter between
    events that a caller built back-to-back for one batch.

    Boundary rules, in priority order, per event:

    1. **No prior state** (``state is None`` or this is the scope's first-ever segmented event):
       always a boundary. There is no previous event to measure a gap against, so this is
       ``time_gap`` with ``trigger=None`` -- a gap of "everything before is unknown", not zero.
    2. **``turns_since_boundary >= max_turns``**: always a boundary (``turn_cap``), even inside
       what would otherwise be a suppressed gap -- the cap is a hard ceiling on episode length,
       not a preference.
    3. **Idle gap ``> gap_s``**: a boundary (``time_gap``) *unless* fewer than ``min_turns`` have
       elapsed since the last one, in which case it is suppressed -- DECISION.md section 1: "Its
       job is a cheap, deterministic pre-segmentation," not a precise oracle, and section 3
       measures precision collapsing to 0.31 on exactly this pattern (a long pause mid-topic)
       without the suppression.
    4. Otherwise: a continuation.

    Returns the decisions aligned 1:1 with ``events``, and the :class:`SegmentState` the next
    batch for this scope should pass back in.
    """
    _validate_params(gap_s, max_turns, min_turns)
    if not events:
        return [], state or SegmentState()

    scope_keys = {event.scope.key() for event in events}
    if len(scope_keys) > 1:
        raise MemoryOperationError(
            f"segment_events received {len(scope_keys)} distinct scopes in one batch: "
            f"{sorted(scope_keys)}. Segment each scope's events separately -- comparing idle gaps "
            "across conversations is not a boundary decision this function can make for you.",
            field_name="events",
        )

    last_occurred_at = state.last_occurred_at if state is not None else None
    turns_since_boundary = state.turns_since_boundary if state is not None else 0

    decisions: List[BoundaryDecision] = []
    previous_occurred_at = last_occurred_at
    for index, event in enumerate(events):
        occurred_at = event.occurred_at
        if occurred_at is None:
            raise MemoryOperationError(
                f"Event at index {index} has no occurred_at: segment_events needs every event's "
                "valid time resolved before it can measure an idle gap. Resolve it (e.g. default "
                "to now) before segmenting, not after.",
                field_name="occurred_at",
            )
        if previous_occurred_at is not None and occurred_at < previous_occurred_at:
            raise MemoryOperationError(
                f"Event at index {index} has occurred_at={occurred_at!r}, before the preceding "
                f"one ({previous_occurred_at!r}). segment_events requires non-decreasing order -- "
                "sort the batch first rather than asking this function to guess the intent of an "
                "out-of-order stream.",
                field_name="occurred_at",
            )

        if previous_occurred_at is None:
            decision = BoundaryDecision(is_boundary=True, method=TIME_GAP_METHOD, trigger=None, confidence=1.0)
        elif turns_since_boundary >= max_turns:
            decision = BoundaryDecision(
                is_boundary=True,
                method=TURN_CAP_METHOD,
                trigger=float(turns_since_boundary),
                confidence=1.0,
            )
        else:
            gap = float(occurred_at - previous_occurred_at)
            if gap > gap_s and turns_since_boundary >= min_turns:
                decision = BoundaryDecision(is_boundary=True, method=TIME_GAP_METHOD, trigger=gap, confidence=1.0)
            else:
                decision = BoundaryDecision(is_boundary=False, method=None, trigger=None, confidence=None)

        decisions.append(decision)
        turns_since_boundary = 1 if decision.is_boundary else turns_since_boundary + 1
        previous_occurred_at = occurred_at

    return decisions, SegmentState(last_occurred_at=previous_occurred_at, turns_since_boundary=turns_since_boundary)


def stamp_boundaries(
    episodes: Sequence[EpisodicRecord],
    decisions: Sequence[BoundaryDecision],
    *,
    version: str,
) -> List[EpisodicRecord]:
    """Write :func:`segment_events`' decisions onto the matching episode rows.

    ``version`` is stamped onto every row, boundary or not -- ``segmenter_version`` documents
    which algorithm decided the row's episodic membership at all, not only the rows where it opened
    something. ``revision=0`` and ``refined=False`` on every row: this is the inline prior, so
    nothing has been re-cut yet regardless of what ``episodes`` already carried -- a caller
    re-stamping an already-refined row through this function has a bug upstream, not a case this
    function should paper over by leaving the old values in place.

    Positional, not by id: :func:`segment_events` returns decisions in the same order as the events
    it was given, and this function trusts that pairing rather than re-deriving it, so ``episodes``
    and ``decisions`` must be built from the same ordered event sequence.

    ``decision.method`` is the value actually written to ``boundary_method`` -- ``is_boundary`` is
    not consulted for that field, only asserted against it below. :func:`segment_events` never
    produces a ``BoundaryDecision`` where the two disagree, so this is a defensive check against a
    hand-built or future ``BoundaryDecision`` (e.g. a boundary with no label) reaching this
    function and being stamped as a silent continuation.
    """
    if len(episodes) != len(decisions):
        raise MemoryOperationError(
            f"stamp_boundaries received {len(episodes)} episode(s) but {len(decisions)} "
            "decision(s). Both must come from the same ordered event sequence -- build "
            "`decisions` with segment_events over the same events episode_from_event built "
            "`episodes` from.",
            field_name="decisions",
        )
    stamped = []
    for episode, decision in zip(episodes, decisions):
        if decision.is_boundary and decision.method is None:
            raise MemoryOperationError(
                f"Episode {episode.id!r} carries a BoundaryDecision with is_boundary=True but "
                "method=None. stamp_boundaries writes `method` as the authoritative "
                "boundary_method, so an unlabelled boundary would be stamped as a silent "
                "continuation (boundary_method=None) -- the caller building BoundaryDecision "
                "has a bug, not this function.",
                field_name="decisions",
            )
        stamped.append(
            replace(
                episode,
                boundary_method=decision.method,
                boundary_trigger=decision.trigger,
                boundary_confidence=decision.confidence,
                segmenter_version=version,
                revision=0,
                refined=False,
            )
        )
    return stamped
