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


def an_episode(id: str, **overrides) -> EpisodicRecord:
    defaults = dict(tenant_id="acme", content=f"episode {id}", created_at=0)
    defaults.update(overrides)
    return EpisodicRecord(id=id, **defaults)


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
    session = a_session(5)
    hit = session[2]
    neighbors = temporal_neighbors(hit, session, radius=10)
    assert hit.id not in {n.episode.id for n in neighbors}


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


def test_buffer_never_exceeds_capacity_across_many_enqueue_calls():
    buffer = ContiguityBuffer(capacity=4)
    session = a_session(20)
    for ep in session:
        buffer.enqueue([ContiguityNeighbor(episode=ep, distance=1, hit_id="hit")])
        assert len(buffer) <= 4


def test_buffer_deduplicates_a_neighbor_shared_by_two_hits():
    session = a_session(6)  # ep-0 .. ep-5
    buffer = ContiguityBuffer(capacity=10)
    # Adjacent hits at radius 2 share neighbors: ep-2's radius-2 neighborhood includes ep-1/ep-4,
    # and so does ep-3's (ep-1 at distance -2, ep-4 at distance +1).
    buffer.enqueue(temporal_neighbors(session[2], session, radius=2))
    assert len(buffer) == 4  # ep-0, ep-1, ep-3, ep-4
    evicted = buffer.enqueue(temporal_neighbors(session[3], session, radius=2))
    # session[3]'s 4 neighbors are ep-1, ep-2, ep-4, ep-5 -- ep-1 and ep-4 are already buffered
    # from session[2]'s enqueue, so only ep-2 and ep-5 are actually new.
    assert len(buffer) == 6
    assert evicted == []


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
