"""
Gemini Finance Agent
====================

Demonstrates this reasoning cookbook example.
"""
# ! pip install -U google-genai yfinance

from egpt.agent import Agent
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.google import Gemini
from egpt.tools.reasoning import ReasoningTools


# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------
def run_example() -> None:
    thinking_agent = Agent(
        model=Gemini(id="gemini-3.5-flash"),
        tools=[
            ReasoningTools(add_instructions=True),
            YFinanceTools(),
        ],
        instructions="Use tables where possible",
        markdown=True,
        stream_events=True,
    )
    thinking_agent.print_response(
        "Write a report comparing NVDA to TSLA in detail",
        stream=True,
        show_reasoning=True,
    )


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_example()
