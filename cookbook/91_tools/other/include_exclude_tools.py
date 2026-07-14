"""
Include Exclude Tools
=============================

Demonstrates include exclude tools.
"""

import asyncio

from egpt.agent import Agent
from egpt.models.openai import OpenAIChat
from egpt.tools.calculator import CalculatorTools
from egpt.tools.websearch import WebSearchTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


agent = Agent(
    model=OpenAIChat(id="gpt-5.2"),
    tools=[
        CalculatorTools(
            exclude_tools=["exponentiate", "factorial", "is_prime", "square_root"],
        ),
        WebSearchTools(include_tools=["web_search"]),
    ],
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(
        agent.aprint_response(
            "Search the web for a difficult sum that can be done with normal arithmetic and solve it.",
            markdown=True,
        )
    )
