"""
LLMs.txt Tools - Agentic Documentation Discovery
=============================

Demonstrates how to use LLMsTxtTools in agentic mode where the agent:
1. Reads the llms.txt index to discover available documentation pages
2. Decides which pages are relevant to the user's question
3. Fetches only the specific pages it needs

The llms.txt format (https://llmstxt.org) is a standardized way for websites
to provide LLM-friendly documentation indexes.
"""

from egpt.agent import Agent
from egpt.integrations.tools.llms_txt import LLMsTxtTools
from egpt.models.openai import OpenAIResponses

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=OpenAIResponses(id="gpt-5.4"),
    tools=[LLMsTxtTools()],
    instructions=[
        "You can read llms.txt files to discover documentation for any project.",
        "First use get_llms_txt_index to see what pages are available.",
        "Then use read_llms_txt_url to fetch only the pages relevant to the user's question.",
    ],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response(
        "Using the llms.txt at https://modelcontextprotocol.io/llms.txt, "
        "find and read the documentation about how to create an agent with tools",
        markdown=True,
        stream=True,
    )
