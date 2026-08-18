"""Episode salience scoring (SM-E5 #112).

``ImportanceScorer`` is the only thing this ticket adds: the schema field, its validation, and its
path to a reader (SM-E7's composite scoring, SM-L6's reflection trigger) all already existed from
SM-E1 (#108). The evaluator agent is mocked throughout -- what is under test is the normalization
and the reuse discipline, not a real model.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import egpt.memory as memory_package
from egpt.memory.episodic.importance import ImportanceScore, ImportanceScorer
from egpt.memory.errors import MemoryOperationError
from egpt.models.base import Model
from egpt.run.agent import RunOutput


def _scorer_with_mocked_agent(score: int = 5, reason: str = "because") -> ImportanceScorer:
    scorer = ImportanceScorer(model=MagicMock(spec=Model))
    response = RunOutput(content=ImportanceScore(score=score, reason=reason))
    scorer._agent.run = MagicMock(return_value=response)
    scorer._agent.arun = AsyncMock(return_value=response)
    return scorer


@pytest.mark.parametrize(
    "score, expected",
    [(1, 0.0), (10, 1.0), (5, 4 / 9), (9, 8 / 9)],
)
def test_the_1_to_10_scale_normalizes_onto_the_units_interval(score, expected):
    """``Decaying.importance`` validates ``[0, 1]`` (SM-C3); the model answers on Generative
    Agents' 1-10 poignancy scale. The two ends must map onto the two ends exactly, not just land
    inside range, or a scorer that always answers 10 would look identical to one always at 0.9."""
    scorer = _scorer_with_mocked_agent(score=score)

    assert scorer.score("shipped v2 to production") == pytest.approx(expected)


def test_score_and_ascore_agree():
    """The sync and async paths make the same decision from the same model, per CLAUDE.md."""
    scorer = _scorer_with_mocked_agent(score=8)

    assert scorer.score("a") == asyncio.run(scorer.ascore("a"))


def test_a_significant_event_outranks_a_trivial_one():
    """Testing note: a high-importance episode outranks a trivial one at equal recency -- true
    already at the scoring step, before any composite (SM-E7) exists to combine it with recency."""
    trivial = _scorer_with_mocked_agent(score=2).score("said good morning")
    significant = _scorer_with_mocked_agent(score=9).score("signed the acquisition")

    assert significant > trivial


def test_one_agent_is_built_and_reused_across_calls():
    """Never one Agent per call (CLAUDE.md) -- a session scoring every episode it writes must not
    build a new evaluator for each one."""
    scorer = _scorer_with_mocked_agent(score=6)
    agent = scorer._agent

    scorer.score("first episode")
    scorer.score("second episode")

    assert scorer._agent is agent
    assert agent.run.call_count == 2


def test_empty_content_is_refused_not_scored():
    scorer = _scorer_with_mocked_agent()

    with pytest.raises(MemoryOperationError) as exc_info:
        scorer.score("   ")
    assert exc_info.value.field_name == "content"
    scorer._agent.run.assert_not_called()


@pytest.mark.asyncio
async def test_empty_content_is_refused_not_scored_async():
    scorer = _scorer_with_mocked_agent()

    with pytest.raises(MemoryOperationError) as exc_info:
        await scorer.ascore("")
    assert exc_info.value.field_name == "content"
    scorer._agent.arun.assert_not_called()


def test_a_non_schema_response_is_refused():
    """The model call is trusted to honour ``output_schema`` (Agent's contract); this guards the
    boundary where trust ends, rather than letting a malformed response rank an episode on a
    string it never scored."""
    scorer = ImportanceScorer(model=MagicMock(spec=Model))
    scorer._agent.run = MagicMock(return_value=RunOutput(content="not a score"))

    with pytest.raises(MemoryOperationError):
        scorer.score("an event")


def test_the_scorer_is_reachable_from_the_memory_package():
    """Exported symmetrically or not at all, the same contract SM-E1 established for the episode
    surface: a caller should not need ``egpt.memory.episodic.importance`` to reach this."""
    assert memory_package.ImportanceScorer is ImportanceScorer
    assert memory_package.ImportanceScore is ImportanceScore
