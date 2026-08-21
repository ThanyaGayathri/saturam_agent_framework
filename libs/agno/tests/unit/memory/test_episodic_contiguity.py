"""Temporal-contiguity retrieval (SM-E8 #115): the +/- n neighbors of a retrieval hit, and the
bounded buffer that accumulates them across a pass (EM-LLM's "bounded queue (implicit)").

Every assertion pins the acceptance criteria: neighbors are found relative to a hit
(:func:`temporal_neighbors`), the buffer is bounded (:class:`ContiguityBuffer`), and blending a
buffered neighbor's distance into its composite score (:func:`blend_contiguity`, tested in
``test_episodic_score.py``) measurably improves temporal recall on a fixture -- the last section
below.
"""

import pytest

from egpt.memory.episodic.contiguity import (
    DEFAULT_CONTIGUITY_RADIUS,
    ContiguityBuffer,
    ContiguityNeighbor,
    temporal_neighbors,
    temporal_order_key,
)
from egpt.memory.episodic.score import (
    DEFAULT_EPISODIC_SCORE_WEIGHTS,
    EpisodicScoreWeights,
    blend_contiguity,
    composite_episodic_score,
)
from egpt.memory.schemas.episodic import EpisodicRecord
from egpt.memory.types import MemoryOperationError

HOUR = 3600


def an_episode(episode_id: str, **overrides) -> EpisodicRecord:
    defaults = dict(tenant_id="acme", content=f"episode {episode_id}", created_at=0)
    defaults.update(overrides)
    return EpisodicRecord(id=episode_id, **defaults)


def a_session(count: int, start: int = 0, step: int = HOUR):
    """``count`` episodes in strict temporal order, one per ``step`` seconds from ``start``."""
    return [an_episode(f"ep-{i}", occurred_at=start + i * step, created_at=start + i * step) for i in range(count)]


# -- temporal_order_key -------------------------------------------------------------------------


def test_order_key_uses_occurred_at_when_set():
    episode = an_episode("ep-1", occurred_at=100, created_at=999)
    assert temporal_order_key(episode) == (100, "ep-1")


def test_order_key_falls_back_to_created_at():
    episode = an_episode("ep-1", occurred_at=None, created_at=42)
    assert temporal_order_key(episode) == (42, "ep-1")


def test_order_key_rejects_an_episode_with_neither_timestamp():
    # TenantScoped.__post_init__ auto-stamps created_at whenever it is None at construction, so
    # the only way to reach "neither set" is a post-construction mutation -- EpisodicRecord is
    # not frozen, the same scenario test_episodic_decay.py's equivalent test relies on.
    episode = an_episode("ep-1", occurred_at=None)
    episode.created_at = None
    with pytest.raises(MemoryOperationError) as exc_info:
        temporal_order_key(episode)
    assert exc_info.value.field_name == "created_at"


# -- temporal_neighbors: neighbors are retrieved with a hit --------------------------------------


def test_finds_neighbors_on_both_sides_within_radius():
    session = a_session(7)  # ep-0 .. ep-6
    hit = session[3]  # ep-3
    neighbors = temporal_neighbors(hit, session, radius=2)
    by_id = {n.episode.id: n.distance for n in neighbors}
    assert by_id == {"ep-1": -2, "ep-2": -1, "ep-4": 1, "ep-5": 2}


def test_single_element_pool_has_no_neighbors():
    hit = an_episode("ep-1", occurred_at=0, created_at=0)
    assert temporal_neighbors(hit, [hit], radius=5) == []


def test_falls_back_to_created_at_for_a_mixed_pool():
    # A pool where only some episodes carry occurred_at -- the ones that don't still order (and
    # neighbor) correctly by created_at, not by being sorted arbitrarily or excluded.
    hit = an_episode("ep-2", occurred_at=2 * HOUR, created_at=2 * HOUR)
    session = [
        an_episode("ep-0", occurred_at=None, created_at=0),
        an_episode("ep-1", occurred_at=1 * HOUR, created_at=1 * HOUR),
        hit,
        an_episode("ep-3", occurred_at=None, created_at=3 * HOUR),
        an_episode("ep-4", occurred_at=4 * HOUR, created_at=4 * HOUR),
    ]
    neighbors = temporal_neighbors(hit, session, radius=2)
    by_id = {n.episode.id: n.distance for n in neighbors}
    assert by_id == {"ep-0": -2, "ep-1": -1, "ep-3": 1, "ep-4": 2}


