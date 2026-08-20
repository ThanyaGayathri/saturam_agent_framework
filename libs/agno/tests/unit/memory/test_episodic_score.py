"""Composite episodic scoring (SM-E7 #114): relevance + recency + importance in one score.

Pins the exact formula (``a*relevance + b*recency + c*importance``, weighted sum not
renormalized), the signal-validation floor (each of the three inputs must be a finite value in
``[0.0, 1.0]``), and the ratified default weights (ADR 0003 section B.1: ``recency=0.0``, since a
per-query additive recency rescore on the hot path measurably hurt retrieval quality) against a
ranked fixture, so a regression toward equal-weighted defaults or a silently-clamped signal fails
here.
"""

import math

import pytest

from egpt.memory.episodic.score import (
    DEFAULT_EPISODIC_SCORE_WEIGHTS,
    EpisodicScoreWeights,
    composite_episodic_score,
)
from egpt.memory.types import MemoryOperationError

# -- weighted sum ----------------------------------------------------------------------------


def test_combines_all_three_signals_with_explicit_equal_weights():
    weights = EpisodicScoreWeights(relevance=1.0, recency=1.0, importance=1.0)
    assert composite_episodic_score(0.2, recency=0.3, importance=0.4, weights=weights) == pytest.approx(0.9)


def test_default_weights_zero_out_recency():
    # ADR 0003 B.1: w_recency=0 for an additive per-query hot-path rescore -- recency is a
    # write-time prior, not a term the default composite counts.
    assert DEFAULT_EPISODIC_SCORE_WEIGHTS.recency == 0.0
    assert composite_episodic_score(0.6, recency=1.0, importance=0.4) == pytest.approx(
        composite_episodic_score(0.6, recency=0.0, importance=0.4)
    )


def test_default_weights_relevance_and_importance_are_one():
    assert DEFAULT_EPISODIC_SCORE_WEIGHTS.relevance == 1.0
    assert DEFAULT_EPISODIC_SCORE_WEIGHTS.importance == 1.0
    assert composite_episodic_score(0.5, recency=0.9, importance=0.25) == pytest.approx(0.75)


def test_score_is_not_renormalized_to_unit_interval():
    # a weighted sum of three signals each at 1.0, with all three weights at 1.0, is 3.0 -- the
    # composite is not divided back down to [0, 1].
    weights = EpisodicScoreWeights(relevance=1.0, recency=1.0, importance=1.0)
    assert composite_episodic_score(1.0, recency=1.0, importance=1.0, weights=weights) == pytest.approx(3.0)


def test_zero_weight_excludes_a_signal_regardless_of_its_value():
    weights = EpisodicScoreWeights(relevance=1.0, recency=0.0, importance=0.0)
    assert composite_episodic_score(0.7, recency=1.0, importance=1.0, weights=weights) == pytest.approx(0.7)


def test_all_zero_weights_collapse_every_score_to_zero():
    # Valid arithmetic, not refused (see composite_episodic_score's docstring): unlike the
    # single-zero default on recency alone, all three at zero is almost certainly a
    # misconfiguration, but the composite can't tell that apart from a caller who genuinely
    # wants every episode tied, so this documents the degenerate behavior rather than hiding it.
    weights = EpisodicScoreWeights(relevance=0.0, recency=0.0, importance=0.0)
    assert composite_episodic_score(0.9, recency=0.9, importance=0.9, weights=weights) == pytest.approx(0.0)
    assert composite_episodic_score(0.1, recency=0.1, importance=0.1, weights=weights) == pytest.approx(0.0)


# -- signal validation ------------------------------------------------------------------------


@pytest.mark.parametrize("field_name", ["relevance", "recency", "importance"])
def test_rejects_a_signal_above_one(field_name):
    kwargs = {"relevance": 0.5, "recency": 0.5, "importance": 0.5}
    kwargs[field_name] = 1.5
    with pytest.raises(MemoryOperationError) as exc_info:
        composite_episodic_score(**kwargs)
    assert exc_info.value.field_name == field_name


@pytest.mark.parametrize("field_name", ["relevance", "recency", "importance"])
def test_rejects_a_signal_below_zero(field_name):
    kwargs = {"relevance": 0.5, "recency": 0.5, "importance": 0.5}
    kwargs[field_name] = -0.1
    with pytest.raises(MemoryOperationError) as exc_info:
        composite_episodic_score(**kwargs)
    assert exc_info.value.field_name == field_name


@pytest.mark.parametrize("field_name", ["relevance", "recency", "importance"])
def test_rejects_a_nan_signal(field_name):
    kwargs = {"relevance": 0.5, "recency": 0.5, "importance": 0.5}
    kwargs[field_name] = math.nan
    with pytest.raises(MemoryOperationError) as exc_info:
        composite_episodic_score(**kwargs)
    assert exc_info.value.field_name == field_name


