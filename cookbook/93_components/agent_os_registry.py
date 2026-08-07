"""
EgptOS Registry App
====================

Demonstrates configuring EgptOS with a Registry and serving the app.
"""

from egpt.agent.agent import Agent
from egpt.integrations.db.postgres import PostgresDb
from egpt.integrations.tools.calculator import CalculatorTools
from egpt.integrations.tools.duckduckgo import DuckDuckGoTools
from egpt.models.anthropic import Claude
from egpt.models.azure import AzureOpenAI
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS
from egpt.registry import Registry

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
    name="EGPT Registry",
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
# Create EgptOS App
# ---------------------------------------------------------------------------
agent = Agent(
    id="registry-agent",
    model=Claude(id="claude-sonnet-4-5"),
    db=db,
)

agent_os = EgptOS(
    agents=[agent],
    id="registry-agent-os",
    registry=registry,
    db=db,
)

app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run EgptOS App
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent_os.serve(app="agent_os_registry:app", reload=True)
