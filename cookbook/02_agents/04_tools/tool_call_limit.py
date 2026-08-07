"""
Tool Call Limit
=============================

This cookbook shows how to use tool call limit to control the number of tool calls an agent can make.
"""

from egpt.agent import Agent
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.openai import OpenAIResponses

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    model=OpenAIResponses(id="gpt-5-mini"),
    tools=[YFinanceTools()],
    tool_call_limit=1,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # It should only call the first tool and fail to call the second tool.
    agent.print_response(
        "Find me the current price of TSLA, then after that find me the latest news about Tesla.",
        stream=True,
    )
