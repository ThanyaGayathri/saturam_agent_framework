"""
Cerebras Openai Oss Gpt
=======================

Cookbook example for `cerebras_openai/oss_gpt.py`.
"""

from egpt.agent.agent import Agent
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.cerebras.cerebras_openai import CerebrasOpenAI

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=CerebrasOpenAI(
        id="gpt-oss-120b",
    ),
    tools=[WebSearchTools()],
    markdown=True,
)

# Print the response in the terminal
agent.print_response("Whats happening in France?")

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
