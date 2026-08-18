"""Episode salience/importance scoring (SM-E5 #112).

Generative Agents score poignancy 1-10 with an LLM at ingestion; this repo's decay inputs
(``Decaying.importance``, SM-C3) instead validate a float in ``[0, 1]``. :class:`ImportanceScorer`
asks the model on the 1-10 scale the research is calibrated against and normalizes down, rather
than inventing a different scale on either end.

Scoring is I/O, so it happens on the async lane, never inside
:func:`~egpt.memory.schemas.episodic.episode_from_event`: that function is pure construction (no
store, no model call), and stays that way. A caller scores separately and applies the result with
``dataclasses.replace(episode, importance=score)``, the same idiom every other episode field
already uses post-construction.

The scored value reaches SM-E7 (composite scoring) and SM-L6 (reflection trigger) through the
existing floor SM-E1 built: ``EPISODE_KEYS`` carries ``importance`` into
``MemoryRecord.metadata`` on every read, scored or not. This module's only job is producing a
calibrated value for that field to carry -- not consuming it.
"""

from textwrap import dedent
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from egpt.memory.errors import MemoryOperationError
from egpt.models.base import Model

if TYPE_CHECKING:
    from egpt.agent import Agent

__all__ = ["ImportanceScore", "ImportanceScorer"]

_INSTRUCTIONS = dedent("""\
    Rate how significant, memorable, or consequential the given event is, on a scale of 1 to 10:
    - 1-2: mundane and routine (small talk, a status ping, a trivial confirmation)
    - 3-5: minor but noteworthy
    - 6-8: significant (a decision made, a problem solved, a meaningful exchange)
    - 9-10: highly significant, emotionally intense, or consequential

    Judge the event on its own terms, with no other context available to you.
""")


class ImportanceScore(BaseModel):
    """The scorer's output schema -- Generative Agents' 1-10 poignancy scale."""

    score: int = Field(..., ge=1, le=10, description="Poignancy from 1 (mundane) to 10 (highly significant).")
    reason: str = Field(..., description="One-sentence justification for the score.")


def _normalize(score: int) -> float:
    """Map the model's 1-10 scale onto the ``[0, 1]`` range ``Decaying.importance`` validates."""
    return (score - 1) / 9.0


class ImportanceScorer:
    """Scores episode content for salience, built once and reused.

    One ``Agent`` per instance, never one per call -- the same reuse discipline CLAUDE.md asks of
    every other agent, since a session scoring every episode it writes would otherwise build one
    per episode.
    """

    _agent: "Agent"

    def __init__(self, model: Model) -> None:
        # Deferred: `egpt.agent` imports `egpt.memory` (for `MemoryManager`'s default tools), so a
        # module-level import here would make `egpt.memory` depend on a package that depends on it.
        from egpt.agent import Agent

        self._agent = Agent(
            model=model,
            description="You rate how significant, memorable, or consequential an event is.",
            instructions=_INSTRUCTIONS,
            output_schema=ImportanceScore,
        )

    @staticmethod
    def _require_content(content: str) -> str:
        if not isinstance(content, str) or not content.strip():
            raise MemoryOperationError(
                f"Importance scoring needs non-empty episode content; got {content!r}.",
                field_name="content",
            )
        return content

    @staticmethod
    def _require_score(result: object) -> float:
        if not isinstance(result, ImportanceScore):
            raise MemoryOperationError(
                f"The importance scorer returned {result!r} instead of an ImportanceScore.",
                field_name="content",
            )
        return _normalize(result.score)

    def score(self, content: str) -> float:
        """Score ``content``'s salience, normalized to ``[0, 1]``."""
        content = self._require_content(content)
        response = self._agent.run(content, stream=False)
        return self._require_score(response.content)

    async def ascore(self, content: str) -> float:
        """Async twin of :meth:`score`."""
        content = self._require_content(content)
        response = await self._agent.arun(content, stream=False)
        return self._require_score(response.content)
