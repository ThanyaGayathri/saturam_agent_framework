"""
Example showing a reasoning Agent in the EgptOS.

You can interact with the Agent as normally. It will reason before providing a final answer.
You will see its chain of thought live as it is generated.
"""

from egpt.agent import Agent
from egpt.models.anthropic import Claude
from egpt.os import EgptOS
from egpt.run.agent import RunEvent  # noqa

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# Create an agent with reasoning enabled
agent = Agent(
    reasoning_model=Claude(
        id="claude-sonnet-4-5",
        thinking={"type": "enabled", "budget_tokens": 1024},
    ),
    reasoning=True,
    instructions="Think step by step about the problem.",
)

# Setup our EgptOS app
agent_os = EgptOS(
    description="Reasoning model streaming",
    agents=[agent],
)
app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Run your EgptOS.

    You can see the configuration and available apps at:
    http://localhost:7777/config

    """
    agent_os.serve(app="reasoning_model:app", reload=True)
