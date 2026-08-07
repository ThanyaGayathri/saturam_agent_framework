"""Run `uv pip install ddgs` to install dependencies."""

import asyncio

from egpt.agent import Agent
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.deepseek import DeepSeek

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

"""
DeepSeek V4 models support tool calls in both thinking and non-thinking modes.
"""

agent = Agent(
    model=DeepSeek(id="deepseek-v4-flash"),
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
