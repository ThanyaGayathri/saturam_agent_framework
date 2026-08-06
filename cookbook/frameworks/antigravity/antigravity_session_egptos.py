"""
Antigravity on EgptOS with session persistence.

Serves an `AntigravityAgent` through EgptOS, backed by a SQLite DB so runs
and sessions persist across restarts and appear in the EgptOS UI's session
list.

Requirements:
    export GEMINI_API_KEY=...

Usage:
    .venvs/demo/bin/python cookbook/frameworks/antigravity/antigravity_session_egptos.py
"""

from egpt.agents.antigravity import AntigravityAgent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.os import EgptOS

db = SqliteDb(db_file="tmp/antigravity_egptos.db")

antigravity_agent = AntigravityAgent(
    name="Antigravity",
    description="Antigravity with persisted sessions",
    db=db,
)

agent_os = EgptOS(
    name="Antigravity Sessioned",
    description="EgptOS serving an Antigravity-backed agent with SQLite sessions",
    agents=[antigravity_agent],
)
app = agent_os.get_app()

if __name__ == "__main__":
    agent_os.serve(app="antigravity_session_egptos:app", reload=True)
