"""
Youtube Tools
=============================

Demonstrates youtube tools.
"""

from egpt.agent import Agent
from egpt.tools.youtube import YouTubeTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


agent = Agent(
    tools=[YouTubeTools()],
    description="You are a YouTube agent. Obtain the captions of a YouTube video and answer questions.",
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response(
        "Summarize this video https://www.youtube.com/watch?v=Iv9dewmcFbs&t",
        markdown=True,
    )
