"""
Web Tools
=============================

Demonstrates web tools.
"""

from egpt.agent import Agent
from egpt.tools.webtools import WebTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


agent = Agent(tools=[WebTools()])

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response("Tell me about https://tinyurl.com/57bmajz4")
