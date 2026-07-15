"""
Antigravity on AgentOS
======================

Serves an Antigravity-backed agent through AgentOS, with sessions persisted
to a local SQLite database.
"""

from egpt.agent import Agent
from egpt.db.sqlite import SqliteDb
from egpt.models.google import GeminiInteractions
from egpt.os import AgentOS

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
db = SqliteDb(
    db_file="tmp/antigravity_agentos.db",
)

# ---------------------------------------------------------------------------
# Create Antigravity Agent and AgentOS
# ---------------------------------------------------------------------------
antigravity_agent = Agent(
    db=db,
    id="antigravity-agent",
    model=GeminiInteractions(agent="antigravity-preview-05-2026", environment="remote"),
    add_history_to_context=True,
    num_history_runs=3,
)

agent_os = AgentOS(
    description="Example OS setup",
    agents=[antigravity_agent],
)

# ---------------------------------------------------------------------------
# Create AgentOS App
# ---------------------------------------------------------------------------
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent_os.serve(app="basic:app", reload=True)
