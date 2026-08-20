"""Episode forgetting / pruning policy (SM-E9 #116), per SM-R5's ratified decision
(``benchmarks/memory_decay/DECISION.md`` section 4).

Every assertion pins the ratified recency-floor-only gate: no age cutoff, no count cap, no
importance-below-floor condition, no not-currently-valid condition -- DECISION.md section 4
measured the stricter three-condition gate and rejected it for failing to bound growth. Archival
is asserted as row-level ``expired_at`` (never a hard delete, never routed through
``GraphSubstrate.invalidate``).
"""

from dataclasses import replace

import pytest

from egpt.memory.episodic.decay import DEFAULT_BASE_HALF_LIFE_S, episode_decay_score
from egpt.memory.episodic.forget import (
    DEFAULT_PRUNE_THRESHOLD,
    forget,
    is_prune_eligible,
    select_for_forgetting,
)
from egpt.memory.schemas.episodic import EpisodicRecord
from egpt.memory.types import MemoryOperationError

DAY = 24 * 3600


def an_episode(**overrides) -> EpisodicRecord:
    # `created_at=0`, unlike `test_episodic_decay.py`'s epoch-timestamp default: every test here
    # archives at a small offset from creation (`at=DAY`, `now=DAY * N`), and `BiTemporal`
    # refuses an `expired_at` that precedes `created_at`.
    defaults = dict(
        id="ep-1",
        tenant_id="acme",
        content="shipped v2 to production",
        created_at=0,
    )
    defaults.update(overrides)
    return EpisodicRecord(**defaults)


# -- is_prune_eligible -----------------------------------------------------------------------------


def test_is_prune_eligible_below_threshold():
    # Default half-life is 5 days; 90 days idle at importance 0 is deep past the floor.
    old = an_episode(created_at=0, last_accessed_at=0, importance=0.0)
    assert is_prune_eligible(old, now=DAY * 90) is True


def test_is_prune_eligible_above_threshold():
    fresh = an_episode(created_at=0, last_accessed_at=0, importance=0.5)
    assert is_prune_eligible(fresh, now=0) is False


def test_is_prune_eligible_at_the_default_threshold_boundary():
    # score == prune_threshold is not "below" it -- strict inequality, matching DECISION.md
    # section 4's "recency < prune_threshold".
    episode = an_episode(created_at=0, last_accessed_at=0, importance=0.0)
    score = episode_decay_score(episode, now=DAY * 90)
    assert is_prune_eligible(episode, now=DAY * 90, prune_threshold=score) is False
    assert is_prune_eligible(episode, now=DAY * 90, prune_threshold=score + 1e-9) is True


def test_is_prune_eligible_higher_importance_survives_longer():
    """DECISION.md section 2.2: importance-scaled half-life is what keeps the recency-floor-only
    gate safe (0 false-forgets) without a separate importance condition."""
    low_importance = an_episode(id="low", created_at=0, last_accessed_at=0, importance=0.0)
    high_importance = an_episode(id="high", created_at=0, last_accessed_at=0, importance=1.0)
    at_30_days = DAY * 30
    assert is_prune_eligible(low_importance, now=at_30_days) is True
    assert is_prune_eligible(high_importance, now=at_30_days) is False


def test_is_prune_eligible_never_reselects_an_already_archived_episode():
    episode = an_episode(created_at=0, last_accessed_at=0, importance=0.0)
    archived = forget(episode, at=DAY)
    assert is_prune_eligible(archived, now=DAY * 90) is False


def test_is_prune_eligible_uses_the_default_threshold_when_unset():
    assert DEFAULT_PRUNE_THRESHOLD == 0.05
    just_above_floor = an_episode(created_at=0, last_accessed_at=0, importance=0.0)
    # At the default half-life (5d) and importance 0, recency crosses 0.05 shortly after 21 days
    # (0.5 ** (21/5) ~= 0.043); pick a point comfortably past it.
    assert is_prune_eligible(just_above_floor, now=DAY * 25) is True


def test_is_prune_eligible_rejects_a_nan_threshold():
    episode = an_episode(created_at=0, last_accessed_at=0, importance=0.0)
    with pytest.raises(MemoryOperationError) as exc_info:
        is_prune_eligible(episode, now=DAY * 90, prune_threshold=float("nan"))
    assert exc_info.value.field_name == "prune_threshold"


def test_is_prune_eligible_rejects_an_infinite_threshold():
    episode = an_episode(created_at=0, last_accessed_at=0, importance=0.0)
    with pytest.raises(MemoryOperationError) as exc_info:
        is_prune_eligible(episode, now=DAY * 90, prune_threshold=float("inf"))
    assert exc_info.value.field_name == "prune_threshold"


def test_is_prune_eligible_respects_a_per_call_threshold_override():
    """Stands in for SM-C5's not-yet-landed per-tenant lookup: the threshold is a plain
    parameter, so a caller-supplied override changes the outcome without editing this module."""
    episode = an_episode(created_at=0, last_accessed_at=0, importance=0.0)
    at_10_days = DAY * 10
    assert is_prune_eligible(episode, now=at_10_days, prune_threshold=DEFAULT_PRUNE_THRESHOLD) is False
    assert is_prune_eligible(episode, now=at_10_days, prune_threshold=0.9) is True


# -- forget -----------------------------------------------------------------------------------------


def test_forget_sets_expired_at_not_a_delete():
    episode = an_episode()
    archived = forget(episode, at=DAY)
    assert archived.expired_at == DAY
    assert archived.id == episode.id
    assert archived.content == episode.content


