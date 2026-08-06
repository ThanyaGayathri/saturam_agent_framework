"""
MCP Docs Server
=============================

Demonstrates connecting an agent to the Model Context Protocol docs MCP server.
"""

import asyncio

from egpt.agent import Agent
from egpt.models.anthropic import Claude
from egpt.tools.mcp import MCPTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


async def run_agent(message: str) -> None:
    async with MCPTools(
        transport="streamable-http", url="https://modelcontextprotocol.io/mcp"
    ) as mcp_docs_server:
        agent = Agent(
            model=Claude(id="claude-sonnet-4-5"),
            tools=[mcp_docs_server],
            markdown=True,
        )
        await agent.aprint_response(input=message, stream=True)


# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_agent("What is the Model Context Protocol?"))
