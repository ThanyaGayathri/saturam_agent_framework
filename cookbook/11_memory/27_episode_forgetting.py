"""
Episode Forgetting / Pruning
=============================

This example shows the SM-E9 forgetting policy: SM-R5's ratified recency-floor
gate (benchmarks/memory_decay/DECISION.md section 4), applied to a mixed batch
of episodes at different ages and importances.

Key concepts:
- An episode is prune-eligible when its SM-E4 decay score falls below a
  threshold (default 0.05) -- and that is the *only* gate. DECISION.md section
  4 measured a stricter three-condition gate (recency floor AND
  importance-below-floor AND not-currently-valid) and rejected it: the extra
  conditions shield permanently-valid clutter from ever being pruned.
- Archival is row-level `expired_at` (invalidate-not-delete) -- never a hard
  delete. `select_for_forgetting` returns detached, already-archived copies;
  the originals are untouched.
- The importance-weighted half-life is what keeps the plain recency-floor gate
  safe: a high-importance episode survives well past where a low-importance
  one at the same age is already eligible.

Runs with no database and no API key.
"""

from egpt.memory import DEFAULT_PRUNE_THRESHOLD, EpisodicRecord, select_for_forgetting

DAY = 24 * 3600
NOW = 40 * DAY

# ---------------------------------------------------------------------------
# 1. A mixed batch: varying ages, varying importances
# ---------------------------------------------------------------------------

episodes = [
    EpisodicRecord(
        id="ep-stale-low-importance",
        tenant_id="acme",
        content="a passing remark about the weather",
        created_at=0,
        last_accessed_at=0,
        importance=0.1,
    ),
    EpisodicRecord(
        id="ep-stale-high-importance",
        tenant_id="acme",
        content="the incident postmortem",
        created_at=0,
        last_accessed_at=0,
        importance=0.9,
    ),
    EpisodicRecord(
        id="ep-recent",
        tenant_id="acme",
        content="Ada shipped v2 to production",
        created_at=NOW - DAY,
        last_accessed_at=NOW - DAY,
        importance=0.5,
    ),
]

print("Batch before pruning (id, age in days, importance)")
for episode in episodes:
    age_days = (NOW - episode.last_accessed_at) / DAY
    print(f"  {episode.id:28s} age={age_days:5.1f}d importance={episode.importance}")

# ---------------------------------------------------------------------------
# 2. Select and archive prune-eligible episodes
# ---------------------------------------------------------------------------

archived = select_for_forgetting(episodes, now=NOW)

print()
print(f"Recency-floor threshold: {DEFAULT_PRUNE_THRESHOLD}")
print(f"Archived {len(archived)} of {len(episodes)} episodes:")
for episode in archived:
    print(f"  {episode.id:28s} expired_at={episode.expired_at}")

# ---------------------------------------------------------------------------
# 3. Importance protects a recent-but-unaccessed episode, and originals
#    are never mutated -- archived copies are detached
# ---------------------------------------------------------------------------

archived_ids = {episode.id for episode in archived}

print()
print("The high-importance episode at the same age survives the low-importance one:")
print("  ep-stale-low-importance archived: ", "ep-stale-low-importance" in archived_ids)
print(
    "  ep-stale-high-importance archived:", "ep-stale-high-importance" in archived_ids
)

print()
print("The originals passed in are never mutated:")
print(
    "  originals still have expired_at=None:",
    all(e.expired_at is None for e in episodes),
)

# ---------------------------------------------------------------------------
# 4. What this layer deliberately does not do
# ---------------------------------------------------------------------------

print()
print("Owned elsewhere")
print("  persisting an archived episode: the caller, via a substrate upsert")
print("  scheduling a forgetting pass after consolidation: SM-L1 (#122) onward")
print("  per-tenant threshold resolution: SM-C5 (#93)")
