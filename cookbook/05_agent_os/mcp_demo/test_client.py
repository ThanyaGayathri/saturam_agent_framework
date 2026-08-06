"""
MCP client example: an agent that operates an EgptOS through its MCP server.

First run the EgptOS with the MCP server enabled:

```bash
.venvs/demo/bin/python cookbook/05_agent_os/mcp_demo/enable_mcp_example.py
```

Then run this client in a second terminal. It connects to the EgptOS MCP server
at /mcp and drives it through the 8 built-in tools: discover the OS
(get_agentos_config), run components (run_agent / run_team / run_workflow),
resolve pauses (continue_run), stop runs (cancel_run), and browse conversations
(get_sessions / get_session_runs).
"""

import asyncio
from uuid import uuid4

from egpt.agent import Agent
from egpt.integrations.db.in_memory import InMemoryDb
from egpt.models.openai import OpenAIResponses
from egpt.tools.mcp import MCPTools

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# This is the URL of the MCP server we want to use.
server_url = "http://localhost:7777/mcp"

session_id = f"session_{uuid4()}"


async def run_agent() -> None:
    async with MCPTools(
        transport="streamable-http", url=server_url, timeout_seconds=60
    ) as mcp_tools:
        agent = Agent(
            model=OpenAIResponses(id="gpt-5.5"),
            tools=[mcp_tools],
            instructions=[
                "You operate an EgptOS through its MCP tools.",
                "Call get_agentos_config first to discover the agents, teams, and workflows you can run.",
                "Use the run tools to delegate work, and the session tools to review past conversations.",
            ],
            user_id="john@example.com",
            session_id=session_id,
            db=InMemoryDb(),
            add_session_state_to_context=True,
            add_history_to_context=True,
            markdown=True,
        )

        await agent.aprint_response(
            input="Which agents do I have in my EgptOS?", stream=True, markdown=True
        )

        # await agent.aprint_response(
        #     input="Use my web research agent to find the latest news about AI",
        #     stream=True,
        #     markdown=True,
        # )

        ## Session history
        # await agent.aprint_response(
        #     input="List my recent sessions and summarize what the last conversation was about.",
        #     stream=True,
        #     markdown=True,
        # )


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_agent())
