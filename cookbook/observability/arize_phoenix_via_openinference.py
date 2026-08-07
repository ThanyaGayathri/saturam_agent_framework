"""
Arize Phoenix Via OpenInference
===============================

Demonstrates instrumenting an EGPT agent with OpenInference and sending traces to Phoenix.
"""

import asyncio
import os

from egpt.agent import Agent
from egpt.integrations.db.in_memory import InMemoryDb
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.openai import OpenAIChat
from phoenix.otel import register
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
os.environ["PHOENIX_API_KEY"] = os.getenv("PHOENIX_API_KEY")
os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = (
    "https://app.phoenix.arize.com/"  # Add the suffix for your organization
)

# Configure the Phoenix tracer
tracer_provider = register(
    project_name="default",  # Default is 'default'
    auto_instrument=True,  # Automatically use the installed OpenInference instrumentation
)


class StockPrice(BaseModel):
    stock_price: float


# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    name="Stock Price Agent",
    model=OpenAIChat(id="gpt-5.2"),
    tools=[YFinanceTools()],
    db=InMemoryDb(),
    instructions="You are a stock price agent. Answer questions in the style of a stock analyst.",
    session_id="test_123",
    output_schema=StockPrice,
)


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(
        agent.aprint_response("What is the current price of Tesla?", stream=True)
    )