def test_forget_does_not_touch_t_valid():
    episode = an_episode(t_valid=100)
    archived = forget(episode, at=DAY)
    assert archived.t_valid == 100


def test_forget_leaves_t_invalid_unset_by_default():
    episode = an_episode()
    archived = forget(episode, at=DAY)
    assert archived.t_invalid is None


def test_forget_accepts_an_explicit_t_invalid():
    episode = an_episode(t_valid=0)
    archived = forget(episode, at=DAY, t_invalid=DAY)
    assert archived.t_invalid == DAY


def test_forget_does_not_mutate_the_original():
    episode = an_episode()
    forget(episode, at=DAY)
    assert episode.expired_at is None


def test_forget_returns_a_different_object():
    episode = an_episode()
    assert forget(episode, at=DAY) is not episode


def test_forget_does_not_share_mutable_fields_with_the_original():
    episode = an_episode(entity_ids=["e1"])
    archived = forget(episode, at=DAY)
    assert archived.entity_ids == episode.entity_ids
    assert archived.entity_ids is not episode.entity_ids


def test_forget_only_changes_expired_at():
    episode = an_episode(importance=0.7)
    archived = forget(episode, at=DAY)
    assert archived == replace(episode, expired_at=DAY)


def test_forget_defaults_at_to_now_epoch_s(monkeypatch):
    # `forget()` has no `now` fallback of its own -- an unset `at` reaches
    # `BiTemporal.expire`, which reads `egpt.utils.dttm.now_epoch_s` directly (not this module's
    # `now_epoch_s` import, which only `select_for_forgetting` uses).
    from egpt.db.schemas import memory as memory_row_module

    monkeypatch.setattr(memory_row_module, "now_epoch_s", lambda: 4242)
    episode = an_episode()
    assert forget(episode).expired_at == 4242


def test_forget_is_idempotent_on_an_already_archived_episode():
    episode = an_episode()
    once = forget(episode, at=DAY)
    twice = forget(once, at=DAY * 2)
    assert twice.expired_at == DAY  # BiTemporal.expire no-ops on replay


# -- select_for_forgetting ---------------------------------------------------------------------------


def test_select_for_forgetting_returns_only_eligible_episodes():
    stale = an_episode(id="stale", created_at=0, last_accessed_at=0, importance=0.0)
    fresh = an_episode(id="fresh", created_at=DAY * 89, last_accessed_at=DAY * 89, importance=0.5)
    selected = select_for_forgetting([stale, fresh], now=DAY * 90)
    assert [e.id for e in selected] == ["stale"]


def test_select_for_forgetting_returns_archived_copies():
    stale = an_episode(created_at=0, last_accessed_at=0, importance=0.0)
    (selected,) = select_for_forgetting([stale], now=DAY * 90)
    assert selected.expired_at is not None
    assert stale.expired_at is None


def test_select_for_forgetting_returns_empty_when_nothing_is_eligible():
    fresh = an_episode(created_at=0, last_accessed_at=0, importance=0.5)
    assert select_for_forgetting([fresh], now=0) == []


def test_select_for_forgetting_scores_the_whole_batch_against_one_clock(monkeypatch):
    """One `now`, not `now_epoch_s()` called per row -- two equally-stale episodes must agree."""
    calls = []
    real_now = 1234

    def fake_now():
        calls.append(1)
        return real_now

    monkeypatch.setattr("egpt.memory.episodic.forget.now_epoch_s", fake_now)
    stale_a = an_episode(id="a", created_at=0, last_accessed_at=0, importance=0.0)
    stale_b = an_episode(id="b", created_at=0, last_accessed_at=0, importance=0.0)
    select_for_forgetting([stale_a, stale_b])
    assert len(calls) == 1


def test_select_for_forgetting_stamps_archived_at_with_the_shared_clock(monkeypatch):
    monkeypatch.setattr("egpt.memory.episodic.forget.now_epoch_s", lambda: DAY * 90)
    stale = an_episode(created_at=0, last_accessed_at=0, importance=0.0)
    (selected,) = select_for_forgetting([stale])
    assert selected.expired_at == DAY * 90


def test_select_for_forgetting_accepts_a_separate_transaction_time():
    """`at` (when the archive is recorded) can differ from `now` (the clock scored against) --
    a replayed pass archiving under the instant it actually runs."""
    stale = an_episode(created_at=0, last_accessed_at=0, importance=0.0)
    (selected,) = select_for_forgetting([stale], now=DAY * 90, at=DAY * 91)
    assert selected.expired_at == DAY * 91


def test_select_for_forgetting_preserves_relative_order():
    stale_a = an_episode(id="a", created_at=0, last_accessed_at=0, importance=0.0)
    stale_b = an_episode(id="b", created_at=1, last_accessed_at=1, importance=0.0)
    selected = select_for_forgetting([stale_a, stale_b], now=DAY * 90)
    assert [e.id for e in selected] == ["a", "b"]


def test_select_for_forgetting_ignores_already_archived_episodes():
    episode = an_episode(created_at=0, last_accessed_at=0, importance=0.0)
    archived = forget(episode, at=DAY)
    assert select_for_forgetting([archived], now=DAY * 90) == []


def test_default_base_half_life_matches_decay_module():
    # Pins that this module did not fork its own copy of the decay defaults.
    assert DEFAULT_BASE_HALF_LIFE_S == 5 * DAY
