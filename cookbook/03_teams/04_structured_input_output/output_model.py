"""
Output Model
============

Demonstrates setting a dedicated model for final team response generation.
"""

from egpt.agent import Agent
from egpt.models.openai import OpenAIResponses
from egpt.team import Team
from egpt.tools.websearch import WebSearchTools

# ---------------------------------------------------------------------------
# Create Members
# ---------------------------------------------------------------------------
itinerary_planner = Agent(
    name="Itinerary Planner",
    model=OpenAIResponses(id="gpt-5.2"),
    description="You help people plan amazing vacations. Use the tools at your disposal to find latest information about the destination.",
    tools=[WebSearchTools()],
)

# ---------------------------------------------------------------------------
# Create Team
# ---------------------------------------------------------------------------
travel_expert = Team(
    model=OpenAIResponses(id="gpt-5-mini"),
    members=[itinerary_planner],
    output_model=OpenAIResponses(id="gpt-5-mini"),
)

# ---------------------------------------------------------------------------
# Run Team
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    travel_expert.print_response("Plan a summer vacation in Paris", stream=True)
