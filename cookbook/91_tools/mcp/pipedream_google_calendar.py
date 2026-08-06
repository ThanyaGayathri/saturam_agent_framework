"""
Pipedream Google Calendar MCP

This example shows how to use Pipedream MCP servers (in this case the Google Calendar one) with EGPT Agents.

1. Connect your Pipedream and Google Calendar accounts: https://mcp.pipedream.com/app/google_calendar
2. Get your Pipedream MCP server url: https://mcp.pipedream.com/app/google_calendar
3. Set the MCP_SERVER_URL environment variable to the MCP server url you got above
4. Install dependencies: uv pip install mcp
"""

import asyncio
import os

from egpt.agent import Agent
from egpt.models.openai import OpenAIChat
from egpt.tools.mcp import MCPTools
from egpt.utils.log import log_exception

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


mcp_server_url = os.getenv("MCP_SERVER_URL")


async def run_agent(task: str) -> None:
    try:
        async with MCPTools(
            url=mcp_server_url, transport="sse", timeout_seconds=20
        ) as mcp:
            agent = Agent(
                model=OpenAIChat(id="gpt-5.2"),
                tools=[mcp],
                markdown=True,
            )
            await agent.aprint_response(input=task, stream=True)
    except Exception as e:
        log_exception(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(
        run_agent("Tell me about all events I have in my calendar for tomorrow")
    )
