"""Inline event segmentation: the SM-R6 v1 prior at write-back (SM-E3 #110).

Every assertion here is on the two things the acceptance criteria name: a stream segments into
more than one episode, and the boundary metadata that decision produced is what actually lands on
the episode row. The async re-segmentation lane (embedding_drift / em_llm / llm) is SM-R7's, not
built here -- there is nothing to assert about it.
"""

from dataclasses import replace

import pytest

from egpt.memory.episodic.segment import (
    DEFAULT_MAX_TURNS_PER_EPISODE,
    DEFAULT_MIN_TURNS_PER_EPISODE,
    DEFAULT_TIME_GAP_S,
    TIME_GAP_METHOD,
    TURN_CAP_METHOD,
    BoundaryDecision,
    SegmentState,
    segment_events,
    segmenter_version,
    stamp_boundaries,
)
from egpt.memory.schemas.episodic import episode_from_event
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryOperationError, Scope
from egpt.memory.writeback import episodes_from_events

SCOPE = Scope(tenant_id="acme", agent_id="assistant", user_id="ada", session_id="s1")
OTHER_SCOPE = Scope(tenant_id="acme", agent_id="assistant", user_id="grace", session_id="s2")

BASE_T = 1_700_000_000


def an_event(occurred_at, **overrides):
    defaults = dict(
        content="hello",
        scope=SCOPE,
        tier=MemoryTier.EPISODIC,
        id=None,
        occurred_at=occurred_at,
    )
    defaults.update(overrides)
    return MemoryEvent(**defaults)


# ---------------------------------------------------------------------------
# segment_events -- the boundary decisions themselves
# ---------------------------------------------------------------------------


def test_first_event_with_no_prior_state_is_always_a_boundary():
    decisions, state = segment_events([an_event(BASE_T)])

    assert decisions == [BoundaryDecision(is_boundary=True, method=TIME_GAP_METHOD, trigger=None, confidence=1.0)]
    assert state == SegmentState(last_occurred_at=BASE_T, turns_since_boundary=1)


def test_events_within_the_gap_are_one_episode():
    events = [an_event(BASE_T), an_event(BASE_T + 10), an_event(BASE_T + 20)]
    decisions, state = segment_events(events, gap_s=900.0, min_turns=1)

    assert [d.is_boundary for d in decisions] == [True, False, False]
    assert state.turns_since_boundary == 3


def test_a_long_idle_gap_opens_a_new_episode():
    """The literal acceptance test: a multi-topic session (a long pause between two clusters of
    turns) segments into more than one episode."""
    events = [
        an_event(BASE_T, content="let's plan the launch"),
        an_event(BASE_T + 30, content="set the date for next tuesday"),
        an_event(BASE_T + 60, content="ok, tuesday it is"),
        an_event(BASE_T + 4000, content="unrelated: is the db backup working"),
        an_event(BASE_T + 4030, content="yes, last one succeeded"),
    ]
    decisions, state = segment_events(events, gap_s=900.0, min_turns=3)

    assert [d.is_boundary for d in decisions] == [True, False, False, True, False]
    boundary = decisions[3]
    assert boundary.method == TIME_GAP_METHOD
    assert boundary.trigger == pytest.approx(3940.0)
    assert boundary.confidence == 1.0
    assert state.turns_since_boundary == 2


def test_gap_exactly_at_the_threshold_is_not_a_boundary():
    """`> gap_s`, not `>= gap_s`: a gap exactly equal to the threshold is still within it."""
    events = [an_event(BASE_T), an_event(BASE_T + 900)]
    decisions, _ = segment_events(events, gap_s=900.0, min_turns=1)

    assert [d.is_boundary for d in decisions] == [True, False]


def test_a_long_gap_below_min_turns_is_suppressed():
    """DECISION.md section 3: unsuppressed, a mid-topic pause fragments one episode into several
    (precision collapses to 0.31). min_turns exists to suppress exactly this."""
    events = [an_event(BASE_T), an_event(BASE_T + 4000)]
    decisions, _ = segment_events(events, gap_s=900.0, min_turns=3)

    assert [d.is_boundary for d in decisions] == [True, False]


def test_turn_cap_fires_even_inside_the_gap_window():
    events = [an_event(BASE_T + i) for i in range(5)]
    decisions, state = segment_events(events, gap_s=900.0, max_turns=3, min_turns=1)

    assert [d.is_boundary for d in decisions] == [True, False, False, True, False]
    cap_boundary = decisions[3]
    assert cap_boundary.method == TURN_CAP_METHOD
    assert cap_boundary.trigger == 3.0
    assert cap_boundary.confidence == 1.0
    assert state.turns_since_boundary == 2


