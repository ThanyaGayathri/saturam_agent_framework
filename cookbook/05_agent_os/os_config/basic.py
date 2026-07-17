"""
Basic
=====

Demonstrates basic.
"""

from egpt.agent import Agent
from egpt.db.postgres import PostgresDb
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS
from egpt.os.config import (
    DatabaseConfig,
    EgptOSConfig,
    Manifest,
    MemoryConfig,
    MemoryDomainConfig,
)
from egpt.team import Team
from egpt.workflow.step import Step
from egpt.workflow.workflow import Workflow

# Setup the database
db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai", id="db-0001")
db2 = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai2", id="db-0002")

# ---------------------------------------------------------------------------
# Create Agent, Team, And Workflow
# ---------------------------------------------------------------------------
basic_agent = Agent(
    name="Marketing Agent",
    db=db,
    enable_session_summaries=True,
    update_memory_on_run=True,
    add_history_to_context=True,
    num_history_runs=3,
    add_datetime_to_context=True,
    markdown=True,
)
basic_team = Team(
    id="basic-team",
    name="Basic Team",
    model=OpenAIChat(id="gpt-4o"),
    db=db,
    members=[basic_agent],
    update_memory_on_run=True,
)
basic_workflow = Workflow(
    id="basic-workflow",
    name="Basic Workflow",
    description="Just a simple workflow",
    db=db2,
    steps=[
        Step(
            name="step1",
            description="Just a simple step",
            agent=basic_agent,
        )
    ],
)

# Setup our EgptOS app
agent_os = EgptOS(
    description="Your EgptOS",
    id="basic-os",
    agents=[basic_agent],
    teams=[basic_team],
    workflows=[basic_workflow],
    # Configuration for the EgptOS
    config=EgptOSConfig(
        manifest={
            "marketing-agent": Manifest(
                description="Plans, runs and reports on marketing campaigns.",
                labels=["beta", "marketing"],
                quick_prompts=[
                    "What can you do?",
                    "How is our latest post working?",
                    "Tell me about our active marketing campaigns",
                ],
            ),
        },
        memory=MemoryConfig(
            dbs=[
                DatabaseConfig(
                    db_id=db.id,
                    domain_config=MemoryDomainConfig(
                        display_name="Main app user memories",
                    ),
                )
            ],
        ),
    ),
)
app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Run your EgptOS.

    You can see the configuration and available endpoints at:
    http://localhost:7777/config
    """
    agent_os.serve(app="basic:app", reload=True)
