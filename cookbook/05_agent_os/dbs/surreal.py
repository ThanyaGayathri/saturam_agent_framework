"""Example showing how to use EgptOS with SurrealDB as database"""

from egpt.agent import Agent
from egpt.integrations.db.surrealdb import SurrealDb
from egpt.knowledge.knowledge import Knowledge
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS
from egpt.team.team import Team
from egpt.vectordb.pgvector import PgVector

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# Setup the SurrealDB database
SURREALDB_URL = "ws://localhost:8000"
SURREALDB_USER = "root"
SURREALDB_PASSWORD = "root"
SURREALDB_NAMESPACE = "egpt"
SURREALDB_DATABASE = "agent_os_demo"

creds = {"username": SURREALDB_USER, "password": SURREALDB_PASSWORD}
db = SurrealDb(None, SURREALDB_URL, creds, SURREALDB_NAMESPACE, SURREALDB_DATABASE)

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"
vector_db = PgVector(table_name="agent_os_knowledge", db_url=db_url)

knowledge = Knowledge(
    contents_db=db,
    vector_db=vector_db,
    name="Agent OS Knowledge",
    description="Knowledge for Agent OS demo",
)

# Agent Setup
agent = Agent(
    db=db,
    name="Basic Agent",
    id="basic-agent",
    model=OpenAIChat(id="gpt-4o"),
    add_history_to_context=True,
    num_history_runs=3,
    knowledge=knowledge,
)

# Team Setup
team = Team(
    db=db,
    id="basic-team",
    name="Team Agent",
    model=OpenAIChat(id="gpt-4o"),
    members=[agent],
    add_history_to_context=True,
    num_history_runs=3,
)

# EgptOS Setup
agent_os = EgptOS(
    description="Example OS setup",
    agents=[agent],
    teams=[team],
)

# Get the app
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Serve the app
    agent_os.serve(app="surreal:app", reload=True)
