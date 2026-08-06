"""
Langtrace Integration
=====================

Demonstrates instrumenting an EGPT agent with Langtrace.
"""

# Must precede other imports
from egpt.agent import Agent
from egpt.models.openai import OpenAIChat
from egpt.tools.yfinance import YFinanceTools
from langtrace_python_sdk import langtrace  # type: ignore

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
langtrace.init()


# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    name="Stock Price Agent",
    model=OpenAIChat(id="gpt-5.2"),
    tools=[YFinanceTools()],
    instructions="You are a stock price agent. Answer questions in the style of a stock analyst.",
    debug_mode=True,
)


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response("What is the current price of Tesla?")
