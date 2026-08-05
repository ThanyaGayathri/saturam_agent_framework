"""
Save Agent to Database
======================

Demonstrates creating an agent and saving it to the database.
"""

from egpt.agent.agent import Agent
from egpt.db.postgres import PostgresDb
from egpt.models.openai import OpenAIChat

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai")

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    id="egpt-agent",
    model=OpenAIChat(id="gpt-5-mini"),
    name="EGPT Agent",
    db=db,
)

# ---------------------------------------------------------------------------
# Run Agent Save Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # agent.print_response("How many people live in Canada?")

    # Save the agent to the database
    version = agent.save()
    print(f"Saved agent as version {version}")

    # By default, saving a agent will create a new version of the agent

    # Delete the agent from the database (soft delete by default)
    # agent.delete()

    # Hard delete (permanently removes from database)
    # agent.delete(hard_delete=True)
