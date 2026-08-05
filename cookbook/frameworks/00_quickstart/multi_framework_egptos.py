"""
Multi-Framework EgptOS — EGPT + Claude Code
============================================
Serve a Claude Code agent (via the Claude Agent SDK) and an EGPT agent
through a single EgptOS. They share one database, one API surface,
and one UI.

This is the example from the launch blog post:
    "EgptOS now supports Claude Code, LangGraph, and DSPy"

Requirements:
    pip install claude-agent-sdk

Usage:
    .venvs/demo/bin/python cookbook/frameworks/00_quickstart/multi_framework_egptos.py

Then call the API:
    # List agents
    curl http://localhost:7777/agents

    # Run the EGPT agent
    curl -X POST http://localhost:7777/agents/egpt-agent/runs \\
        -F "message=List the Python files in this directory" \\
        -F "stream=true" --no-buffer

    # Run the Claude Code agent
    curl -X POST http://localhost:7777/agents/claude-code-agent/runs \\
        -F "message=Read README.md and summarize it" \\
        -F "stream=true" --no-buffer
"""

from egpt.agent import Agent
from egpt.agents.claude import ClaudeAgent
from egpt.db.sqlite import SqliteDb
from egpt.os import EgptOS
from egpt.tools.workspace import Workspace

# ---------------------------------------------------------------------------
# Claude Code agent (via the Claude Agent SDK)
# ---------------------------------------------------------------------------
claude_agent = ClaudeAgent(
    name="Claude Code Agent",
    model="claude-sonnet-4-6",
    allowed_tools=["Read", "Edit", "Bash"],
    permission_mode="acceptEdits",
    max_turns=10,
)

# ---------------------------------------------------------------------------
# EGPT agent
# ---------------------------------------------------------------------------
egpt_agent = Agent(
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

# ---------------------------------------------------------------------------
# Serve both through one EgptOS
# ---------------------------------------------------------------------------
agent_os = EgptOS(
    agents=[egpt_agent, claude_agent],
    tracing=True,
    db=SqliteDb(db_file="tmp/egptos.db"),
)
app = agent_os.get_app()

if __name__ == "__main__":
    agent_os.serve(app="multi_framework_egptos:app", reload=True)
