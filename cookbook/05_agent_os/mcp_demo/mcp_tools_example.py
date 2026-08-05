"""
Example EgptOS app where the agent has MCPTools.

EgptOS handles the lifespan of the MCPTools internally.
"""

from egpt.agent import Agent
from egpt.db.sqlite import SqliteDb
from egpt.models.anthropic import Claude
from egpt.os import EgptOS
from egpt.tools.mcp import MCPTools

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# Setup the database
db = SqliteDb(db_file="tmp/egptos.db")

mcp_tools = MCPTools(transport="streamable-http", url="https://modelcontextprotocol.io")

# Setup basic agent
mcp_support_agent = Agent(
    id="mcp-support-agent",
    name="MCP Support Agent",
    model=Claude(id="claude-sonnet-4-5"),
    db=db,
    tools=[mcp_tools],
    add_history_to_context=True,
    num_history_runs=3,
    markdown=True,
)


agent_os = EgptOS(
    description="Example app with MCP Tools",
    agents=[mcp_support_agent],
)


app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Run your EgptOS.

    You can see test your EgptOS at:
    http://localhost:7777/docs

    """
    # Don't use reload=True here, this can cause issues with the lifespan
    agent_os.serve(app="mcp_tools_example:app")
