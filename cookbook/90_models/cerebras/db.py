"""Run `uv pip install ddgs sqlalchemy cerebras_cloud_sdk` to install dependencies."""

from egpt.agent import Agent
from egpt.integrations.db.postgres import PostgresDb
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.cerebras import Cerebras

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

# Setup the database
db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"
db = PostgresDb(db_url=db_url)

agent = Agent(
    model=Cerebras(id="llama-3.3-70b"),
    db=db,
    tools=[WebSearchTools()],
    add_history_to_context=True,
)
agent.print_response("How many people live in Canada?")
agent.print_response("What is their national anthem called?")

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
