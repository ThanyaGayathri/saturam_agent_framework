"""
Time-Aware Blended Ranking
===========================

This example shows SM-H5's `rank()`: the pure function that sits after fusion
(SM-H2, `14_`) and rerank (SM-H3, not yet built) and before `MemorySlice`
assembly (SM-H6) -- an explicit time-window pre-filter, then SM-E7's
composite score (`26_`) blended into every episodic record's fused score.

Key concepts:
- The temporal filter only runs when the query is explicitly time-scoped
  (`MemoryQuery.valid_from`/`valid_to` set) -- ADR 0003 section B.1: recency
  is an *optional pre-filter*, not an implicit "now" narrowing. An unscoped
  query keeps every candidate at this stage.
- The composite blend only touches EPISODIC-tier records -- SM-E7's
  composite is an episodic scoring device. A semantic record's fused score
  passes through untouched.
- The composite's `recency` *signal* is always 0.0 here, never read from the
  weights a caller supplies. ADR 0003 section B.1 rules out a per-query
  additive recency rescore on the hot path outright (SM-R8's measured
  `recency_weight` degradation, 0.8934 -> 0.7265); pinning the signal itself
  enforces that no matter what `weights.recency` a caller passes.
- A fused score above 1.0 (`_merge`'s score-sum default can sum two
  `[0.0, 1.0]` legs to as much as 2.0) is clamped before it reaches the
  composite's own `[0.0, 1.0]` signal validation.

Wiring this into `CoLocatedMemoryProvider.read`/`aread` is SM-H6's (#136,
"MemorySlice assembly"), not shown here -- this cookbook ranks a plain list
of `MemoryRecord`s already representing a fused (and, once SM-H3 ships,
reranked) result set. Runs with no database and no API key: every record
below is constructed in memory.
"""

from egpt.memory import (
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    MemoryTier,
    Scope,
)
from egpt.memory.episodic.score import EpisodicScoreWeights
from egpt.memory.retrieval.rank import rank

SCOPE = Scope(tenant_id="acme", user_id="u1")


def episode(id_, content, score, importance, t_valid=None, t_invalid=None):
    return MemoryRecord(
        id=id_,
        tier=MemoryTier.EPISODIC,
        content=content,
        scope=SCOPE,
        score=score,
        t_valid=t_valid,
        t_invalid=t_invalid,
        metadata={"importance": importance},
    )


# ---------------------------------------------------------------------------
# 1. A recent, important, and relevant episode ranks first
# ---------------------------------------------------------------------------

fused = [
    episode("small_talk", "how's the weather", score=0.15, importance=0.1),
    episode("shipped_v2", "shipped v2 to production", score=0.85, importance=0.9),
    episode("status_ping", "quick status ping", score=0.6, importance=0.2),
]

query = MemoryQuery(text="what shipped recently")
ranked = rank(fused, query)

print("Ranked (highest composite first):")
for record in ranked:
    print(f"  {record.score:.2f}  {record.id}")

# ---------------------------------------------------------------------------
# 2. The temporal filter only runs for an explicitly time-scoped query
# ---------------------------------------------------------------------------

dated = [
    episode(
        "last_week",
        "last week's incident",
        score=0.7,
        importance=0.6,
        t_valid=1_700_000_000,
        t_invalid=1_700_086_400,
    ),
    episode(
        "today",
        "today's incident",
        score=0.7,
        importance=0.6,
        t_valid=1_700_600_000,
        t_invalid=1_700_686_400,
    ),
]

unscoped = rank(dated, MemoryQuery(text="incident"))
print()
print("No window -- both candidates survive:", sorted(r.id for r in unscoped))

windowed = rank(
    dated,
    MemoryQuery(text="incident", valid_from=1_700_500_000, valid_to=1_700_700_000),
)
print(
    "Window around 'today' -- 'last_week' is filtered before ranking:",
    [r.id for r in windowed],
)

# ---------------------------------------------------------------------------
# 3. A nonzero recency weight does not change the result
# ---------------------------------------------------------------------------

one = episode("a", "a", score=0.5, importance=0.5)
no_recency = rank(
    [one],
    MemoryQuery(text="q"),
    weights=EpisodicScoreWeights(relevance=1.0, recency=0.0, importance=1.0),
)
with_recency = rank(
    [one],
    MemoryQuery(text="q"),
    weights=EpisodicScoreWeights(relevance=1.0, recency=5.0, importance=1.0),
)
print()
print("Same score regardless of the recency weight (the signal itself is always 0.0):")
print(
    f"  recency=0.0 -> {no_recency[0].score:.2f}   recency=5.0 -> {with_recency[0].score:.2f}"
)

# ---------------------------------------------------------------------------
# 4. An episodic record with no importance in metadata is refused
# ---------------------------------------------------------------------------

malformed = MemoryRecord(
    id="bad",
    tier=MemoryTier.EPISODIC,
    content="bad",
    scope=SCOPE,
    score=0.5,
    metadata={},
)
print()
print("Refusals")
try:
    rank([malformed], MemoryQuery(text="q"))
    print("  malformed episode: NOT REFUSED -- this boundary no longer guards")
except MemoryOperationError as error:
    print("  malformed episode:", error)

# ---------------------------------------------------------------------------
# 5. What this layer deliberately does not do
# ---------------------------------------------------------------------------

print()
print("Owned elsewhere")
print("  fusion:                                  SM-H2 (#132)")
print("  cross-encoder rerank:                     SM-H3 (#133)")
print("  contiguity blend (out of this ticket's dependency list): SM-E8 (#115)")
print("  wiring this into CoLocatedMemoryProvider: SM-H6 (#136)")
