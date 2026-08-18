"""Episodic recency-decay scoring (SM-E4 #111), per SM-R5's ratified decision
(``benchmarks/memory_decay/DECISION.md``).

Every assertion pins the canonical scorer -- ``0.5 ** (age / half_life)`` with importance
modulating the half-life -- against the exact section of DECISION.md it comes from, so a
regression toward one of the rejected variants (an additive ``+ gamma * importance`` term, an
unmodulated half-life) fails here rather than in SM-E7.
"""

from dataclasses import replace

import pytest

from egpt.memory.episodic.decay import (
    DEFAULT_BASE_HALF_LIFE_S,
    DEFAULT_IMP_FACTOR,
    episode_decay_score,
    half_life_s,
    recency_decay,
    reinforce,
)
from egpt.memory.schemas.episodic import EpisodicRecord
from egpt.memory.types import MemoryOperationError

DAY = 24 * 3600


def an_episode(**overrides) -> EpisodicRecord:
    defaults = dict(
        id="ep-1",
        tenant_id="acme",
        content="shipped v2 to production",
        created_at=1_700_000_000,
    )
    defaults.update(overrides)
    return EpisodicRecord(**defaults)


# -- half_life_s ---------------------------------------------------------------------------------


def test_half_life_at_zero_importance_is_the_base():
    assert half_life_s(0.0, base_half_life_s=DAY, imp_factor=DEFAULT_IMP_FACTOR) == DAY


def test_half_life_scales_by_one_plus_imp_factor_times_importance():
    # DECISION.md section 1: importance 1.0 -> half-life x(1 + imp_factor).
    assert half_life_s(1.0, base_half_life_s=DAY, imp_factor=4.0) == DAY * 5


def test_half_life_rejects_importance_outside_unit_interval():
    with pytest.raises(MemoryOperationError) as exc_info:
        half_life_s(1.5)
    assert exc_info.value.field_name == "importance"


def test_half_life_rejects_importance_below_zero():
    with pytest.raises(MemoryOperationError) as exc_info:
        half_life_s(-0.1)
    assert exc_info.value.field_name == "importance"


def test_half_life_rejects_non_positive_base():
    with pytest.raises(MemoryOperationError) as exc_info:
        half_life_s(0.5, base_half_life_s=0)
    assert exc_info.value.field_name == "base_half_life_s"


def test_half_life_rejects_negative_base():
    with pytest.raises(MemoryOperationError) as exc_info:
        half_life_s(0.5, base_half_life_s=-DAY)
    assert exc_info.value.field_name == "base_half_life_s"


def test_half_life_at_zero_imp_factor_ignores_importance():
    low = half_life_s(0.0, base_half_life_s=DAY, imp_factor=0.0)
    high = half_life_s(1.0, base_half_life_s=DAY, imp_factor=0.0)
    assert low == high == DAY


def test_half_life_rejects_imp_factor_that_zeroes_the_computed_half_life():
    with pytest.raises(MemoryOperationError) as exc_info:
        half_life_s(importance=1.0, imp_factor=-1.0)
    assert exc_info.value.field_name == "imp_factor"


def test_half_life_rejects_imp_factor_that_inverts_the_computed_half_life():
    with pytest.raises(MemoryOperationError) as exc_info:
        half_life_s(importance=1.0, imp_factor=-2.0)
    assert exc_info.value.field_name == "imp_factor"


# -- recency_decay --------------------------------------------------------------------------------


def test_recency_decay_at_zero_age_is_one():
    assert recency_decay(last_accessed_at=1000, importance=0.5, now=1000) == 1.0


def test_recency_decay_at_one_half_life_is_one_half():
    score = recency_decay(last_accessed_at=0, importance=0.0, now=DAY, base_half_life_s=DAY)
    assert score == pytest.approx(0.5)


def test_recency_decay_recent_scores_higher_than_old():
    """The issue's own testing note: an old un-accessed episode ranks below a recent one."""
    recent = recency_decay(last_accessed_at=DAY * 9, importance=0.5, now=DAY * 10)
    old = recency_decay(last_accessed_at=DAY * 0, importance=0.5, now=DAY * 10)
    assert recent > old


def test_recency_decay_higher_importance_decays_slower_at_equal_age():
    low_importance = recency_decay(last_accessed_at=0, importance=0.0, now=DAY * 5)
    high_importance = recency_decay(last_accessed_at=0, importance=1.0, now=DAY * 5)
    assert high_importance > low_importance


def test_recency_decay_rejects_now_before_last_accessed_at():
    with pytest.raises(MemoryOperationError) as exc_info:
        recency_decay(last_accessed_at=1000, importance=0.5, now=999)
    assert exc_info.value.field_name == "now"


def test_recency_decay_uses_default_constants_when_unset():
    # Pins the DECISION.md section 1 provisional defaults (5 days, imp_factor 4.0), which SM-R1
    # (#76) may later revise -- a change to these constants should be a deliberate edit here too.
    default_at_zero_importance = recency_decay(last_accessed_at=0, importance=0.0, now=int(DEFAULT_BASE_HALF_LIFE_S))
    assert default_at_zero_importance == pytest.approx(0.5)
    assert DEFAULT_BASE_HALF_LIFE_S == 5 * DAY
    assert DEFAULT_IMP_FACTOR == 4.0


