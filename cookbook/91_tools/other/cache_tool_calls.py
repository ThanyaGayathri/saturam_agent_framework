"""
Cache Tool Calls
=============================

Demonstrates cache tool calls.
"""

import asyncio

from egpt.agent import Agent
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.openai import OpenAIChat

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


agent = Agent(
    model=OpenAIChat(id="gpt-4o-mini"),
    tools=[WebSearchTools(), YFinanceTools(cache_results=True)],
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(
        agent.aprint_response(
            "What is the current stock price of AAPL and latest news on 'Apple'?",
            markdown=True,
        )
    )
