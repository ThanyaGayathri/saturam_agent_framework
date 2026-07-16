"""
Reasoning Agent
===============

Demonstrates reasoning agent.
"""

from egpt.agent.agent import Agent
from egpt.models.openai import OpenAIResponses
from egpt.os import EgptOS
from egpt.os.interfaces.agui import AGUI
from egpt.tools.websearch import WebSearchTools

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

chat_agent = Agent(
    name="Assistant",
    model=OpenAIResponses(id="o4-mini"),
    instructions="You are a helpful AI assistant.",
    add_datetime_to_context=True,
    add_history_to_context=True,
    add_location_to_context=True,
    timezone_identifier="Etc/UTC",
    markdown=True,
    tools=[WebSearchTools()],
)

# Setup your EgptOS app
agent_os = EgptOS(
    agents=[chat_agent],
    interfaces=[AGUI(agent=chat_agent)],
)
app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Run your EgptOS.

    You can see the configuration and available apps at:
    http://localhost:9001/config

    """
    agent_os.serve(app="reasoning_agent:app", reload=True, port=9001)
