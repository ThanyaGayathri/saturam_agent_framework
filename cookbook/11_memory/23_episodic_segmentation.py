"""
Episodic Segmentation: the SM-R6 v1 Inline Prior
==================================================

This example shows the write-back-time segmentation SM-E3 adds -- the deterministic,
zero-cost cut that turns a raw event stream into episode-boundary-tagged episode rows,
per `benchmarks/memory_event_segmentation/DECISION.md` section 1.

Key concepts:
- Two rules, in priority order: a hard `max_turns_per_episode` cap, and a `time_gap`
  boundary (default 900s idle) suppressed below `min_turns_per_episode` so a user's
  mid-topic pause does not fragment one episode into several micro-episodes --
  DECISION.md section 3 measured precision collapsing to 0.31 on exactly that pattern
  without the suppression.
- `episodes_from_events` composes `events_from_run` (SM-E2)'s flat event list with the
  segmenter and `episode_from_event` (SM-E1): every event still becomes its own episode
  row, but each row's `boundary_method`/`boundary_trigger`/`boundary_confidence` records
  whether -- and why -- it opened a new episode.
- A single write-back call usually shares one `occurred_at` across its whole batch, so a
  time gap only ever shows up *between* calls. `SegmentState` is the carry a caller
  threads from one write-back call to the next for the same scope.
- This is the *inline* prior only -- deterministic, no model or embedding call. The
  async re-segmentation lane (embedding-similarity drift, EM-LLM, an LLM call) is
  SM-R7's, against a real embedder/model this ticket's acceptance criteria do not
  give it.

Runs with no database and no API key -- every function here is pure construction, the
same CLAUDE.md exemption `episode_from_event` (SM-E1) and `episode_decay_score` (SM-E4)
already use.
"""

from egpt.memory import MemoryEvent, MemoryTier, Scope
from egpt.memory.episodic import SegmentState, segmenter_version
from egpt.memory.writeback import episodes_from_events

scope = Scope(tenant_id="acme", agent_id="assistant", user_id="ada", session_id="s1")

HOUR = 3600

# ---------------------------------------------------------------------------
# 1. A two-topic session: launch planning, then an unrelated backup check
#    after a long idle gap -- the acceptance test: "a multi-topic session
#    segments into more than one episode."
# ---------------------------------------------------------------------------

t0 = 1_700_000_000

launch_planning = [
    MemoryEvent(
        content="let's plan the launch",
        scope=scope,
        tier=MemoryTier.EPISODIC,
        occurred_at=t0,
    ),
    MemoryEvent(
        content="set the date for next tuesday",
        scope=scope,
        tier=MemoryTier.EPISODIC,
        occurred_at=t0 + 30,
    ),
    MemoryEvent(
        content="ok, tuesday it is",
        scope=scope,
        tier=MemoryTier.EPISODIC,
        occurred_at=t0 + 60,
    ),
]

backup_check = [
    MemoryEvent(
        content="unrelated: is the db backup working",
        scope=scope,
        tier=MemoryTier.EPISODIC,
        occurred_at=t0 + 2 * HOUR,
    ),
    MemoryEvent(
        content="yes, last one succeeded",
        scope=scope,
        tier=MemoryTier.EPISODIC,
        occurred_at=t0 + 2 * HOUR + 30,
    ),
]

episodes, state = episodes_from_events(launch_planning + backup_check, min_turns=2)

print("A multi-topic session, segmented in one call")
for episode in episodes:
    opened = "OPENS episode" if episode.boundary_method is not None else "continuation"
    print(f"  [{opened:>15}] {episode.content!r}")
print(f"  episode count: {sum(1 for e in episodes if e.boundary_method is not None)}")

# ---------------------------------------------------------------------------
# 2. The same session, split across two write-back calls -- SegmentState is
#    what lets the second call see the idle gap the first call ended on.
# ---------------------------------------------------------------------------

episodes_1, state_1 = episodes_from_events(launch_planning, min_turns=2)
episodes_2, state_2 = episodes_from_events(backup_check, state=state_1, min_turns=2)

print()
print("The same session across two write-back calls, threading SegmentState")
print("  call 1 (launch planning):")
for episode in episodes_1:
    print(
        f"    boundary_method={episode.boundary_method!r} content={episode.content!r}"
    )
print("  call 2 (backup check, state carried from call 1):")
for episode in episodes_2:
    print(
        f"    boundary_method={episode.boundary_method!r} content={episode.content!r}"
    )
print(
    "  call-2's opening boundary_trigger (idle seconds):",
    episodes_2[0].boundary_trigger,
)

# ---------------------------------------------------------------------------
# 3. A hard turn cap fires even inside the gap window -- a long unbroken
#    session still segments, per DECISION.md's provisional max_turns_per_episode.
# ---------------------------------------------------------------------------

chatty_turn = [
    MemoryEvent(
        content=f"message {i}",
        scope=scope,
        tier=MemoryTier.EPISODIC,
        occurred_at=t0 + i,  # one second apart -- well within any gap threshold
    )
    for i in range(5)
]

capped_episodes, _ = episodes_from_events(chatty_turn, max_turns=3, min_turns=1)

print()
print("A hard turn cap (max_turns=3) fires even with no idle gap")
for episode in capped_episodes:
    print(
        f"  boundary_method={episode.boundary_method!r} "
        f"boundary_trigger={episode.boundary_trigger!r} content={episode.content!r}"
    )

# ---------------------------------------------------------------------------
# 4. Boundary metadata (DECISION.md section 5) on one opening episode
# ---------------------------------------------------------------------------

opening = episodes[0]
print()
print("Boundary metadata on the first episode of section 1")
print("  boundary_method:    ", opening.boundary_method)
print("  boundary_trigger:   ", opening.boundary_trigger)
print("  boundary_confidence:", opening.boundary_confidence)
print("  segmenter_version:  ", opening.segmenter_version)
print("  revision, refined:  ", opening.revision, opening.refined)
print(
    "  matches segmenter_version(min_turns=2):",
    opening.segmenter_version == segmenter_version(min_turns=2),
)

# ---------------------------------------------------------------------------
# 5. What this layer deliberately does not do
# ---------------------------------------------------------------------------

print()
print("Owned elsewhere")
print(
    "  the async re-segmentation lane (embedding-similarity drift, EM-LLM, LLM): SM-R7"
)
print(
    "  the stable-id + supersede/revision re-cut contract: SM-R7, applied to rows this"
)
print("    module leaves at revision=0 (nothing has been re-cut yet)")
print(
    "  wiring episodes_from_events into a live write path: no seam method yet accepts"
)
print(
    "    List[EpisodicRecord] (see the PR discussion) -- an unassigned gap, not SM-V1's"
)
print("    (SM-V1 shipped the pgvector *vector substrate* backend, a different thing)")
print("  wiring this into an actual agent turn: SM-X3 (#139)")
print(f"  a fresh SegmentState() with no history: {SegmentState()}")
