"""
Claude Agent SDK with session persistence.

Demonstrates multi-turn conversations where chat history is persisted
to EGPT's DB. Each run is stored as a session with messages, so you
can resume conversations and see history in the EgptOS UI.

Requirements:
    pip install claude-agent-sdk

Usage:
    python cookbook/frameworks/claude-agent-sdk/claude_session_egptos.py
"""

from egpt.agents.claude import ClaudeAgent
from egpt.db.postgres import PostgresDb
from egpt.os import EgptOS

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"
pg_db = PostgresDb(db_url=db_url)

agent = ClaudeAgent(
    name="Claude Chat",
    model="claude-sonnet-4-6",
    allowed_tools=["WebSearch"],
    permission_mode="acceptEdits",
    max_turns=7,
    db=pg_db,
)

# ---------------------------------------------------------------------------
# Setup EgptOS
# ---------------------------------------------------------------------------
agent_os = EgptOS(
    id="claude-session-egptos",
    name="Claude Agent SDK Example",
    description="EgptOS serving a Claude Agent SDK agent",
    agents=[agent],
)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent_os.serve(app="claude_session_egptos:app", reload=True)
