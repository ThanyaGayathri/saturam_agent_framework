"""Run `uv pip install ddgs` to install dependencies."""

from egpt.agent import Agent
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.langdb import LangDB

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=LangDB(id="llama3-1-70b-instruct-v1.0"),
    tools=[WebSearchTools()],
    markdown=True,
)
agent.print_response("Whats happening in France?", stream=True)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
