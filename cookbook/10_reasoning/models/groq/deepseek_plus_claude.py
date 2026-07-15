"""
Deepseek Plus Claude
====================

Demonstrates this reasoning cookbook example.
"""

from egpt.agent import Agent
from egpt.models.anthropic import Claude
from egpt.models.groq import Groq


# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------
def run_example() -> None:
    deepseek_plus_claude = Agent(
        model=Claude(id="claude-3-7-sonnet-20250219"),
        reasoning_model=Groq(
            id="qwen/qwen3-32b",
            temperature=0.6,
            max_tokens=1024,
            top_p=0.95,
        ),
    )
    deepseek_plus_claude.print_response("9.11 and 9.9 -- which is bigger?", stream=True)


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_example()
