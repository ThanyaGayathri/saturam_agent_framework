"""EgptOS HITL: Confirmation Required

EgptOS equivalent of cookbook/03_teams/20_human_in_the_loop/confirmation_required.py

A team member's tool requires human confirmation before execution.
When the tool is called the run pauses and the API response contains the
requirement. The client confirms or rejects, then calls continue_run.

EgptOS handles streaming and async automatically, so this single server
covers the sync, async, streaming, and async-streaming variants.

Run:
    .venvs/demo/bin/python cookbook/05_agent_os/hitl/confirmation_required.py
"""

from egpt.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.models.openai import OpenAIResponses
from egpt.os import EgptOS
from egpt.team import Team
from egpt.tools import tool

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

db = SqliteDb(
    db_file="tmp/agent_os_hitl.db",
    session_table="hitl_confirmation_sessions",
)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool(requires_confirmation=True)
def get_the_weather(city: str) -> str:
    """Get the current weather for a city."""
    return f"It is currently 70 degrees and cloudy in {city}"


# ---------------------------------------------------------------------------
# Create members
# ---------------------------------------------------------------------------

weather_agent = Agent(
    name="WeatherAgent",
    model=OpenAIResponses(id="gpt-5-mini"),
    tools=[get_the_weather],
    instructions=(
        "You MUST call the get_the_weather tool to answer any weather question. "
        "Do NOT answer from your own knowledge."
    ),
    db=db,
    telemetry=False,
)

# ---------------------------------------------------------------------------
# Create team
# ---------------------------------------------------------------------------

team = Team(
    id="hitl-confirmation-team",
    name="WeatherTeam",
    model=OpenAIResponses(id="gpt-5-mini"),
    members=[weather_agent],
    instructions="Delegate all weather questions to the WeatherAgent immediately.",
    db=db,
    telemetry=False,
    add_history_to_context=True,
)

# ---------------------------------------------------------------------------
# Create EgptOS
# ---------------------------------------------------------------------------

agent_os = EgptOS(
    id="hitl-confirmation-required",
    description="EgptOS HITL: member tool requiring confirmation before execution",
    agents=[weather_agent],
    teams=[team],
)

app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="confirmation_required:app", port=7777, reload=True)
