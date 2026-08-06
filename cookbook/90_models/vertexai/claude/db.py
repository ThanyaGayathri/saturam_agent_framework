"""Run `uv pip install ddgs sqlalchemy anthropic` to install dependencies."""

from egpt.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.vertexai.claude import Claude

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

# Setup the database
db = SqliteDb(db_file="tmp/data.db")

agent = Agent(
    model=Claude(id="claude-sonnet-4@20250514"),
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
