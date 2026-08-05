"""
Jinareader Tools
=============================

Demonstrates jinareader tools.
"""

from egpt.agent import Agent
from egpt.tools.jina import JinaReaderTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


agent = Agent(tools=[JinaReaderTools()])

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response(
        "Summarize: https://github.com/modelcontextprotocol/modelcontextprotocol"
    )
