"""EGPT EgptOS A2A Server for testing A2AClient.

This server uses EGPT's EgptOS to create an A2A-compatible
agent that can be tested with A2AClient.

Prerequisites:
    export OPENAI_API_KEY=your_key

Usage:
    python cookbook/05_agent_os/client_a2a/servers/egpt_server.py

The server will start at http://localhost:7003
"""

from egpt.agent.agent import Agent
from egpt.db.sqlite import SqliteDb
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

db = SqliteDb(db_file="tmp/agent.db")
chat_agent = Agent(
    name="basic-agent",
    model=OpenAIChat(id="gpt-5.2"),
    id="basic-agent",
    db=db,
    description="A helpful AI assistant that provides thoughtful answers.",
    instructions="You are a helpful AI assistant.",
    add_datetime_to_context=True,
    add_history_to_context=True,
    markdown=True,
)

agent_os = EgptOS(
    agents=[chat_agent],
    a2a_interface=True,
)
app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="egpt_server:app", reload=True, port=7003)
