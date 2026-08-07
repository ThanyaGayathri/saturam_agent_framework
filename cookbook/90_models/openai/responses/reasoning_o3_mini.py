"""
Openai Reasoning O3 Mini
========================

Cookbook example for `openai/responses/reasoning_o3_mini.py`.
"""

from egpt.agent import Agent
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.openai import OpenAIResponses

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=OpenAIResponses(id="o3-mini"),
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
