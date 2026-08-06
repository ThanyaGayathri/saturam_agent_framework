"""
Traces with EgptOS using SqliteDb

Requirements:
    uv pip install opentelemetry-api opentelemetry-sdk openinference-instrumentation-agno
"""

from egpt.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS
from egpt.tools.hackernews import HackerNewsTools

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# Set up database
db = SqliteDb(db_file="tmp/traces.db")

agent = Agent(
    name="HackerNews Agent",
    model=OpenAIChat(id="gpt-5.2"),
    tools=[HackerNewsTools()],
    instructions="You are a hacker news agent. Answer questions concisely.",
    markdown=True,
    db=db,
)

# Setup our EgptOS app
agent_os = EgptOS(
    description="Example app for tracing HackerNews",
    agents=[agent],
    tracing=True,
)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="basic_agent_with_postgresdb:app", reload=True)
