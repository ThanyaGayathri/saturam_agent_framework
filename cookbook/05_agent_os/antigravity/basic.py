"""
Antigravity on EgptOS
======================

Serves an Antigravity-backed agent through EgptOS, with sessions persisted
to a local SQLite database.
"""

from egpt.agent import Agent
from egpt.db.sqlite import SqliteDb
from egpt.models.google import GeminiInteractions
from egpt.os import EgptOS

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
db = SqliteDb(
    db_file="tmp/antigravity_egptos.db",
)

# ---------------------------------------------------------------------------
# Create Antigravity Agent and EgptOS
# ---------------------------------------------------------------------------
antigravity_agent = Agent(
    db=db,
    id="antigravity-agent",
    model=GeminiInteractions(agent="antigravity-preview-05-2026", environment="remote"),
    add_history_to_context=True,
    num_history_runs=3,
)

agent_os = EgptOS(
    description="Example OS setup",
    agents=[antigravity_agent],
)

# ---------------------------------------------------------------------------
# Create EgptOS App
# ---------------------------------------------------------------------------
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent_os.serve(app="basic:app", reload=True)
