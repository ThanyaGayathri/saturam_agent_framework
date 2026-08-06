"""
Hackernews Tools
=============================

Demonstrates hackernews tools.
"""

from egpt.agent import Agent
from egpt.integrations.tools.hackernews import HackerNewsTools
from egpt.models.anthropic import Claude

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


agent = Agent(
    model=Claude(id="claude-sonnet-4-5"),
    tools=[HackerNewsTools()],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response(
        "Write a report on trending startups and products.", stream=True
    )
