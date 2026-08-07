"""
Reasoning Agent
===============

Demonstrates reasoning agent.
"""

from egpt.agent.agent import Agent
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

reasoning_agent = Agent(
    name="reasoning-agent",
    id="reasoning_agent",
    model=OpenAIChat(id="o4-mini"),
    description="An advanced AI assistant with deep reasoning and analytical capabilities, enhanced with real-time web search to deliver thorough, well-thought-out responses with contextual awareness",
    instructions="You are a helpful AI assistant with reasoning capabilities.",
    add_datetime_to_context=True,
    add_history_to_context=True,
    add_location_to_context=True,
    timezone_identifier="Etc/UTC",
    markdown=True,
    tools=[WebSearchTools()],
)

# Setup your EgptOS app
agent_os = EgptOS(
    agents=[reasoning_agent],
    a2a_interface=True,
)
app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Run your EgptOS with A2A interface.
    You can run the Agent via A2A protocol:
    POST http://localhost:7777/agents/{id}/v1/message:send
    For streaming responses:
    POST http://localhost:7777/agents/{id}/v1/message:stream
    Retrieve the agent card at:
    GET  http://localhost:7777/agents/{id}/.well-known/agent-card.json
    """
    agent_os.serve(app="reasoning_agent:app", reload=True, port=7777)
