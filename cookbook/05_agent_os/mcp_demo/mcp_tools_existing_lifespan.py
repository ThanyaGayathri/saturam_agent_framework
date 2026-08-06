"""
Example EgptOS app where the agent has MCPTools.

EgptOS handles the lifespan of the MCPTools internally.

In addition you can pass your own lifespan to EgptOS.
"""

from contextlib import asynccontextmanager

from egpt.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.models.anthropic import Claude
from egpt.os import EgptOS
from egpt.tools.mcp import MCPTools
from egpt.utils.log import log_info

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# Setup the database
db = SqliteDb(db_file="tmp/egptos.db")

mcp_tools = MCPTools(
    transport="streamable-http", url="https://modelcontextprotocol.io/mcp"
)

# Setup basic support agent
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


@asynccontextmanager
async def lifespan(app):
    log_info("Starting My FastAPI App")
    yield
    log_info("Stopping My FastAPI App")


agent_os = EgptOS(
    description="Example app with MCP Tools",
    agents=[mcp_support_agent],
    enable_mcp_server=True,
    lifespan=lifespan,
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
    agent_os.serve(app="mcp_tools_existing_lifespan:app")
