"""
LangWatch Integration
=====================

Demonstrates instrumenting an Agno agent and sending traces to LangWatch.
"""

import langwatch
from egpt.agent import Agent
from egpt.models.openai import OpenAIChat
from egpt.tools.yfinance import YFinanceTools
from openinference.instrumentation.agno import AgnoInstrumentor

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
# Initialize LangWatch and instrument Agno
langwatch.setup(instrumentors=[AgnoInstrumentor()])


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
