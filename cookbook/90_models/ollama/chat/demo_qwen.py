"""
Ollama Demo Qwen
================

Cookbook example for `ollama/chat/demo_qwen.py`.
"""

from egpt.agent import Agent
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.ollama import Ollama

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=Ollama(id="qwen3:8b"),
    tools=[
        YFinanceTools(),
    ],
    instructions="Use tables to display data.",
)

agent.print_response("Write a report on NVDA", stream=True, markdown=True)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
