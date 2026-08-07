"""
Llama Reasoning Tools
=====================

Demonstrates this reasoning cookbook example.
"""

from egpt.agent import Agent
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.meta import Llama
from egpt.tools.reasoning import ReasoningTools


# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------
def run_example() -> None:
    reasoning_agent = Agent(
        model=Llama(id="Llama-4-Maverick-17B-128E-Instruct-FP8"),
        tools=[
            ReasoningTools(
                enable_think=True,
                enable_analyze=True,
                add_instructions=True,
            ),
            YFinanceTools(),
        ],
        instructions="Use tables where possible",
        markdown=True,
    )
    reasoning_agent.print_response(
        "What is the NVDA stock price? Write me a report",
        show_full_reasoning=True,
    )


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_example()
