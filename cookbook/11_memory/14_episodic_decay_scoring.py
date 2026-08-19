"""
Episodic Recency-Decay Scoring
===============================

This example shows the episodic recency-decay signal (SM-E4), the ranking
scorer SM-R5's decision (benchmarks/memory_decay/DECISION.md) picked after
measuring five candidates on a reproducible simulation.

Key concepts:
- The canonical scorer is `0.5 ** (age_since_access / half_life)`, where
  `half_life = base_half_life * (1 + imp_factor * importance)` -- importance
  modulates the decay *rate*, not the score. The rejected variants add
  importance as a second, additive score term; this one deliberately does not.
- A never-accessed episode decays from when it was written (`created_at`), not
  from epoch zero -- otherwise every fresh episode would score near zero.
- Reinforcement resets the clock on confirmed access, not on bare injection
  into a result list (DECISION.md section 3's measured reason). It returns a
  detached copy: nothing here writes it back to a store.
- This module scores; it does not rank live traffic. Composing it with
  relevance into one score is SM-E7 (#114); wiring that composite into
  retrieval is SM-H5 (#135). Both are open.

Runs with no database and no API key.
"""

from egpt.memory import (
    DEFAULT_BASE_HALF_LIFE_S,
    DEFAULT_IMP_FACTOR,
    EpisodicRecord,
    episode_decay_score,
    reinforce,
)

DAY = 24 * 3600

# ---------------------------------------------------------------------------
# 1. An old, un-accessed episode ranks below a recent one
# ---------------------------------------------------------------------------

now = 10 * DAY

old_episode = EpisodicRecord(
    id="ep-old",
    tenant_id="acme",
    content="Ada configured the staging cluster.",
    created_at=0,  # never accessed since -- decays from when it was written
)
recent_episode = EpisodicRecord(
    id="ep-recent",
    tenant_id="acme",
    content="Ada shipped v2 to production.",
    created_at=9 * DAY,
)

old_score = episode_decay_score(old_episode, now=now)
recent_score = episode_decay_score(recent_episode, now=now)

print("Recency-only ranking (both at default importance 0.5)")
print("  old episode (10d idle):   ", round(old_score, 4))
print("  recent episode (1d idle): ", round(recent_score, 4))
print("  recent ranks above old:   ", recent_score > old_score)

# ---------------------------------------------------------------------------
# 2. Importance modulates the decay rate, not the score directly
# ---------------------------------------------------------------------------

low_importance = EpisodicRecord(
    id="ep-low",
    tenant_id="acme",
    content="a passing remark",
    created_at=0,
    importance=0.1,
)
high_importance = EpisodicRecord(
    id="ep-high",
    tenant_id="acme",
    content="the incident postmortem",
    created_at=0,
    importance=0.9,
)

print()
print("Same age, different importance -- importance widens the half-life")
print(
    "  low importance (0.1) at 10d idle: ",
    round(episode_decay_score(low_importance, now=now), 4),
)
print(
    "  high importance (0.9) at 10d idle:",
    round(episode_decay_score(high_importance, now=now), 4),
)
print(
    f"  provisional defaults: base_half_life={DEFAULT_BASE_HALF_LIFE_S / DAY:g}d, imp_factor={DEFAULT_IMP_FACTOR}"
)

# ---------------------------------------------------------------------------
# 3. Accessing an old episode reinforces it
# ---------------------------------------------------------------------------

just_accessed = reinforce(old_episode, at=now)

print()
print("Confirmed access resets the clock (DECISION.md section 3)")
print(
    "  old episode before access: ", round(episode_decay_score(old_episode, now=now), 4)
)
print(
    "  old episode after access:  ",
    round(episode_decay_score(just_accessed, now=now), 4),
)
print(
    "  now outranks the recent one:",
    episode_decay_score(just_accessed, now=now) > recent_score,
)
print("  original episode untouched: ", old_episode.last_accessed_at is None)

# ---------------------------------------------------------------------------
# 4. What this layer deliberately does not do
# ---------------------------------------------------------------------------

print()
print("Owned elsewhere")
print("  importance scoring:                 SM-E5 (#112)")
print("  relevance scoring:                  SM-E6 (#113)")
print("  composing decay + importance + relevance into one score: SM-E7 (#114)")
print("  persisting a reinforced last_accessed_at: the caller, via a substrate upsert")
print("  blending the composite into live retrieval ranking: SM-H5 (#135)")
