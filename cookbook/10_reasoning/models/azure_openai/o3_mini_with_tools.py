"""
O3 Mini With Tools
==================

Demonstrates this reasoning cookbook example.
"""

from egpt.agent import Agent
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.azure.openai_chat import AzureOpenAI


# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------
def run_example() -> None:
    agent = Agent(
        model=AzureOpenAI(id="o3-mini"),
        tools=[YFinanceTools()],
        instructions="Use tables to display data.",
        markdown=True,
    )
    agent.print_response("Write a report comparing NVDA to TSLA", stream=True)


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_example()
