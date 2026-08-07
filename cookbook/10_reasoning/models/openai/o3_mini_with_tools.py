"""
O3 Mini With Tools
==================

Demonstrates this reasoning cookbook example.
"""

from egpt.agent import Agent
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.openai import OpenAIChat


# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------
def run_example() -> None:
    agent = Agent(
        model=OpenAIChat(id="o3-mini"),
        tools=[WebSearchTools(enable_news=False)],
        instructions="Use tables to display data.",
        markdown=True,
    )

    agent.print_response("Write a report comparing NVDA to TSLA", stream=True)


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_example()
