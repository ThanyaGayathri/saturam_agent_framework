"""Seltz Tools Example.

Run `pip install seltz openai python-dotenv` to install dependencies.
"""

from dotenv import load_dotenv
from egpt.agent import Agent
from egpt.integrations.tools.seltz import SeltzTools
from egpt.models.openai import OpenAIResponses

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


load_dotenv()

agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[SeltzTools(show_results=True)],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent.print_response("Search for current AI safety reports", markdown=True)
