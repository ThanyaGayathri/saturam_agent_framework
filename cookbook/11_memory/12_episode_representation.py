"""
The Episode Representation
==========================

This example shows the unit of episodic memory (SM-E1) -- how a write-back event
becomes the stored episode row, and how that row comes back as a record the seam
can hand to an agent.

Key concepts:
- The stored shape is egpt.db.schemas.memory.EpisodicRecord (SM-C3), re-exported
  by egpt.memory rather than redefined, so the seam and the database cannot hold
  two different ideas of what an episode is.
- An episode is raw and non-lossy: content is stored as it arrived, and valid
  time is when it happened, not when it was recorded.
- graph_node_id is derived from the scope and the episode id, so the vector row
  and the graph episode node address each other without either side choosing.
- The projection back out carries context (when/where/why), importance and the
  decay inputs, so the scoring tickets have something to score.
- Every boundary refuses rather than guesses: a missing id, the wrong tier,
  caller metadata that would shadow an episode field, or a link belonging to
  another episode.

Runs with no database and no API key.
"""

from dataclasses import replace

from egpt.memory import (
    EPISODE_KEYS,
    MemoryEvent,
    MemoryOperationError,
    MemoryTier,
    Scope,
    episode_from_event,
    episode_to_record,
    graph_node_id_for,
)

scope = Scope(
    tenant_id="acme", agent_id="assistant", user_id="ada", session_id="deploy-42"
)

# ---------------------------------------------------------------------------
# 1. An event becomes an episode
# ---------------------------------------------------------------------------

event = MemoryEvent(
    content="Ada shipped v2 to production after the migration finished.",
    scope=scope,
    tier=MemoryTier.EPISODIC,
    id="ep-1",  # the provider stamps this; it is the cross-substrate dedup key
    topics=["deploy"],
    occurred_at=1_700_000_000,
    metadata={"channel": "slack"},
)

episode = episode_from_event(event)

print("Stored episode")
print("  id:          ", episode.id)
print("  tenant:      ", episode.tenant_id)
print("  content:     ", episode.content)
print("  occurred_at: ", episode.occurred_at, "(when it happened)")
print("  t_valid:     ", episode.t_valid, "(valid time, from occurred_at)")
print("  created_at:  ", episode.created_at, "(transaction time, this write)")
print("  importance:  ", episode.importance, "(default; SM-E5 scores it)")

# ---------------------------------------------------------------------------
# 2. The link to the graph episode node is derived, never free-form
# ---------------------------------------------------------------------------

print()
print("Graph link")
print("  graph_node_id:", episode.graph_node_id)
print("  derived from: ", "scope.key() =", scope.key(), "+ the episode id")
print(
    "  same for any caller:", episode.graph_node_id == graph_node_id_for(scope, "ep-1")
)

# ---------------------------------------------------------------------------
# 3. Reading it back
# ---------------------------------------------------------------------------

record = episode_to_record(episode)

print()
print("Projected record")
print("  tier:      ", record.tier.value)
print("  content:   ", record.content)
print("  topics:    ", record.topics)
print("  scope:     ", record.scope.key())
print("  provenance:", record.provenance)
print("  episode metadata keys:", sorted(EPISODE_KEYS))
print("  caller metadata survives:", record.metadata["channel"])

# ---------------------------------------------------------------------------
# 4. A re-cut can split one episode into several
# ---------------------------------------------------------------------------

split = episode_to_record(replace(episode, superseded_by=["ep-2", "ep-3"]))

print()
print("After a re-segmentation split (SM-R6 identity contract)")
print(
    "  superseded_by on record:",
    split.superseded_by,
    "-- a fork names no single replacement",
)
print("  every replacement:      ", split.provenance["superseded_by"])

# ---------------------------------------------------------------------------
# 5. The boundary refuses rather than guesses
# ---------------------------------------------------------------------------

print()
print("Refusals")

for label, build in (
    ("no id", lambda: episode_from_event(replace(event, id=None))),
    (
        "wrong tier",
        lambda: episode_from_event(replace(event, tier=MemoryTier.SEMANTIC)),
    ),
    (
        "shadowing metadata",
        lambda: episode_from_event(replace(event, metadata={"importance": 9})),
    ),
    (
        "another episode's node",
        lambda: episode_to_record(
            replace(episode, graph_node_id=graph_node_id_for(scope, "ep-9"))
        ),
    ),
):
    try:
        build()
        # No fallthrough: a boundary that stops refusing would otherwise print nothing at all,
        # and a silently missing line reads exactly like a line that was never there.
        print("  " + label + ": NOT REFUSED -- this boundary no longer guards")
    except MemoryOperationError as error:
        print("  " + label + ":", error)

# ---------------------------------------------------------------------------
# 6. What this layer deliberately does not do
# ---------------------------------------------------------------------------

print()
print("Owned elsewhere")
print("  capturing episodes from a run:      SM-E2 (#109)")
print("  deciding where an episode starts:   SM-E3 (#110)")
print("  decay and importance scoring:       SM-E4 (#111), SM-E5 (#112)")
print("  persisting and searching episodes:  SM-V1 (#95)")
print("  writing the episode node and edges: SM-G6 (#104)")
print("  keeping the two views consistent:   SM-E10 (#117)")
