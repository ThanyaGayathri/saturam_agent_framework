"""
Composio Tools
=============================

Demonstrates composio tools.
"""

from composio_agno import Action, ComposioToolSet
from egpt.agent import Agent

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


toolset = ComposioToolSet()
composio_tools = toolset.get_tools(
    actions=[Action.GITHUB_STAR_A_REPOSITORY_FOR_THE_AUTHENTICATED_USER]
)
agent = Agent(tools=composio_tools)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response("Can you star modelcontextprotocol/modelcontextprotocol repo?")