def test_neighbors_are_ordered_nearest_to_farthest_before_then_after():
    session = a_session(7)
    hit = session[3]
    neighbors = temporal_neighbors(hit, session, radius=2)
    assert [(n.episode.id, n.distance) for n in neighbors] == [
        ("ep-2", -1),
        ("ep-4", 1),
        ("ep-1", -2),
        ("ep-5", 2),
    ]


def test_every_neighbor_carries_the_hit_id():
    session = a_session(5)
    hit = session[2]
    neighbors = temporal_neighbors(hit, session, radius=1)
    assert all(n.hit_id == hit.id for n in neighbors)


def test_never_includes_the_hit_itself():
    session = a_session(5)  # ep-0 .. ep-4
    hit = session[2]
    neighbors = temporal_neighbors(hit, session, radius=10)
    assert hit.id not in {n.episode.id for n in neighbors}
    # radius=10 against a 5-episode session clips at both edges (see
    # test_clips_at_the_start_of_the_session / test_clips_at_the_end_of_the_session for the
    # single-edge cases) -- every remaining episode becomes a neighbor at its true distance from
    # the hit, not a clamped or truncated one.
    by_id = {n.episode.id: n.distance for n in neighbors}
    assert by_id == {"ep-0": -2, "ep-1": -1, "ep-3": 1, "ep-4": 2}


def test_clips_at_the_start_of_the_session():
    session = a_session(5)  # ep-0 .. ep-4
    hit = session[1]  # ep-1: only one episode before it
    neighbors = temporal_neighbors(hit, session, radius=3)
    before = sorted(n.episode.id for n in neighbors if n.distance < 0)
    assert before == ["ep-0"]


def test_clips_at_the_end_of_the_session():
    session = a_session(5)  # ep-0 .. ep-4
    hit = session[3]  # ep-3: only one episode after it
    neighbors = temporal_neighbors(hit, session, radius=3)
    after = sorted(n.episode.id for n in neighbors if n.distance > 0)
    assert after == ["ep-4"]


def test_sorts_an_out_of_order_candidate_pool_before_locating_neighbors():
    # A caller forwarding a scan page (transaction-time order) or an unordered candidate list
    # must still get the neighbors that are correct in *temporal* (occurred_at) order.
    session = a_session(5)
    hit = session[2]
    shuffled = [session[4], session[0], session[2], session[3], session[1]]
    neighbors = temporal_neighbors(hit, shuffled, radius=1)
    by_id = {n.episode.id: n.distance for n in neighbors}
    assert by_id == {"ep-1": -1, "ep-3": 1}


def test_radius_zero_returns_no_neighbors():
    session = a_session(5)
    hit = session[2]
    assert temporal_neighbors(hit, session, radius=0) == []


def test_default_radius_is_two():
    assert DEFAULT_CONTIGUITY_RADIUS == 2


def test_rejects_a_negative_radius():
    session = a_session(5)
    hit = session[2]
    with pytest.raises(MemoryOperationError) as exc_info:
        temporal_neighbors(hit, session, radius=-1)
    assert exc_info.value.field_name == "radius"


def test_rejects_a_hit_absent_from_the_candidate_pool():
    session = a_session(5)
    stranger = an_episode("ep-stranger", occurred_at=10 * HOUR, created_at=10 * HOUR)
    with pytest.raises(MemoryOperationError) as exc_info:
        temporal_neighbors(stranger, session, radius=1)
    assert exc_info.value.field_name == "hit"


def test_rejects_a_hit_against_an_empty_pool():
    hit = an_episode("ep-1", occurred_at=0, created_at=0)
    with pytest.raises(MemoryOperationError) as exc_info:
        temporal_neighbors(hit, [], radius=1)
    assert exc_info.value.field_name == "hit"


def test_rejects_a_duplicate_episode_id_in_the_pool():
    session = a_session(5)
    duplicate = an_episode("ep-2", occurred_at=99 * HOUR, created_at=99 * HOUR)
    with pytest.raises(MemoryOperationError) as exc_info:
        temporal_neighbors(session[0], [*session, duplicate], radius=1)
    assert exc_info.value.field_name == "episodes"


