"""
Episode Importance/Salience Scoring
====================================

This example shows the salience scorer SM-E5 adds -- how an episode's raw content
becomes a calibrated importance value, and how that value is applied to an
already-built episode.

Key concepts:
- ImportanceScorer asks the model on Generative Agents' 1-10 poignancy scale and
  normalizes down to the [0, 1] range Decaying.importance already validates
  (SM-C3, shipped by SM-E1). The two ends map onto the two ends exactly: 1 -> 0.0,
  10 -> 1.0.
- Scoring is I/O, so episode_from_event never does it -- that function stays pure
  construction. A caller scores separately, on its own time, and applies the
  result with dataclasses.replace(episode, importance=score), the same idiom
  every other episode field already uses post-construction.
- One Model call per score()/ascore() invocation, against the same ImportanceScorer
  instance every time -- talks to Model directly rather than building an
  egpt.agent.Agent, since egpt.memory (services) may not depend on egpt.agent
  (orchestration) per ADR 0001.
- Episode content reaches the model inside an <episode> envelope, read as the
  thing being scored rather than as an instruction.
- The scored value reaches SM-E7 (composite scoring) and SM-L6 (reflection
  trigger) through the EPISODE_KEYS floor SM-E1 already built: nothing more to
  wire on the read side.

Requires a real model, so this needs OPENAI_API_KEY (or your configured
provider's credentials) -- there is no offline path for LLM-assigned salience.
"""

import asyncio
from dataclasses import replace

from egpt.memory import (
    EPISODE_KEYS,
    MemoryEvent,
    MemoryTier,
    Scope,
    episode_from_event,
    episode_to_record,
)
from egpt.memory.episodic import ImportanceScorer
from egpt.models.openai import OpenAIResponses

scope = Scope(
    tenant_id="acme", agent_id="assistant", user_id="ada", session_id="deploy-42"
)

# ---------------------------------------------------------------------------
# 1. Two contrasting episodes, both at the row default
# ---------------------------------------------------------------------------

mundane_event = MemoryEvent(
    content="Ada said good morning in the team channel.",
    scope=scope,
    tier=MemoryTier.EPISODIC,
    id="ep-mundane",
    occurred_at=1_700_000_000,
)
significant_event = MemoryEvent(
    content="Ada signed the acquisition after eight months of negotiation.",
    scope=scope,
    tier=MemoryTier.EPISODIC,
    id="ep-significant",
    occurred_at=1_700_000_100,
)

mundane_episode = episode_from_event(mundane_event)
significant_episode = episode_from_event(significant_event)

print("Before scoring")
print("  mundane.importance:    ", mundane_episode.importance, "(row default)")
print("  significant.importance:", significant_episode.importance, "(row default)")

# ---------------------------------------------------------------------------
# 2. Score on the async lane, then apply with dataclasses.replace
# ---------------------------------------------------------------------------

scorer = ImportanceScorer(model=OpenAIResponses(id="gpt-5.5"))


async def score_both():
    mundane_score, significant_score = await asyncio.gather(
        scorer.ascore(mundane_episode.content),
        scorer.ascore(significant_episode.content),
    )
    return mundane_score, significant_score


mundane_score, significant_score = asyncio.run(score_both())

scored_mundane = replace(mundane_episode, importance=mundane_score)
scored_significant = replace(significant_episode, importance=significant_score)

print()
print("After scoring (LLM-assigned, 1-10 normalized to [0, 1])")
print("  mundane.importance:    ", scored_mundane.importance)
print("  significant.importance:", scored_significant.importance)
print(
    "  significant > mundane: ",
    scored_significant.importance > scored_mundane.importance,
)

# ---------------------------------------------------------------------------
# 3. The scored value reaches a reader through the existing floor (SM-E1)
# ---------------------------------------------------------------------------

record = episode_to_record(scored_significant)

print()
print("Projected record")
print("  metadata['importance']:", record.metadata["importance"])
print("  every episode key present:", set(EPISODE_KEYS) <= set(record.metadata))

# ---------------------------------------------------------------------------
# 4. What this layer deliberately does not do
# ---------------------------------------------------------------------------

print()
print("Owned elsewhere")
print("  combining importance with recency and relevance: SM-E7 (#114)")
print("  triggering reflection on accumulated importance:  SM-L6")
print(
    "  wiring the scorer into a live write path:         not yet built anywhere (see PR)"
)
