"""
Team Reliability Evaluation for News Search
===========================================

Demonstrates tool-call reliability checks for a team workflow.
"""

from typing import Optional

from egpt.agent import Agent
from egpt.eval.reliability import ReliabilityEval, ReliabilityResult
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.openai import OpenAIChat
from egpt.run.team import TeamRunOutput
from egpt.team.team import Team

# ---------------------------------------------------------------------------
# Create Team
# ---------------------------------------------------------------------------
team_member = Agent(
    name="News Searcher",
    model=OpenAIChat("gpt-4o"),
    role="Searches the web for the latest news.",
    tools=[WebSearchTools(enable_news=True)],
)
team = Team(
    name="News Research Team",
    model=OpenAIChat("gpt-4o"),
    members=[team_member],
    markdown=True,
    show_members_responses=True,
)
expected_tool_calls = [
    "delegate_task_to_member",
    "search_news",
]


# ---------------------------------------------------------------------------
# Create Evaluation Function
# ---------------------------------------------------------------------------
def evaluate_team_reliability():
    response: TeamRunOutput = team.run("What is the latest news on AI?")
    evaluation = ReliabilityEval(
        name="Team Reliability Evaluation",
        team_response=response,
        expected_tool_calls=expected_tool_calls,
    )
    result: Optional[ReliabilityResult] = evaluation.run(print_results=True)
    if result:
        result.assert_passed()


# ---------------------------------------------------------------------------
# Run Evaluation
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    evaluate_team_reliability()
