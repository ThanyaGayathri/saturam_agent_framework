"""
AgentOS Registry App
====================

Demonstrates configuring AgentOS with a Registry and serving the app.
"""

from egpt.agent.agent import Agent
from egpt.db.postgres import PostgresDb
from egpt.models.anthropic import Claude
from egpt.models.azure import AzureOpenAI
from egpt.models.openai import OpenAIChat
from egpt.os import AgentOS
from egpt.registry import Registry
from egpt.tools.calculator import CalculatorTools
from egpt.tools.duckduckgo import DuckDuckGoTools

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai", id="postgres_db")


def sample_tool():
    return "Hello, world!"


# ---------------------------------------------------------------------------
# Create Registry
# ---------------------------------------------------------------------------
registry = Registry(
    name="Agno Registry",
    tools=[DuckDuckGoTools(), sample_tool, CalculatorTools()],
    models=[
        OpenAIChat(id="gpt-5-mini"),
        OpenAIChat(id="gpt-5"),
        Claude(id="claude-sonnet-4-5"),
        AzureOpenAI(id="gpt-5-mini"),
    ],
    dbs=[db],
)

# ---------------------------------------------------------------------------
# Create AgentOS App
# ---------------------------------------------------------------------------
agent = Agent(
    id="registry-agent",
    model=Claude(id="claude-sonnet-4-5"),
    db=db,
)

agent_os = AgentOS(
    agents=[agent],
    id="registry-agent-os",
    registry=registry,
    db=db,
)

app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run AgentOS App
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent_os.serve(app="agent_os_registry:app", reload=True)
