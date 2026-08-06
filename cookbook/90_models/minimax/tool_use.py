"""
MiniMax Tool Use
================

Cookbook example for `minimax/tool_use.py`.
"""

from egpt.agent import Agent
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.minimax import MiniMax

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=MiniMax(id="MiniMax-M3"),
    markdown=True,
    tools=[WebSearchTools()],
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent.print_response("What is happening in France?", stream=True)
