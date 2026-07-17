"""
Basic
=====

Demonstrates basic.
"""

from egpt.agent import Agent
from egpt.db.sqlite import SqliteDb
from egpt.models.openai import OpenAIChat
from egpt.os.app import EgptOS
from egpt.os.interfaces.whatsapp import Whatsapp

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

agent_db = SqliteDb(db_file="tmp/persistent_memory.db")
basic_agent = Agent(
    name="Basic Agent",
    model=OpenAIChat(id="gpt-4o"),
    db=agent_db,
    add_history_to_context=True,
    num_history_runs=3,
    add_datetime_to_context=True,
    markdown=True,
    instructions=[
        "You are chatting on WhatsApp. Keep responses conversational and natural.",
        "Structure your responses as separate short paragraphs separated by double newlines.",
        "Each paragraph should be a distinct thought or message, like a human would send on WhatsApp.",
        "Keep each paragraph to 1-3 sentences max.",
    ],
)


# Setup our EgptOS app
agent_os = EgptOS(
    agents=[basic_agent],
    interfaces=[Whatsapp(agent=basic_agent)],
)
app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="basic:app", reload=True)
