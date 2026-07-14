"""
Team Instantiation Performance Evaluation
=========================================

Demonstrates measuring team instantiation performance.
"""

from egpt.agent import Agent
from egpt.eval.performance import PerformanceEval
from egpt.models.openai import OpenAIChat
from egpt.team.team import Team

# ---------------------------------------------------------------------------
# Create Team Member
# ---------------------------------------------------------------------------
team_member = Agent(model=OpenAIChat(id="gpt-4o"))


# ---------------------------------------------------------------------------
# Create Benchmark Function
# ---------------------------------------------------------------------------
def instantiate_team():
    return Team(members=[team_member])


# ---------------------------------------------------------------------------
# Create Evaluation
# ---------------------------------------------------------------------------
instantiation_perf = PerformanceEval(
    name="Instantiation Performance Team", func=instantiate_team, num_iterations=1000
)

# ---------------------------------------------------------------------------
# Run Evaluation
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    instantiation_perf.run(print_results=True, print_summary=True)
