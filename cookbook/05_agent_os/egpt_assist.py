"""
EGPT Assist
===========

Demonstrates a minimal EGPT agent backed by the Model Context Protocol docs.
"""

from egpt.agent import Agent
from egpt.db.sqlite import SqliteDb
from egpt.models.anthropic import Claude
from egpt.os import EgptOS
from egpt.tools.mcp import MCPTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

egpt_assist = Agent(
    name="EGPT Assist",
    model=Claude(id="claude-sonnet-4-5"),
    db=SqliteDb(db_file="egpt.db"),
    tools=[MCPTools(url="https://modelcontextprotocol.io/mcp")],
    add_datetime_to_context=True,
    add_history_to_context=True,
    num_history_runs=10,
    markdown=True,
)

agent_os = EgptOS(agents=[egpt_assist])
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="egpt_assist:app", reload=True)
