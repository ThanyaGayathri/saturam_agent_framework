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
    assert composite_episodic_score(0.2, 0.3, 0.4, weights) == pytest.approx(0.9)


def test_default_weights_zero_out_recency():
    # ADR 0003 B.1: w_recency=0 for an additive per-query hot-path rescore -- recency is a
    # write-time prior, not a term the default composite counts.
    assert DEFAULT_EPISODIC_SCORE_WEIGHTS.recency == 0.0
    assert composite_episodic_score(0.6, 1.0, 0.4) == pytest.approx(composite_episodic_score(0.6, 0.0, 0.4))


def test_default_weights_relevance_and_importance_are_one():
    assert DEFAULT_EPISODIC_SCORE_WEIGHTS.relevance == 1.0
    assert DEFAULT_EPISODIC_SCORE_WEIGHTS.importance == 1.0
    assert composite_episodic_score(0.5, 0.9, 0.25) == pytest.approx(0.75)


def test_score_is_not_renormalized_to_unit_interval():
    # a weighted sum of three signals each at 1.0, with all three weights at 1.0, is 3.0 -- the
    # composite is not divided back down to [0, 1].
    weights = EpisodicScoreWeights(relevance=1.0, recency=1.0, importance=1.0)
    assert composite_episodic_score(1.0, 1.0, 1.0, weights) == pytest.approx(3.0)


def test_zero_weight_excludes_a_signal_regardless_of_its_value():
    weights = EpisodicScoreWeights(relevance=1.0, recency=0.0, importance=0.0)
    assert composite_episodic_score(0.7, 1.0, 1.0, weights) == pytest.approx(0.7)


def test_all_zero_weights_collapse_every_score_to_zero():
    # Valid arithmetic, not refused (see composite_episodic_score's docstring): unlike the
    # single-zero default on recency alone, all three at zero is almost certainly a
    # misconfiguration, but the composite can't tell that apart from a caller who genuinely
    # wants every episode tied, so this documents the degenerate behavior rather than hiding it.
    weights = EpisodicScoreWeights(relevance=0.0, recency=0.0, importance=0.0)
    assert composite_episodic_score(0.9, 0.9, 0.9, weights) == pytest.approx(0.0)
    assert composite_episodic_score(0.1, 0.1, 0.1, weights) == pytest.approx(0.0)


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


def test_rejects_a_nan_signal():
    with pytest.raises(MemoryOperationError) as exc_info:
        composite_episodic_score(math.nan, 0.5, 0.5)
    assert exc_info.value.field_name == "relevance"


def test_rejects_an_infinite_signal():
    with pytest.raises(MemoryOperationError) as exc_info:
        composite_episodic_score(0.5, math.inf, 0.5)
    assert exc_info.value.field_name == "recency"


def test_boundary_values_zero_and_one_are_accepted():
    assert composite_episodic_score(0.0, 0.0, 0.0) == pytest.approx(0.0)
    weights = EpisodicScoreWeights(relevance=1.0, recency=1.0, importance=1.0)
    assert composite_episodic_score(1.0, 1.0, 1.0, weights) == pytest.approx(3.0)


# -- weight validation -------------------------------------------------------------------------


def test_rejects_a_nan_weight():
    weights = EpisodicScoreWeights(relevance=math.nan, recency=0.0, importance=1.0)
    with pytest.raises(MemoryOperationError) as exc_info:
        composite_episodic_score(0.5, 0.5, 0.5, weights)
    assert exc_info.value.field_name == "weights.relevance"


def test_rejects_an_infinite_weight():
    weights = EpisodicScoreWeights(relevance=1.0, recency=math.inf, importance=1.0)
    with pytest.raises(MemoryOperationError) as exc_info:
        composite_episodic_score(0.5, 0.5, 0.5, weights)
    assert exc_info.value.field_name == "weights.recency"


@pytest.mark.parametrize("field_name", ["relevance", "recency", "importance"])
def test_rejects_a_negative_weight(field_name):
    # A negative weight inverts a signal's contribution -- a more relevant/recent/important
    # episode would score lower, silently flipping the ranking rather than just skewing it.
    weights = EpisodicScoreWeights(**{field_name: -1.0})
    with pytest.raises(MemoryOperationError) as exc_info:
        composite_episodic_score(0.5, 0.5, 0.5, weights)
    assert exc_info.value.field_name == f"weights.{field_name}"


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
        name: composite_episodic_score(relevance, recency, importance, weights)
        for name, (relevance, recency, importance) in episodes.items()
    }
    ranking = sorted(scored, key=scored.__getitem__, reverse=True)
    assert ranking == [
        "shipped_v2",
        "quick_status_ping",
        "old_incident_postmortem",
        "unrelated_small_talk",
    ]
