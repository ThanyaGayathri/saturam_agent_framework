"""
Parallel Tools
=============================

Demonstrates parallel tools.
"""

from egpt.agent import Agent
from egpt.models.openai import OpenAIResponses
from egpt.tools.parallel import ParallelTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


agent = Agent(
    model=OpenAIResponses(id="gpt-5.4"),
    tools=[ParallelTools()],
    instructions="No need to tell me its based on your research.",
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response(
        "Tell me about Agno's EgptOS?",
        stream=True,
        stream_events=True,
    )
