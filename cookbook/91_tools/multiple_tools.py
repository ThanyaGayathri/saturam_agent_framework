"""Run `uv pip install openai ddgs yfinance` to install dependencies."""

from egpt.agent import Agent
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.openai import OpenAIChat

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=[WebSearchTools(), YFinanceTools()],
    instructions=["Use tables to display data"],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response(
        "Write a thorough report on NVDA, get all financial information and latest news",
        stream=True,
    )
