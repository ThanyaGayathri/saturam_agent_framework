"""
EGPT Agent on EgptOS
=====================
A native EGPT agent with the Workspace tool, served through EgptOS.

The Workspace tool gives the agent read/write/search access to a local
directory. Destructive ops (write/edit/delete/shell) require human
confirmation by default — EgptOS renders these as approval prompts
in the run timeline.

Usage:
    .venvs/demo/bin/python cookbook/frameworks/00_quickstart/egpt_agent.py
"""

from egpt.agent import Agent
from egpt.db.sqlite import SqliteDb
from egpt.os import EgptOS
from egpt.tools.workspace import Workspace

agent = Agent(
    name="EGPT Agent",
    model="openai:gpt-5.4",
    tools=[
        Workspace(
            root=".",
            allowed=["read", "list", "search"],
            confirm=["write", "edit", "delete", "shell"],
        )
    ],
)

agent_os = EgptOS(
    agents=[agent],
    tracing=True,
    db=SqliteDb(db_file="tmp/egptos.db"),
)
app = agent_os.get_app()

if __name__ == "__main__":
    agent_os.serve(app="egpt_agent:app", reload=True)
