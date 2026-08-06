"""
Openai Verbosity Control
========================

Cookbook example for `openai/responses/verbosity_control.py`.
"""

from egpt.agent import Agent
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.openai import OpenAIChat

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=OpenAIChat(id="gpt-5", verbosity="high"),
    tools=[YFinanceTools()],
    instructions="Use tables to display data.",
    markdown=True,
)
agent.print_response("Write a report comparing NVDA to TSLA", stream=True)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
