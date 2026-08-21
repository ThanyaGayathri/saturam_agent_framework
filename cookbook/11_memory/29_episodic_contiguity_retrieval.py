"""
Temporal-Contiguity Retrieval
==============================

This example shows SM-E8's temporal-contiguity retrieval: given a retrieval
hit, name its +/- n neighbors in temporal order (EM-LLM's "temporal-contiguity
buffer"), accumulate them across a pass in a bounded FIFO, and blend a
neighbor's distance from the hit into its composite score (SM-E7).

Key concepts:
- temporal_neighbors(hit, episodes, radius) sorts its own copy of episodes by
  temporal_order_key (occurred_at, falling back to created_at) and returns the
  episodes within radius positions of hit, nearest-to-farthest, before-then-
  after at each distance. The hit must appear in episodes -- a neighbor is a
  position relative to the hit within that candidate pool.
- ContiguityBuffer(capacity) is a bounded FIFO, deduplicated by episode id:
  enqueuing past capacity evicts the oldest-enqueued neighbor still present.
  A neighbor two hits share is only ever buffered once, from whichever hit
  enqueued it first.
- contiguity_bonus(distance, weight, decay) / blend_contiguity(base_score,
  distance) turn a buffered neighbor's distance from its hit into a bonus on
  top of its own composite_episodic_score -- the bonus is full weight at
  distance 1 and halves each additional hop by default, so contiguity augments
  ranking without overwhelming a directly relevant hit.
- Wiring this into live retrieval ranking is SM-H5's (#135), not shown here --
  this module only names neighbors, bounds how many a pass holds onto, and
  scores a distance once a caller has decided to blend it in.

Runs with no database and no API key: episodes below are built directly and
every signal is a plain float.
"""

from egpt.memory import (
    ContiguityBuffer,
    EpisodicScoreWeights,
    MemoryOperationError,
    blend_contiguity,
    composite_episodic_score,
    contiguity_bonus,
    temporal_neighbors,
)
from egpt.memory.schemas.episodic import EpisodicRecord

HOUR = 3600

# ---------------------------------------------------------------------------
# 1. A session of episodes, in temporal order
# ---------------------------------------------------------------------------

session = [
    EpisodicRecord(
        id="ep-0", tenant_id="acme", content="stand-up: plan the day", occurred_at=0
    ),
    EpisodicRecord(
        id="ep-1",
        tenant_id="acme",
        content="pulled the SM-E7 branch",
        occurred_at=1 * HOUR,
    ),
    EpisodicRecord(
        id="ep-2",
        tenant_id="acme",
        content="shipped the composite scorer",
        occurred_at=2 * HOUR,
    ),
    EpisodicRecord(
        id="ep-3",
        tenant_id="acme",
        content="wrote the SM-E7 cookbook",
        occurred_at=3 * HOUR,
    ),
    EpisodicRecord(
        id="ep-4", tenant_id="acme", content="opened the SM-E7 PR", occurred_at=4 * HOUR
    ),
    EpisodicRecord(id="ep-5", tenant_id="acme", content="lunch", occurred_at=5 * HOUR),
]
# "shipped the composite scorer" -- the episode a relevance search surfaced
hit = session[2]

# ---------------------------------------------------------------------------
# 2. Its temporal neighbors
# ---------------------------------------------------------------------------

neighbors = temporal_neighbors(hit, session, radius=2)
print(f"Hit: {hit.id} -- {hit.content!r}")
print("Neighbors (nearest first):")
for neighbor in neighbors:
    print(
        f"  distance {neighbor.distance:+d}: {neighbor.episode.id} -- {neighbor.episode.content!r}"
    )

# ---------------------------------------------------------------------------
# 3. Accumulating neighbors across a retrieval pass in a bounded buffer
# ---------------------------------------------------------------------------

buffer = ContiguityBuffer(capacity=4)
evicted = buffer.enqueue(neighbors)
print()
print(f"Buffer holds {len(buffer)}/4 neighbors after one hit's radius-2 lookup:")
for neighbor in buffer.neighbors():
    print(
        f"  {neighbor.episode.id} (distance {neighbor.distance:+d}, from hit {neighbor.hit_id})"
    )
print(f"Evicted to stay within capacity: {[n.episode.id for n in evicted]}")

# A second hit's neighbors that overlap the first hit's are deduplicated, not re-added.
second_hit = session[3]
second_neighbors = temporal_neighbors(second_hit, session, radius=1)
buffer.enqueue(second_neighbors)
print(f"After a second hit ({second_hit.id})'s neighbors: {len(buffer)}/4 buffered")

# ---------------------------------------------------------------------------
# 4. Blending a neighbor's distance into its composite score
# ---------------------------------------------------------------------------

weights = EpisodicScoreWeights(relevance=1.0, recency=0.0, importance=1.0)

# The temporal continuation of the hit: low relevance/importance on its own.
continuation = next(n for n in neighbors if n.episode.id == "ep-3")
continuation_base = composite_episodic_score(
    0.2, recency=0.0, importance=0.3, weights=weights
)

# An unrelated episode from later that day: scores higher on relevance/importance alone.
distractor_base = composite_episodic_score(
    0.3, recency=0.0, importance=0.35, weights=weights
)

print()
print("Without contiguity, an unrelated episode outranks the hit's own continuation:")
print(f"  continuation (ep-3) base score: {continuation_base:.3f}")
print(f"  distractor base score:          {distractor_base:.3f}")

continuation_blended = blend_contiguity(continuation_base, continuation.distance)
print()
print(
    f"contiguity_bonus at distance {continuation.distance:+d}: {contiguity_bonus(continuation.distance):.3f}"
)
print("With the blend, the continuation now outranks the distractor:")
print(f"  continuation blended score: {continuation_blended:.3f}")
print(f"  distractor base score:      {distractor_base:.3f}")
assert continuation_blended > distractor_base

# ---------------------------------------------------------------------------
# 5. The boundary refuses rather than guesses
# ---------------------------------------------------------------------------

print()
print("Refusals")

stranger = EpisodicRecord(
    id="ep-stranger",
    tenant_id="acme",
    content="not in this session",
    occurred_at=99 * HOUR,
)

for label, call in (
    ("a negative radius", lambda: temporal_neighbors(hit, session, radius=-1)),
    (
        "a hit absent from the candidate pool",
        lambda: temporal_neighbors(stranger, session, radius=1),
    ),
    ("a zero-capacity buffer", lambda: ContiguityBuffer(capacity=0)),
    (
        "a contiguity bonus at distance 0 (the hit is not its own neighbor)",
        lambda: contiguity_bonus(0),
    ),
):
    try:
        call()
        print("  " + label + ": NOT REFUSED -- this boundary no longer guards")
    except MemoryOperationError as error:
        print("  " + label + ":", error)

# ---------------------------------------------------------------------------
# 6. What this layer deliberately does not do
# ---------------------------------------------------------------------------

print()
print("Owned elsewhere")
print(
    "  fetching the candidate pool a hit's neighbors are drawn from: a substrate scan/query"
)
print("  producing a neighbor's own composite score:          SM-E7 (#114)")
print("  wiring contiguity into live retrieval ranking:        SM-H5 (#135)")