def test_radius_zero_skips_the_duplicate_id_check():
    # radius=0 never builds index_by_id (no neighbors to locate), so a duplicate elsewhere in the
    # pool that a nonzero radius would refuse is not this call's problem to reconcile.
    session = a_session(5)
    duplicate = an_episode("ep-3", occurred_at=99 * HOUR, created_at=99 * HOUR)
    assert temporal_neighbors(session[0], [*session, duplicate], radius=0) == []


def test_radius_zero_still_rejects_a_hit_absent_from_the_pool():
    session = a_session(5)
    stranger = an_episode("ep-stranger", occurred_at=10 * HOUR, created_at=10 * HOUR)
    with pytest.raises(MemoryOperationError) as exc_info:
        temporal_neighbors(stranger, session, radius=0)
    assert exc_info.value.field_name == "hit"


# -- ContiguityBuffer: the buffer is bounded ------------------------------------------------------


def test_buffer_accumulates_enqueued_neighbors():
    session = a_session(5)
    buffer = ContiguityBuffer(capacity=10)
    buffer.enqueue(temporal_neighbors(session[2], session, radius=1))
    assert len(buffer) == 2
    assert "ep-1" in buffer
    assert "ep-3" in buffer


def test_buffer_evicts_oldest_first_past_capacity():
    buffer = ContiguityBuffer(capacity=3)
    session = a_session(10)
    neighbors = [ContiguityNeighbor(episode=ep, distance=1, hit_id="hit") for ep in session]
    evicted = buffer.enqueue(neighbors)
    assert len(buffer) == 3
    # The first 7 enqueued (of 10) are evicted, oldest (ep-0) first.
    assert [n.episode.id for n in evicted] == [f"ep-{i}" for i in range(7)]
    assert {n.episode.id for n in buffer.neighbors()} == {"ep-7", "ep-8", "ep-9"}


def test_buffer_capacity_one_evicts_immediately_on_the_second_distinct_id():
    buffer = ContiguityBuffer(capacity=1)
    session = a_session(3)
    evicted_first = buffer.enqueue([ContiguityNeighbor(episode=session[0], distance=1, hit_id="hit")])
    assert len(buffer) == 1
    assert evicted_first == []
    evicted_second = buffer.enqueue([ContiguityNeighbor(episode=session[1], distance=1, hit_id="hit")])
    assert len(buffer) == 1
    assert [n.episode.id for n in evicted_second] == ["ep-0"]
    assert {n.episode.id for n in buffer.neighbors()} == {"ep-1"}


def test_buffer_never_exceeds_capacity_across_many_enqueue_calls():
    buffer = ContiguityBuffer(capacity=4)
    session = a_session(20)
    for ep in session:
        buffer.enqueue([ContiguityNeighbor(episode=ep, distance=1, hit_id="hit")])
        assert len(buffer) <= 4


def test_buffer_deduplicates_a_neighbor_shared_by_two_hits():
    session = a_session(6)  # ep-0 .. ep-5
    buffer = ContiguityBuffer(capacity=10)
    # session[2]'s radius-2 neighborhood is ep-0/ep-1/ep-3/ep-4; session[3]'s is
    # ep-1/ep-2/ep-4/ep-5. ep-1 and ep-4 are in *both* neighborhoods -- the shared neighbors this
    # test's dedup assertion below is about.
    buffer.enqueue(temporal_neighbors(session[2], session, radius=2))
    assert len(buffer) == 4  # ep-0, ep-1, ep-3, ep-4
    evicted = buffer.enqueue(temporal_neighbors(session[3], session, radius=2))
    # session[3]'s 4 neighbors are ep-1, ep-2, ep-4, ep-5 -- ep-1 and ep-4 are already buffered
    # from session[2]'s enqueue, so only ep-2 and ep-5 are actually new.
    assert len(buffer) == 6
    assert evicted == []
    # First-occurrence-wins: ep-1/ep-4 keep the distance/hit_id session[2]'s enqueue gave them
    # (distance -1/+2 from ep-2), not session[3]'s (which would have been -2/+1 from ep-3).
    by_id = {n.episode.id: n for n in buffer.neighbors()}
    assert by_id["ep-1"].hit_id == session[2].id
    assert by_id["ep-1"].distance == -1
    assert by_id["ep-4"].hit_id == session[2].id
    assert by_id["ep-4"].distance == 2


