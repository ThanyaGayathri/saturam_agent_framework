"""Shared consolidation-lane episode selection, used by every graph extraction pipeline.

``EntityExtractionPipeline`` (SM-G2 #100) and ``RelationExtractionPipeline`` (SM-G3 #101) both read
the same episodic slice under the same two rules -- current tier, current belief -- capped
identically. One helper, so the rule cannot drift between the two pipelines that apply it.
"""

from typing import List, Sequence, Tuple

from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryOperationError, MemoryRecord

__all__ = ["select_current_episodes"]


def select_current_episodes(
    episodes: Sequence[MemoryRecord], *, max_episodes_per_pass: int, purpose: str
) -> Tuple[List[MemoryRecord], bool]:
    """The episodes one extraction pass reads from, and whether it left any **unread**.

    ``purpose`` names what the pass extracts (e.g. ``"Entities"``, ``"Relations"``) in the refusal
    below, so the message identifies which pass rejected the tier rather than reading identically
    for both callers.

    Unread, not skipped: a superseded row was examined and passed over, so reporting the pass
    incomplete for one would tell a scheduler to re-run a pass with nothing left to do.
    """
    chosen: List[MemoryRecord] = []
    for index, episode in enumerate(episodes):
        if episode.tier is not MemoryTier.EPISODIC:
            raise MemoryOperationError(
                f"Episode {episode.id!r} is {episode.tier.value!r}. {purpose} are extracted from "
                f"the {MemoryTier.EPISODIC.value} tier; a semantic fact is already distilled, "
                "and re-extracting one would attribute it to a derived row.",
                field_name="tier",
            )
        # A superseded episode's result belongs to its live revision. Extracting from it would
        # attribute that result to a row no read returns.
        if episode.is_current_belief():
            chosen.append(episode)
        if len(chosen) == max_episodes_per_pass:
            return chosen, index + 1 < len(episodes)
    return chosen, False
