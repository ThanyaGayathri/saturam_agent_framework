"""Streaming example using OpenRouter with the Responses API.

Requirements:
- Set OPENROUTER_API_KEY environment variable
"""

from egpt.agent import Agent
from egpt.models.openrouter import OpenRouterResponses

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=OpenRouterResponses(id="openai/gpt-oss-20b", reasoning={"enabled": True}),
    markdown=True,
)

# Stream the response
agent.print_response("Write a short poem about the moon", stream=True)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
