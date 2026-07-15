"""
Claude Reasoning Tools
======================

Demonstrates this reasoning cookbook example.
"""

from egpt.agent import Agent
from egpt.models.anthropic import Claude
from egpt.tools.reasoning import ReasoningTools
from egpt.tools.websearch import WebSearchTools


# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------
def run_example() -> None:
    reasoning_agent = Agent(
        model=Claude(id="claude-sonnet-4-20250514"),
        tools=[
            ReasoningTools(add_instructions=True),
            WebSearchTools(enable_news=False),
        ],
        instructions="Use tables to display data.",
        markdown=True,
    )

    # Semiconductor market analysis example
    reasoning_agent.print_response(
        """\
        Analyze the semiconductor market performance focusing on:
        - NVIDIA (NVDA)
        - AMD (AMD)
        - Intel (INTC)
        - Taiwan Semiconductor (TSM)
        Compare their market positions, growth metrics, and future outlook.""",
        stream=True,
        show_full_reasoning=True,
    )


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_example()
