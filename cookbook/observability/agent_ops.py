"""
AgentOps Integration
====================

Demonstrates logging EGPT model calls with AgentOps.
"""

import agentops
from egpt.agent import Agent
from egpt.models.openai import OpenAIChat

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
# Initialize AgentOps
agentops.init()


# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(model=OpenAIChat(id="gpt-4o"))


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    response = agent.run("Share a 2 sentence horror story")
    print(response.content)
