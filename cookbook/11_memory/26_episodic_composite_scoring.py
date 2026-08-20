"""
Composite Episodic Scoring
===========================

This example shows SM-E7's composite scorer: relevance (SM-E6) + recency (SM-E4)
+ importance (SM-E5) combined into one ranking score with configurable weights.

Key concepts:
- composite_episodic_score(relevance, *, recency, importance, weights) is a
  weighted sum, a*relevance + b*recency + c*importance -- not renormalized
  back to [0, 1], the same choice DECISION.md's ratified two-term scorer
  makes. recency and importance are keyword-only so a transposed pair can't
  silently type-check into the wrong score.
- Each of the three signals must already be a finite value in [0.0, 1.0] --
  the contract cosine_relevance, recency_decay and ImportanceScorer.score
  each promise their own callers. A value outside that range is refused, not
  clamped.
- DEFAULT_EPISODIC_SCORE_WEIGHTS is not equal-weighted: recency defaults to
  0.0. ADR 0003 section B.1 ratifies SM-R8's measured finding that an
  additive per-query recency rescore on the hot path hurts retrieval
  quality -- recency belongs at write time, as a prior, not recomputed per
  query. A caller scoring at write time passes an explicit
  EpisodicScoreWeights with a nonzero recency weight instead.
- Wiring this composite into live retrieval ranking is SM-H5's (#135), not
  shown here -- this module only combines already-computed signals.

Runs with no database and no API key: every signal below is a plain float,
already computed by the SM-E4/E5/E6 modules those cookbooks demonstrate.
"""

from egpt.memory import (
    DEFAULT_EPISODIC_SCORE_WEIGHTS,
    EpisodicScoreWeights,
    MemoryOperationError,
    composite_episodic_score,
)

# ---------------------------------------------------------------------------
# 1. The default weights: relevance and importance count, recency does not
# ---------------------------------------------------------------------------

print("Default weights:", DEFAULT_EPISODIC_SCORE_WEIGHTS)

stale_but_relevant_and_important = composite_episodic_score(
    relevance=0.9, recency=0.02, importance=0.9
)
fresh_but_relevant_and_important = composite_episodic_score(
    relevance=0.9, recency=0.95, importance=0.9
)
print("Same score whether the episode is stale or fresh, by default:")
print("  stale: ", stale_but_relevant_and_important)
print("  fresh: ", fresh_but_relevant_and_important)

# ---------------------------------------------------------------------------
# 2. A write-time caller opts into a nonzero recency weight
# ---------------------------------------------------------------------------

write_time_weights = EpisodicScoreWeights(relevance=1.0, recency=1.0, importance=1.0)
print()
print("With an explicit write-time weight set, staleness now matters:")
print(
    "  stale: ",
    composite_episodic_score(
        relevance=0.9, recency=0.02, importance=0.9, weights=write_time_weights
    ),
)
print(
    "  fresh: ",
    composite_episodic_score(
        relevance=0.9, recency=0.95, importance=0.9, weights=write_time_weights
    ),
)

# ---------------------------------------------------------------------------
# 3. Ranking a fixture of episodes
# ---------------------------------------------------------------------------

episodes = {
    # relevance, recency, importance
    "shipped_v2_to_prod": (0.9, 0.9, 0.9),
    "quick_status_ping": (0.9, 0.9, 0.1),
    "old_incident_postmortem": (0.8, 0.05, 0.9),
    "unrelated_small_talk": (0.1, 0.2, 0.1),
}
scored = {
    name: composite_episodic_score(
        relevance=relevance,
        recency=recency,
        importance=importance,
        weights=write_time_weights,
    )
    for name, (relevance, recency, importance) in episodes.items()
}
print()
print("Ranked episodes (write-time weights, highest first):")
for name in sorted(scored, key=scored.__getitem__, reverse=True):
    print(f"  {scored[name]:.2f}  {name}")

# ---------------------------------------------------------------------------
# 4. The boundary refuses rather than guesses
# ---------------------------------------------------------------------------

print()
print("Refusals")

for label, call in (
    (
        "a relevance value outside [0, 1]",
        lambda: composite_episodic_score(relevance=1.5, recency=0.5, importance=0.5),
    ),
    (
        "a NaN weight",
        lambda: composite_episodic_score(
            relevance=0.5,
            recency=0.5,
            importance=0.5,
            weights=EpisodicScoreWeights(relevance=float("nan")),
        ),
    ),
    (
        "a negative weight (would silently invert that signal's contribution)",
        lambda: composite_episodic_score(
            relevance=0.5,
            recency=0.5,
            importance=0.5,
            weights=EpisodicScoreWeights(relevance=-1.0),
        ),
    ),
):
    try:
        call()
        print("  " + label + ": NOT REFUSED -- this boundary no longer guards")
    except MemoryOperationError as error:
        print("  " + label + ":", error)

# ---------------------------------------------------------------------------
# 5. What this layer deliberately does not do
# ---------------------------------------------------------------------------

print()
print("Owned elsewhere")
print(
    "  producing the three signals:          SM-E4 (#111), SM-E5 (#112), SM-E6 (#113)"
)
print("  wiring the composite into retrieval ranking: SM-H5 (#135)")