def test_recency_decay_defaults_now_to_now_epoch_s(monkeypatch):
    monkeypatch.setattr("egpt.memory.episodic.decay.now_epoch_s", lambda: DAY * 3)
    assert recency_decay(last_accessed_at=0, importance=0.0, now=None) == recency_decay(
        last_accessed_at=0, importance=0.0, now=DAY * 3
    )


# -- episode_decay_score ---------------------------------------------------------------------------


def test_episode_decay_score_uses_last_accessed_at_over_the_created_at_fallback():
    """`last_accessed_at` wins when set; a later assertion confirms the fallback separately."""
    episode = an_episode(created_at=0, last_accessed_at=DAY * 9, importance=0.5)
    never_accessed = an_episode(created_at=DAY * 9, last_accessed_at=None, importance=0.5)
    assert episode_decay_score(episode, now=DAY * 10) == episode_decay_score(never_accessed, now=DAY * 10)


def test_episode_decay_score_rejects_an_access_time_before_the_episodes_clock():
    """The `recency_decay` `now`-before-`last_accessed_at` refusal, through the episode path."""
    episode = an_episode(created_at=0, last_accessed_at=DAY * 5)
    with pytest.raises(MemoryOperationError) as exc_info:
        episode_decay_score(episode, now=DAY * 3)
    assert exc_info.value.field_name == "now"


def test_episode_decay_score_defaults_last_accessed_at_to_created_at():
    fresh = an_episode(created_at=DAY * 10, last_accessed_at=None, importance=0.5)
    assert episode_decay_score(fresh, now=DAY * 10) == 1.0


def test_episode_decay_score_old_unaccessed_episode_ranks_below_recent_one():
    old = an_episode(id="old", created_at=0, last_accessed_at=None, importance=0.5)
    recent = an_episode(id="recent", created_at=DAY * 9, last_accessed_at=None, importance=0.5)
    assert episode_decay_score(recent, now=DAY * 10) > episode_decay_score(old, now=DAY * 10)


def test_episode_decay_score_rejects_an_episode_with_neither_timestamp_set():
    # TenantScoped.__post_init__ auto-stamps created_at whenever it is None at construction, so
    # the only way to reach "neither set" is a post-construction mutation -- EpisodicRecord is
    # not frozen, so a caller can do this.
    episode = an_episode(last_accessed_at=None)
    episode.created_at = None
    with pytest.raises(MemoryOperationError) as exc_info:
        episode_decay_score(episode, now=DAY)
    assert exc_info.value.field_name == "created_at"


def test_episode_decay_score_reads_the_episodes_own_importance():
    low = an_episode(id="low", created_at=0, importance=0.0)
    high = an_episode(id="high", created_at=0, importance=1.0)
    assert episode_decay_score(high, now=DAY * 5) > episode_decay_score(low, now=DAY * 5)


# -- reinforce --------------------------------------------------------------------------------------


def test_reinforce_resets_last_accessed_at_to_now():
    episode = an_episode(created_at=0, last_accessed_at=0)
    reinforced = reinforce(episode, at=DAY * 3)
    assert reinforced.last_accessed_at == DAY * 3


def test_reinforce_defaults_to_now_epoch_s(monkeypatch):
    monkeypatch.setattr("egpt.memory.episodic.decay.now_epoch_s", lambda: 4242)
    episode = an_episode(created_at=0, last_accessed_at=0)
    assert reinforce(episode).last_accessed_at == 4242


def test_reinforce_does_not_mutate_the_original():
    episode = an_episode(created_at=0, last_accessed_at=0)
    reinforce(episode, at=DAY)
    assert episode.last_accessed_at == 0


def test_reinforce_returns_a_different_object():
    episode = an_episode(created_at=0, last_accessed_at=0)
    assert reinforce(episode, at=DAY) is not episode


def test_reinforce_only_changes_last_accessed_at():
    episode = an_episode(created_at=0, last_accessed_at=0, importance=0.7, content="x")
    reinforced = reinforce(episode, at=DAY)
    assert reinforced == replace(episode, last_accessed_at=DAY)


def test_reinforce_allows_backward_access_for_out_of_order_replay():
    """The docstring's own claim: going backward is a replayed access, not a clock bug."""
    episode = an_episode(created_at=0, last_accessed_at=DAY * 5)
    reinforced = reinforce(episode, at=DAY * 2)
    assert reinforced.last_accessed_at == DAY * 2


def test_accessing_an_old_episode_reinforces_it_above_a_recent_unaccessed_one():
    """The issue's own testing note, second half: accessing an old episode reinforces it."""
    old = an_episode(id="old", created_at=0, last_accessed_at=0, importance=0.5)
    recent = an_episode(id="recent", created_at=DAY * 9, last_accessed_at=None, importance=0.5)
    now = DAY * 10
    assert episode_decay_score(old, now=now) < episode_decay_score(recent, now=now)

    just_accessed = reinforce(old, at=now)
    assert episode_decay_score(just_accessed, now=now) > episode_decay_score(recent, now=now)
