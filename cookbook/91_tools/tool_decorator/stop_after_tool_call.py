"""
Stop After Tool Call
=============================

Demonstrates stop after tool call.
"""

from egpt.agent import Agent
from egpt.tools import tool

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


@tool(stop_after_tool_call=True)
def get_answer_to_life_universe_and_everything() -> str:
    """
    This returns the answer to the life, the universe and everything.
    """
    return "42"


agent = Agent(
    tools=[get_answer_to_life_universe_and_everything],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response("What is the answer to life, the universe and everything?")
