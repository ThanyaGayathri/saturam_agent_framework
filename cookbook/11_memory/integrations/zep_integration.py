"""
Zep Integration
===============

Demonstrates Zep-powered memory retrieval for an EGPT agent.
"""

import time

from egpt.agent import Agent
from egpt.integrations.tools.zep import ZepTools
from egpt.models.openai import OpenAIChat

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
# Initialize the ZepTools
zep_tools = ZepTools(user_id="egpt", session_id="egpt-session")

zep_tools.add_zep_message(role="user", content="My name is John Billings")
zep_tools.add_zep_message(role="user", content="I live in NYC")
zep_tools.add_zep_message(role="user", content="I'm going to a concert tomorrow")

# Allow the memories to sync with Zep database
time.sleep(10)


# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    model=OpenAIChat(),
    tools=[zep_tools],
    dependencies={"memory": zep_tools.get_zep_memory(memory_type="context")},
    add_dependencies_to_context=True,
)


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Ask the Agent about the user
    agent.print_response("What do you know about me?")
