"""
Anthropic Mcp Connector
=======================

Cookbook example for `anthropic/mcp_connector.py`.
"""

from egpt.agent import Agent
from egpt.models.anthropic import Claude
from egpt.utils.models.claude import MCPServerConfiguration

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=Claude(
        id="claude-sonnet-4-20250514",
        betas=["mcp-client-2025-04-04"],
        mcp_servers=[
            MCPServerConfiguration(
                type="url",
                name="deepwiki",
                url="https://mcp.deepwiki.com/sse",
            )
        ],
    ),
    markdown=True,
)

agent.print_response(
    "Tell me about https://github.com/modelcontextprotocol/modelcontextprotocol",
    stream=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
