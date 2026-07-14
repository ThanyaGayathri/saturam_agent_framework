"""
Linkup Tools
=============================

Demonstrates linkup tools.
"""

from egpt.agent import Agent
from egpt.tools.linkup import LinkupTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


agent = Agent(tools=[LinkupTools()])

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response("What's the latest news in French politics?", markdown=True)
