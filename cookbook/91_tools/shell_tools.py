"""
Shell Tools
=============================

Demonstrates shell tools.
"""

from egpt.agent import Agent
from egpt.tools.shell import ShellTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


agent = Agent(tools=[ShellTools()])

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response("Show me the contents of the current directory", markdown=True)
