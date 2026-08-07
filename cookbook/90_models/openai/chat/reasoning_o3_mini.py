"""
Openai Reasoning O3 Mini
========================

Cookbook example for `openai/chat/reasoning_o3_mini.py`.
"""

from egpt.agent import Agent
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.openai import OpenAIChat

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=OpenAIChat(id="o3-mini", reasoning_effort="high"),
    tools=[YFinanceTools()],
    markdown=True,
)

# Print the response in the terminal
agent.print_response("Write a report on the NVDA, is it a good buy?", stream=True)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