def test_buffer_rejects_nonpositive_capacity():
    for bad_capacity in (0, -1):
        with pytest.raises(MemoryOperationError) as exc_info:
            ContiguityBuffer(capacity=bad_capacity)
        assert exc_info.value.field_name == "capacity"


def test_buffer_contains_and_neighbors_reflect_state():
    buffer = ContiguityBuffer(capacity=5)
    assert len(buffer) == 0
    assert buffer.neighbors() == []
    episode = an_episode("ep-1", occurred_at=0, created_at=0)
    buffer.enqueue([ContiguityNeighbor(episode=episode, distance=1, hit_id="hit")])
    assert "ep-1" in buffer
    assert "ep-missing" not in buffer
    assert len(buffer.neighbors()) == 1


def test_buffer_contains_a_non_string_object_is_false_not_a_type_error():
    # __contains__ is typed `object`, matching Python's protocol -- a caller checking membership
    # of something that isn't an episode id string gets False, not a TypeError. An unhashable
    # value (a list) is the case that actually distinguishes this from a bare `in self._by_id`:
    # a plain dict membership check raises TypeError on an unhashable key, where this method
    # must not.
    buffer = ContiguityBuffer(capacity=5)
    assert 42 not in buffer
    assert None not in buffer
    assert object() not in buffer
    assert ["ep-1"] not in buffer


# -- Improves temporal recall on a fixture ---------------------------------------------------------


def test_contiguity_blend_improves_temporal_recall_on_a_fixture():
    """The AC's own wording, made executable.

    ``hit`` (ep-2) is retrieved by relevance alone. ``continuation`` (ep-3) is its immediate
    temporal successor -- the "what happened next" episode a human's temporal-order recall would
    surface -- but scores low on relevance/importance in isolation. ``distractor`` is an
    unrelated episode that is NOT a temporal neighbor of any hit, scored just high enough to beat
    ``continuation`` on relevance/importance alone.

    Without contiguity, the distractor outranks the continuation: similarity-only retrieval loses
    the temporal-order signal EM-LLM's contiguity buffer exists to recover. Blending
    ``continuation``'s buffered distance from the hit (:func:`blend_contiguity`) into its
    composite score reverses that ranking -- the fixture this ticket's acceptance criterion asks
    for.
    """
    weights = EpisodicScoreWeights(relevance=1.0, recency=0.0, importance=1.0)

    hit = an_episode("ep-2", occurred_at=2 * HOUR, created_at=2 * HOUR)
    continuation = an_episode("ep-3", occurred_at=3 * HOUR, created_at=3 * HOUR)
    distractor = an_episode("ep-distractor", occurred_at=99 * HOUR, created_at=99 * HOUR)
    session = [hit, continuation, distractor]

    continuation_base = composite_episodic_score(0.2, recency=0.0, importance=0.3, weights=weights)
    distractor_base = composite_episodic_score(0.3, recency=0.0, importance=0.35, weights=weights)

    # Without the contiguity blend, the unrelated distractor wins.
    assert distractor_base > continuation_base

    neighbors = temporal_neighbors(hit, session, radius=1)
    continuation_neighbor = next(n for n in neighbors if n.episode.id == "ep-3")
    assert continuation_neighbor.distance == 1

    continuation_blended = blend_contiguity(continuation_base, continuation_neighbor.distance)

    # With the blend, the temporal continuation of the hit outranks the distractor -- improved
    # temporal recall.
    assert continuation_blended > distractor_base


def test_contiguity_blend_default_weight_does_not_overwhelm_a_directly_relevant_hit():
    # A neighbor's bonus must stay small enough that it cannot outrank a genuinely more relevant,
    # more important episode under the default composite weights -- contiguity augments ranking,
    # it does not dominate it.
    directly_relevant = composite_episodic_score(
        0.9, recency=0.0, importance=0.9, weights=DEFAULT_EPISODIC_SCORE_WEIGHTS
    )
    weak_neighbor_base = composite_episodic_score(
        0.1, recency=0.0, importance=0.1, weights=DEFAULT_EPISODIC_SCORE_WEIGHTS
    )
    weak_neighbor_blended = blend_contiguity(weak_neighbor_base, distance=1)
    assert directly_relevant > weak_neighbor_blended