def test_state_carries_a_boundary_decision_across_batches():
    first_batch = [an_event(BASE_T), an_event(BASE_T + 10)]
    decisions_1, state_1 = segment_events(first_batch, gap_s=900.0, min_turns=1)
    assert [d.is_boundary for d in decisions_1] == [True, False]

    second_batch = [an_event(BASE_T + 4000)]
    decisions_2, state_2 = segment_events(second_batch, state=state_1, gap_s=900.0, min_turns=1)

    assert decisions_2 == [BoundaryDecision(is_boundary=True, method=TIME_GAP_METHOD, trigger=3990.0, confidence=1.0)]
    assert state_2 == SegmentState(last_occurred_at=BASE_T + 4000, turns_since_boundary=1)


def test_turn_cap_fires_on_first_event_of_next_batch_when_state_is_full():
    """The only branch that cannot fire within a single batch: a prior batch ends with
    turns_since_boundary already at max_turns, and the very next event -- still inside the gap
    window, so time_gap would not fire on its own -- opens on turn_cap instead."""
    first_batch = [an_event(BASE_T + i) for i in range(3)]
    _, state_1 = segment_events(first_batch, gap_s=900.0, max_turns=3, min_turns=1)
    assert state_1.turns_since_boundary == 3

    second_batch = [an_event(BASE_T + 5)]  # well within the gap window
    decisions_2, state_2 = segment_events(second_batch, state=state_1, gap_s=900.0, max_turns=3, min_turns=1)

    assert decisions_2 == [BoundaryDecision(is_boundary=True, method=TURN_CAP_METHOD, trigger=3.0, confidence=1.0)]
    assert state_2 == SegmentState(last_occurred_at=BASE_T + 5, turns_since_boundary=1)


def test_empty_batch_returns_empty_decisions_and_passes_state_through():
    state_in = SegmentState(last_occurred_at=BASE_T, turns_since_boundary=2)
    decisions, state_out = segment_events([], state=state_in)

    assert decisions == []
    assert state_out is state_in


def test_empty_batch_with_no_state_returns_a_fresh_one():
    decisions, state_out = segment_events([])

    assert decisions == []
    assert state_out == SegmentState()


def test_mixed_scopes_in_one_batch_are_refused():
    events = [an_event(BASE_T, scope=SCOPE), an_event(BASE_T + 1, scope=OTHER_SCOPE)]
    with pytest.raises(MemoryOperationError) as excinfo:
        segment_events(events)
    assert excinfo.value.field_name == "events"


def test_unresolved_occurred_at_is_refused():
    with pytest.raises(MemoryOperationError) as excinfo:
        segment_events([an_event(BASE_T), an_event(None)])
    assert excinfo.value.field_name == "occurred_at"


def test_out_of_order_events_are_refused():
    with pytest.raises(MemoryOperationError) as excinfo:
        segment_events([an_event(BASE_T), an_event(BASE_T - 10)])
    assert excinfo.value.field_name == "occurred_at"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"gap_s": 0.0},
        {"gap_s": -1.0},
        {"gap_s": float("inf")},
        {"gap_s": float("nan")},
        {"max_turns": 0},
        {"min_turns": 0},
        {"min_turns": 5, "max_turns": 3},
    ],
)
def test_invalid_parameters_are_refused(kwargs):
    with pytest.raises(MemoryOperationError):
        segment_events([an_event(BASE_T)], **kwargs)


def test_min_turns_equal_to_max_turns_lets_the_cap_fire_first():
    """When min_turns == max_turns, turns_since_boundary hits the cap at the same event a
    suppressed gap boundary would have first become allowed -- the cap wins because it is checked
    first, so the opening method is turn_cap, not time_gap."""
    events = [
        an_event(BASE_T),
        an_event(BASE_T + 10),
        an_event(BASE_T + 4000),  # well past gap_s, and turns_since_boundary is now 2 == min==max
    ]
    decisions, _ = segment_events(events, gap_s=900.0, max_turns=2, min_turns=2)

    assert [d.is_boundary for d in decisions] == [True, False, True]
    assert decisions[2].method == TURN_CAP_METHOD


def test_boundary_decision_rejects_out_of_range_confidence():
    with pytest.raises(MemoryOperationError) as excinfo:
        BoundaryDecision(is_boundary=True, method=TIME_GAP_METHOD, trigger=None, confidence=2.0)
    assert excinfo.value.field_name == "confidence"


