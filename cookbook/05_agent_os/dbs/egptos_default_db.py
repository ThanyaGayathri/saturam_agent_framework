"""
EgptOS Demo

Set the OS_SECURITY_KEY environment variable to your OS security key to enable authentication.

Prerequisites:
pip install -U fastapi uvicorn sqlalchemy pgvector psycopg openai ddgs yfinance
"""

from egpt.agent import Agent
from egpt.db.postgres import PostgresDb
from egpt.knowledge.knowledge import Knowledge
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS
from egpt.tools.mcp import MCPTools
from egpt.vectordb.pgvector import PgVector

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# Database connection
db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"

# Create Postgres-backed memory store
db = PostgresDb(db_url=db_url)

# Create Postgres-backed vector store
vector_db = PgVector(
    db_url=db_url,
    table_name="mcp_docs",
)
knowledge = Knowledge(
    name="MCP Docs",
    contents_db=db,
    vector_db=vector_db,
)

# Create your agents
mcp_agent = Agent(
    name="MCP Agent",
    model=OpenAIChat(id="gpt-4.1"),
    tools=[
        MCPTools(transport="streamable-http", url="https://modelcontextprotocol.io")
    ],
    knowledge=knowledge,
    markdown=True,
)

# Create the EgptOS
agent_os = EgptOS(
    id="egptos-demo",
    agents=[mcp_agent],
    db=db,  # This is the default database for EgptOS, the mcp_agent will use this
)
app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="egptos_default_db:app", port=7777, reload=True)
