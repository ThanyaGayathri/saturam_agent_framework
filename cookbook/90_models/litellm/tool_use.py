"""
Litellm Tool Use
================

Cookbook example for `litellm/tool_use.py`.
"""

import asyncio

from egpt.agent import Agent
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.litellm import LiteLLM

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

openai_agent = Agent(
    model=LiteLLM(
        id="gpt-4o",
        name="LiteLLM",
    ),
    markdown=True,
    tools=[YFinanceTools()],
)

# Ask a question that would likely trigger tool use

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # --- Sync ---
    openai_agent.print_response("How is TSLA stock doing right now?")

    # --- Sync + Streaming ---
    openai_agent.print_response("Whats happening in France?", stream=True)

    # --- Async ---
    asyncio.run(openai_agent.aprint_response("What is happening in France?"))
