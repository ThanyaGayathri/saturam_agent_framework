"""
Claude Code Agent on EgptOS
============================
A Claude Code agent (via the Claude Agent SDK), served through EgptOS.

The Claude Agent SDK runs Claude Code as a subprocess. Tool execution
is handled by the SDK — you configure which built-in tools are allowed.

Requirements:
    pip install claude-agent-sdk

Usage:
    .venvs/demo/bin/python cookbook/frameworks/00_quickstart/claude_agent.py
"""

from egpt.agents.claude import ClaudeAgent
from egpt.db.sqlite import SqliteDb
from egpt.os import EgptOS

agent = ClaudeAgent(
    name="Claude Code Agent",
    model="claude-sonnet-4-6",
    allowed_tools=["Read", "Edit", "Bash"],
    permission_mode="acceptEdits",
    max_turns=10,
)

agent_os = EgptOS(
    agents=[agent],
    tracing=True,
    db=SqliteDb(db_file="tmp/egptos.db"),
)
app = agent_os.get_app()

if __name__ == "__main__":
    agent_os.serve(app="claude_agent:app", reload=True)
