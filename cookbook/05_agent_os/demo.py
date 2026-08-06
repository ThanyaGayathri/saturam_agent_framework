"""
EgptOS Demo

Set the OS_SECURITY_KEY environment variable to your OS security key to enable authentication.

Prerequisites:
uv pip install -U fastapi uvicorn sqlalchemy pgvector psycopg openai ddgs yfinance
"""

from egpt.agent import Agent
from egpt.db.postgres import PostgresDb
from egpt.knowledge.knowledge import Knowledge
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS
from egpt.team import Team
from egpt.tools.mcp import MCPTools
from egpt.tools.websearch import WebSearchTools
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
mcp_docs_agent = Agent(
    name="MCP Docs Agent",
    model=OpenAIChat(id="gpt-4.1"),
    tools=[
        MCPTools(transport="streamable-http", url="https://modelcontextprotocol.io/mcp")
    ],
    db=db,
    update_memory_on_run=True,
    knowledge=knowledge,
    markdown=True,
)

simple_agent = Agent(
    name="Simple Agent",
    role="Simple agent",
    id="simple_agent",
    model=OpenAIChat(id="gpt-5.2"),
    instructions=["You are a simple agent"],
    db=db,
    update_memory_on_run=True,
)

research_agent = Agent(
    name="Research Agent",
    role="Research agent",
    id="research_agent",
    model=OpenAIChat(id="gpt-5.2"),
    instructions=["You are a research agent"],
    tools=[WebSearchTools()],
    db=db,
    update_memory_on_run=True,
)

# Create a team
research_team = Team(
    name="Research Team",
    description="A team of agents that research the web",
    members=[research_agent, simple_agent],
    model=OpenAIChat(id="gpt-4.1"),
    id="research_team",
    instructions=[
        "You are the lead researcher of a research team.",
    ],
    db=db,
    update_memory_on_run=True,
    add_datetime_to_context=True,
    markdown=True,
)

# Create the EgptOS
agent_os = EgptOS(
    id="egptos-demo",
    agents=[mcp_docs_agent],
    teams=[research_team],
)
app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="demo:app", port=7777, reload=True)