@pytest.mark.parametrize("field_name", ["relevance", "recency", "importance"])
@pytest.mark.parametrize("infinity", [math.inf, -math.inf])
def test_rejects_an_infinite_signal(field_name, infinity):
    kwargs = {"relevance": 0.5, "recency": 0.5, "importance": 0.5}
    kwargs[field_name] = infinity
    with pytest.raises(MemoryOperationError) as exc_info:
        composite_episodic_score(**kwargs)
    assert exc_info.value.field_name == field_name


def test_boundary_values_zero_and_one_are_accepted():
    assert composite_episodic_score(0.0, recency=0.0, importance=0.0) == pytest.approx(0.0)
    weights = EpisodicScoreWeights(relevance=1.0, recency=1.0, importance=1.0)
    assert composite_episodic_score(1.0, recency=1.0, importance=1.0, weights=weights) == pytest.approx(3.0)


# -- weight validation -------------------------------------------------------------------------


@pytest.mark.parametrize("field_name", ["relevance", "recency", "importance"])
def test_rejects_a_nan_weight(field_name):
    weights = EpisodicScoreWeights(**{field_name: math.nan})
    with pytest.raises(MemoryOperationError) as exc_info:
        composite_episodic_score(0.5, recency=0.5, importance=0.5, weights=weights)
    assert exc_info.value.field_name == f"{field_name}_weight"


@pytest.mark.parametrize("field_name", ["relevance", "recency", "importance"])
@pytest.mark.parametrize("infinity", [math.inf, -math.inf])
def test_rejects_an_infinite_weight(field_name, infinity):
    weights = EpisodicScoreWeights(**{field_name: infinity})
    with pytest.raises(MemoryOperationError) as exc_info:
        composite_episodic_score(0.5, recency=0.5, importance=0.5, weights=weights)
    assert exc_info.value.field_name == f"{field_name}_weight"


@pytest.mark.parametrize("field_name", ["relevance", "recency", "importance"])
def test_rejects_a_negative_weight(field_name):
    # A negative weight inverts a signal's contribution -- a more relevant/recent/important
    # episode would score lower, silently flipping the ranking rather than just skewing it.
    weights = EpisodicScoreWeights(**{field_name: -1.0})
    with pytest.raises(MemoryOperationError) as exc_info:
        composite_episodic_score(0.5, recency=0.5, importance=0.5, weights=weights)
    assert exc_info.value.field_name == f"{field_name}_weight"


# -- ranking matches expected ordering on a fixture -------------------------------------------


def test_ranking_matches_expected_ordering_on_a_fixture():
    # Equal weights so all three signals visibly contribute to the ranking -- the acceptance
    # criterion this test pins.
    weights = EpisodicScoreWeights(relevance=1.0, recency=1.0, importance=1.0)
    episodes = {
        # High on every signal -- must rank first.
        "shipped_v2": (0.9, 0.9, 0.9),
        # Highly relevant and recent, but low importance -- beats a stale/unimportant episode,
        # loses to one that is also important.
        "quick_status_ping": (0.9, 0.9, 0.1),
        # Important and relevant, but decayed almost to nothing -- still beats the noise episode.
        "old_incident_postmortem": (0.8, 0.05, 0.9),
        # Low on every signal -- must rank last.
        "unrelated_small_talk": (0.1, 0.2, 0.1),
    }
    scored = {
        name: composite_episodic_score(relevance, recency=recency, importance=importance, weights=weights)
        for name, (relevance, recency, importance) in episodes.items()
    }
    ranking = sorted(scored, key=scored.__getitem__, reverse=True)
    assert ranking == [
        "shipped_v2",
        "quick_status_ping",
        "old_incident_postmortem",
        "unrelated_small_talk",
    ]


def test_default_weights_rank_a_stale_important_episode_over_a_fresh_unimportant_one():
    # Executable form of the ADR 0003 B.1 rationale prose above test_default_weights_zero_out_recency:
    # under the shipped defaults, recency has zero say in the ranking, so a stale episode that is
    # both relevant and important outranks a fresh episode that is neither. A regression that
    # re-enables recency in the defaults (recency=1.0) would flip this ranking, but would NOT fail
    # test_ranking_matches_expected_ordering_on_a_fixture above, since that test hard-codes its own
    # non-default weights rather than using DEFAULT_EPISODIC_SCORE_WEIGHTS.
    episodes = {
        "stale_important_incident": (0.9, 0.01, 0.9),
        "fresh_unimportant_ping": (0.2, 0.99, 0.1),
    }
    scored = {
        name: composite_episodic_score(
            relevance, recency=recency, importance=importance, weights=DEFAULT_EPISODIC_SCORE_WEIGHTS
        )
        for name, (relevance, recency, importance) in episodes.items()
    }
    assert scored["stale_important_incident"] > scored["fresh_unimportant_ping"]
