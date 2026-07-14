"""
Inception Tool Use
==================

Cookbook example for `inception/tool_use.py`.
"""

from egpt.agent import Agent
from egpt.models.inception import Inception
from egpt.tools.websearch import WebSearchTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=Inception(id="mercury-2"),
    tools=[WebSearchTools()],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent.print_response("Whats happening in France?", stream=True)
