"""
Claude Agent SDK on EgptOS
===========================
Serve a Claude Agent SDK agent through EgptOS -- the same runtime
used for native Agno agents.

The agent is available at the standard /agents/{agent_id}/runs endpoint,
supports streaming (SSE) and non-streaming responses, and appears in
the EgptOS UI alongside any native agents.

Requirements:
    pip install claude-agent-sdk

Usage:
    .venvs/demo/bin/python cookbook/frameworks/claude-agent-sdk/claude_agentos.py

Then call the API:
    # List agents
    curl http://localhost:7777/agents

    # Streaming
    curl -X POST http://localhost:7777/agents/claude-assistant/runs \
        -F "message=What is quantum computing?" \
        -F "stream=true" \
        --no-buffer

    # Non-streaming
    curl -X POST http://localhost:7777/agents/claude-assistant/runs \
        -F "message=What is quantum computing?" \
        -F "stream=false"
"""

from egpt.agents.claude import ClaudeAgent
from egpt.os import EgptOS

# ---------------------------------------------------------------------------
# Create the Claude Agent SDK agent
# ---------------------------------------------------------------------------
claude_agent = ClaudeAgent(
    name="Claude Assistant",
    description="A Claude-powered assistant served through EgptOS",
    model="claude-sonnet-4-6",
    allowed_tools=["Read", "Bash"],
    permission_mode="acceptEdits",
    max_turns=10,
)

# ---------------------------------------------------------------------------
# Setup EgptOS
# ---------------------------------------------------------------------------
agent_os = EgptOS(
    name="Claude Agent SDK Example",
    description="EgptOS serving a Claude Agent SDK agent",
    agents=[claude_agent],
)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent_os.serve(app="claude_agentos:app", reload=True)
