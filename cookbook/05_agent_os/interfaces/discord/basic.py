"""
Basic Discord Agent
===================

Runs a simple Agno-powered Discord bot.
"""

from egpt.agent import Agent
from egpt.integrations.discord import DiscordClient
from egpt.models.openai import OpenAIChat

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
basic_agent = Agent(
    name="Basic Agent",
    model=OpenAIChat(id="gpt-4o"),
    add_history_to_context=True,
    num_history_runs=3,
    add_datetime_to_context=True,
)

discord_agent = DiscordClient(basic_agent)


# ---------------------------------------------------------------------------
# Run Discord Bot
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    discord_agent.serve()