def test_boundary_decision_allows_none_confidence():
    # None means "no decision made a confidence claim" (a continuation) -- must not be rejected
    # as out of range.
    BoundaryDecision(is_boundary=False, method=None, trigger=None, confidence=None)


def test_defaults_match_the_sm_r6_ratified_values():
    """ADR 0003's ratified defaults table: time_gap 900s, max_turns 40, min_turns 3."""
    assert DEFAULT_TIME_GAP_S == 900.0
    assert DEFAULT_MAX_TURNS_PER_EPISODE == 40
    assert DEFAULT_MIN_TURNS_PER_EPISODE == 3


# ---------------------------------------------------------------------------
# stamp_boundaries -- decisions -> EpisodicRecord fields
# ---------------------------------------------------------------------------


def _episode(event_id, occurred_at):
    return episode_from_event(an_event(occurred_at, id=event_id))


def test_stamp_boundaries_writes_boundary_fields_on_an_opening_episode():
    episodes = [_episode("ep-1", BASE_T)]
    decisions = [BoundaryDecision(is_boundary=True, method=TIME_GAP_METHOD, trigger=None, confidence=1.0)]
    version = segmenter_version()

    stamped = stamp_boundaries(episodes, decisions, version=version)

    assert stamped[0].boundary_method == TIME_GAP_METHOD
    assert stamped[0].boundary_trigger is None
    assert stamped[0].boundary_confidence == 1.0
    assert stamped[0].segmenter_version == version
    assert stamped[0].revision == 0
    assert stamped[0].refined is False


def test_stamp_boundaries_leaves_continuation_boundary_fields_null():
    episodes = [_episode("ep-2", BASE_T + 10)]
    decisions = [BoundaryDecision(is_boundary=False, method=None, trigger=None, confidence=None)]

    stamped = stamp_boundaries(episodes, decisions, version=segmenter_version())

    assert stamped[0].boundary_method is None
    assert stamped[0].boundary_trigger is None
    assert stamped[0].boundary_confidence is None
    # segmenter_version and revision/refined are provenance, not boundary facts -- set regardless.
    assert stamped[0].segmenter_version == segmenter_version()
    assert stamped[0].revision == 0
    assert stamped[0].refined is False


def test_stamp_boundaries_overwrites_a_preexisting_refined_row():
    already_refined = replace(_episode("ep-3", BASE_T), revision=2, refined=True, boundary_method="llm")
    decisions = [BoundaryDecision(is_boundary=True, method=TIME_GAP_METHOD, trigger=None, confidence=1.0)]

    stamped = stamp_boundaries([already_refined], decisions, version=segmenter_version())

    assert stamped[0].revision == 0
    assert stamped[0].refined is False
    assert stamped[0].boundary_method == TIME_GAP_METHOD


def test_stamp_boundaries_refuses_a_boundary_decision_with_no_method():
    episodes = [_episode("ep-1", BASE_T)]
    decisions = [BoundaryDecision(is_boundary=True, method=None, trigger=None, confidence=1.0)]

    with pytest.raises(MemoryOperationError) as excinfo:
        stamp_boundaries(episodes, decisions, version=segmenter_version())
    assert excinfo.value.field_name == "decisions"


def test_stamp_boundaries_length_mismatch_is_refused():
    episodes = [_episode("ep-1", BASE_T), _episode("ep-2", BASE_T + 10)]
    decisions = [BoundaryDecision(is_boundary=True, method=TIME_GAP_METHOD, trigger=None, confidence=1.0)]

    with pytest.raises(MemoryOperationError) as excinfo:
        stamp_boundaries(episodes, decisions, version="v")
    assert excinfo.value.field_name == "decisions"


@pytest.mark.parametrize("version", ["", "   "])
def test_stamp_boundaries_refuses_a_blank_version(version):
    episodes = [_episode("ep-1", BASE_T)]
    decisions = [BoundaryDecision(is_boundary=True, method=TIME_GAP_METHOD, trigger=None, confidence=1.0)]

    with pytest.raises(MemoryOperationError) as excinfo:
        stamp_boundaries(episodes, decisions, version=version)
    assert excinfo.value.field_name == "version"


def test_segmenter_version_reflects_its_parameters():
    assert segmenter_version(gap_s=900.0, max_turns=40, min_turns=3) != segmenter_version(
        gap_s=600.0, max_turns=40, min_turns=3
    )


# ---------------------------------------------------------------------------
# episodes_from_events (writeback.py) -- the composed write-back seam
# ---------------------------------------------------------------------------


