"""Example for EgptOS to show how to pass dependencies to an agent."""

from egpt.agent import Agent
from egpt.integrations.db.postgres import PostgresDb
from egpt.os import EgptOS

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# Setup the database
db = PostgresDb(id="basic-db", db_url="postgresql+psycopg://ai:ai@localhost:5532/ai")

# Setup basic agents, teams and workflows
story_writer = Agent(
    id="story-writer-agent",
    name="Story Writer Agent",
    db=db,
    markdown=True,
    instructions="You are a story writer. You are asked to write a story about a robot. Always name the robot {robot_name}",
)

# Setup our EgptOS app
agent_os = EgptOS(
    description="Example EgptOS to show how to pass dependencies to an agent",
    agents=[story_writer],
)
app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Run your EgptOS.

    Test passing dependencies to an agent:
    curl --location 'http://localhost:7777/agents/story-writer-agent/runs' \
        --header 'Content-Type: application/x-www-form-urlencoded' \
        --data-urlencode 'message=Write me a 5 line story.' \
        --data-urlencode 'dependencies={"robot_name": "Anna"}'
    """
    agent_os.serve(app="pass_dependencies_to_agent:app", reload=True)
