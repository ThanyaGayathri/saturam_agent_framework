"""
Stop After Tool Call
=============================

Demonstrates stop after tool call.
"""

from egpt.agent import Agent
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.openai import OpenAIChat

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


agent = Agent(
    model=OpenAIChat(id="gpt-5.2"),
    tools=[
        WebSearchTools(
            stop_after_tool_call_tools=["web_search"],
            show_result_tools=["web_search"],
        )
    ],
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response("Whats the latest about gpt 5?", markdown=True)