def test_episodes_from_events_segments_a_multi_topic_session_into_more_than_one_episode():
    events = [
        an_event(BASE_T, content="plan the launch"),
        an_event(BASE_T + 30, content="set the date"),
        an_event(BASE_T + 4000, content="unrelated: db backup check"),
        an_event(BASE_T + 4030, content="backup looks fine"),
    ]

    episodes, state = episodes_from_events(events, gap_s=900.0, min_turns=2)

    assert len(episodes) == 4
    opened = [e.boundary_method is not None for e in episodes]
    assert opened == [True, False, True, False]
    assert state.turns_since_boundary == 2


def test_episodes_from_events_stamps_ids_when_the_caller_left_them_unset():
    events = [an_event(BASE_T, id=None), an_event(BASE_T + 10, id=None)]
    episodes, _ = episodes_from_events(events, min_turns=1)

    ids = [e.id for e in episodes]
    assert all(ids)
    assert len(set(ids)) == 2


def test_episodes_from_events_stamps_a_whitespace_only_id():
    """A whitespace-only id is truthy (`if event.id`), so a caller who passes one must not slip
    past the stamping guard and reach episode_from_event's own (differently worded) refusal."""
    events = [an_event(BASE_T, id="   ")]
    episodes, _ = episodes_from_events(events)

    assert episodes[0].id != "   "
    assert episodes[0].id.strip()


def test_episodes_from_events_resolves_missing_occurred_at_to_one_shared_now():
    events = [an_event(None, id="a"), an_event(None, id="b")]
    episodes, _ = episodes_from_events(events, min_turns=1)

    assert episodes[0].occurred_at == episodes[1].occurred_at
    # Same shared "now" also means no manufactured gap between them.
    assert episodes[1].boundary_method is None


def test_episodes_from_events_mixing_none_and_older_explicit_timestamp_is_out_of_order():
    """The footgun the docstring warns about: a None-timestamped event resolves to "now", which
    is after an explicit, older timestamp elsewhere in the same batch -- an out-of-order rejection
    the message now attributes to the null resolution, not just to "index 1 precedes index 0"."""
    old_explicit = an_event(BASE_T, id="old")  # far in the past relative to "now"
    unstamped = an_event(None, id="unstamped")

    with pytest.raises(MemoryOperationError) as excinfo:
        episodes_from_events([unstamped, old_explicit], min_turns=1)

    assert excinfo.value.field_name == "occurred_at"
    assert "resolved occurred_at=None" in str(excinfo.value)


def test_episodes_from_events_validates_params_even_with_an_all_non_episodic_batch():
    semantic_event = an_event(BASE_T, tier=MemoryTier.SEMANTIC, id="sem-1")

    with pytest.raises(MemoryOperationError) as excinfo:
        episodes_from_events([semantic_event], gap_s=-1.0)
    assert excinfo.value.field_name == "gap_s"


def test_episodes_from_events_drops_non_episodic_events():
    semantic_event = an_event(BASE_T, tier=MemoryTier.SEMANTIC, id="sem-1")
    episodic_event = an_event(BASE_T + 10, id="ep-1")

    episodes, _ = episodes_from_events([semantic_event, episodic_event])

    assert len(episodes) == 1
    assert episodes[0].id == "ep-1"


def test_episodes_from_events_empty_batch_returns_empty_list_and_passes_state_through():
    state_in = SegmentState(last_occurred_at=BASE_T, turns_since_boundary=2)
    episodes, state_out = episodes_from_events([], state=state_in)

    assert episodes == []
    assert state_out is state_in


def test_episodes_from_events_all_non_episodic_returns_empty_list():
    events = [an_event(BASE_T, tier=MemoryTier.SEMANTIC, id="sem-1")]
    episodes, state_out = episodes_from_events(events)

    assert episodes == []
    assert state_out == SegmentState()


def test_episodes_from_events_threads_state_across_two_write_back_calls():
    first_batch = [an_event(BASE_T, id="a"), an_event(BASE_T + 10, id="b")]
    episodes_1, state_1 = episodes_from_events(first_batch, gap_s=900.0, min_turns=1)
    assert [e.boundary_method is not None for e in episodes_1] == [True, False]

    second_batch = [an_event(BASE_T + 4000, id="c")]
    episodes_2, _ = episodes_from_events(second_batch, state=state_1, gap_s=900.0, min_turns=1)

    assert episodes_2[0].boundary_method == TIME_GAP_METHOD
    assert episodes_2[0].boundary_trigger == pytest.approx(3990.0)
