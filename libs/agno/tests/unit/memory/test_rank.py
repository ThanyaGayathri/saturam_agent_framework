"""Time-aware blended ranking (SM-H5 #135): the pure ranking function, independent of any
provider.

Mirrors ``test_fuse.py``'s shape -- this file is only about what :func:`rank` itself decides:
temporal pre-filtering, the SM-E7 composite blend (and its ``recency=0.0`` floor per ADR 0003
B.1), score clamping, and the acceptance criterion the issue names directly (Testing Notes: "a
recent + important + relevant episode ranks correctly").
"""

import pytest

from egpt.memory.episodic.score import EpisodicScoreWeights
from egpt.memory.retrieval.rank import rank
from egpt.memory.types import MemoryOperationError, MemoryQuery, MemoryRecord, MemoryTier, Scope

ACME = Scope(tenant_id="acme")


def episode(id_, score=0.5, importance=0.5, t_valid=None, t_invalid=None, content=None):
    return MemoryRecord(
        id=id_,
        tier=MemoryTier.EPISODIC,
        content=content if content is not None else id_,
        scope=ACME,
        score=score,
        t_valid=t_valid,
        t_invalid=t_invalid,
        metadata={"importance": importance},
    )


def query(**kwargs):
    kwargs.setdefault("text", "q")
    return MemoryQuery(**kwargs)


# -- temporal filter, applied only when the query is explicitly time-scoped -------------------


def test_no_window_leaves_every_record_in_the_candidate_set():
    records = [episode("a", t_valid=100, t_invalid=200), episode("b", t_valid=None, t_invalid=None)]
    ranked = rank(records, query())
    assert {r.id for r in ranked} == {"a", "b"}


def test_a_window_drops_a_record_whose_valid_time_does_not_overlap_it():
    records = [episode("in_window", t_valid=100, t_invalid=200), episode("before_window", t_valid=0, t_invalid=50)]
    ranked = rank(records, query(valid_from=100, valid_to=200))
    assert [r.id for r in ranked] == ["in_window"]


def test_a_record_with_no_valid_time_is_open_ended_and_survives_any_window():
    records = [episode("open_ended", t_valid=None, t_invalid=None)]
    ranked = rank(records, query(valid_from=1_000, valid_to=2_000))
    assert [r.id for r in ranked] == ["open_ended"]


# -- composite blend, episodic only -------------------------------------------------------------


def test_a_more_important_episode_at_equal_relevance_ranks_first():
    records = [
        episode("low_importance", score=0.6, importance=0.1),
        episode("high_importance", score=0.6, importance=0.9),
    ]
    ranked = rank(records, query())
    assert [r.id for r in ranked] == ["high_importance", "low_importance"]


def test_a_more_relevant_episode_at_equal_importance_ranks_first():
    records = [
        episode("low_relevance", score=0.2, importance=0.5),
        episode("high_relevance", score=0.9, importance=0.5),
    ]
    ranked = rank(records, query())
    assert [r.id for r in ranked] == ["high_relevance", "low_relevance"]


def test_recent_important_and_relevant_beats_a_stale_unimportant_low_relevance_episode():
    """The issue's own Testing Note: "A recent + important + relevant episode ranks correctly."
    Recency is not a query-time signal here (ADR 0003 B.1) -- "recent" is expressed as already
    baked into the record's fused relevance score, the write-time prior this module consumes."""
    records = [
        episode("weak", score=0.1, importance=0.1),
        episode("strong", score=0.95, importance=0.9),
    ]
    ranked = rank(records, query())
    assert [r.id for r in ranked] == ["strong", "weak"]


def test_composite_score_replaces_the_fused_score_on_the_returned_copy():
    records = [episode("a", score=0.4, importance=0.6)]
    ranked = rank(records, query())
    assert ranked[0].score == pytest.approx(0.4 + 0.6)


def test_a_non_episodic_record_passes_through_with_its_score_unchanged():
    record = MemoryRecord(id="s", tier=MemoryTier.SEMANTIC, content="fact", scope=ACME, score=0.42)
    ranked = rank([record], query())
    assert ranked[0].score == pytest.approx(0.42)


def test_the_original_record_is_not_mutated():
    original = episode("a", score=0.4, importance=0.6)
    rank([original], query())
    assert original.score == 0.4


# -- recency signal is always 0.0, never read from `weights` ------------------------------------


def test_a_nonzero_recency_weight_does_not_change_the_result():
    # ADR 0003 B.1: no per-query additive recency rescore, enforced structurally by always
    # passing a 0.0 recency *signal* -- `weights.recency * 0.0 == 0.0` regardless of the weight.
    records = [episode("a", score=0.4, importance=0.6)]
    without_recency = rank(records, query(), weights=EpisodicScoreWeights(relevance=1.0, recency=0.0, importance=1.0))
    with_recency = rank(records, query(), weights=EpisodicScoreWeights(relevance=1.0, recency=5.0, importance=1.0))
    assert without_recency[0].score == pytest.approx(with_recency[0].score)


def test_an_invalid_recency_weight_is_still_refused():
    records = [episode("a", score=0.4, importance=0.6)]
    with pytest.raises(MemoryOperationError) as exc_info:
        rank(records, query(), weights=EpisodicScoreWeights(relevance=1.0, recency=-1.0, importance=1.0))
    assert exc_info.value.field_name == "recency_weight"


# -- score clamping, for `_merge`'s score-sum default overshooting 1.0 --------------------------


def test_a_fused_score_above_one_is_clamped_before_scoring():
    # `_merge`'s score-sum default can sum two [0.0, 1.0] legs to as much as 2.0; the composite's
    # own signal validation would otherwise reject it outright.
    records = [episode("a", score=1.7, importance=0.5)]
    ranked = rank(records, query())
    assert ranked[0].score == pytest.approx(1.0 + 0.5)


def test_a_missing_score_is_treated_as_zero_relevance():
    record = MemoryRecord(
        id="a", tier=MemoryTier.EPISODIC, content="a", scope=ACME, score=None, metadata={"importance": 0.5}
    )
    ranked = rank([record], query())
    assert ranked[0].score == pytest.approx(0.0 + 0.5)


# -- missing importance is refused, not silently skipped ----------------------------------------


def test_an_episodic_record_with_no_importance_in_metadata_is_refused():
    record = MemoryRecord(id="a", tier=MemoryTier.EPISODIC, content="a", scope=ACME, score=0.5, metadata={})
    with pytest.raises(MemoryOperationError) as exc_info:
        rank([record], query())
    assert exc_info.value.field_name == "importance"


# -- deterministic tie-break ----------------------------------------------------------------------


def test_ties_break_deterministically_on_content_then_tier_then_id():
    tied = [
        episode("z", score=0.5, importance=0.5, content="alpha"),
        episode("a", score=0.5, importance=0.5, content="alpha"),
    ]
    ranked = rank(tied, query())
    assert [r.id for r in ranked] == ["a", "z"]
