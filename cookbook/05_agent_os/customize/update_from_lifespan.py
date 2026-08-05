"""
Update From Lifespan
====================

Demonstrates update from lifespan.
"""

from contextlib import asynccontextmanager

from egpt.agent.agent import Agent
from egpt.db.postgres.postgres import PostgresDb
from egpt.os import EgptOS
from egpt.tools.mcp import MCPTools

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

db = PostgresDb(id="basic-db", db_url="postgresql+psycopg://ai:ai@localhost:5532/ai")

# First agent. We will add this to the EgptOS on initialization.
agent1 = Agent(
    name="First Agent",
    markdown=True,
)

# Second agent. We will add this to the EgptOS in the lifespan function.
agent2 = Agent(
    id="second-agent",
    name="Second Agent",
    tools=[
        MCPTools(transport="streamable-http", url="https://modelcontextprotocol.io")
    ],
    markdown=True,
    db=db,
)


# Lifespan function receiving the EgptOS instance as parameter.
@asynccontextmanager
async def lifespan(app, agent_os):
    # Add the new Agent
    agent_os.agents.append(agent2)

    # Resync the EgptOS
    agent_os.resync(app=app)

    yield


# Setup our EgptOS with the lifespan function and the first agent.
agent_os = EgptOS(
    lifespan=lifespan,
    agents=[agent1],
    enable_mcp_server=True,
)

# Get our app.
app = agent_os.get_app()

# Serve the app.
# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="update_from_lifespan:app", reload=True)
