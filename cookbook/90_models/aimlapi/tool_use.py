"""Run `pip install ddgs` to install dependencies."""

import asyncio

from egpt.agent import Agent
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.aimlapi import AIMLAPI

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=AIMLAPI(id="gpt-4o-mini"),
    tools=[WebSearchTools()],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # --- Sync ---
    agent.print_response("Whats happening in France?")

    # --- Async + Streaming ---
    asyncio.run(agent.aprint_response("Whats happening in France?", stream=True))
