"""
Zendesk Tools
=============================

Demonstrates zendesk tools.
"""

from egpt.agent import Agent
from egpt.tools.zendesk import ZendeskTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


agent = Agent(tools=[ZendeskTools()])

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response("How do I login?", markdown=True)
