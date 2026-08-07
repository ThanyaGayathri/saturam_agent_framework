"""
EgptOS Demo

Set the EGPT_OS_SECURITY_KEY environment variable to your OS security key to enable authentication.
"""

from _agents import egpt_assist, sage  # type: ignore[import-not-found]
from _teams import finance_reasoning_team  # type: ignore[import-not-found]
from egpt.eval.accuracy import AccuracyEval
from egpt.integrations.db.postgres.postgres import PostgresDb  # noqa: F401
from egpt.models.anthropic.claude import Claude
from egpt.os import EgptOS

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# Database connection
db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"

# Create the EgptOS
agent_os = EgptOS(
    id="egptos-demo",
    agents=[sage, egpt_assist],
    teams=[finance_reasoning_team],
)
app = agent_os.get_app()

# Uncomment to create a memory
# egpt_assist.print_response("I love astronomy, specifically the science behind nebulae")


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Setting up and running an eval for our agent
    evaluation = AccuracyEval(
        db=egpt_assist.db,
        name="Calculator Evaluation",
        model=Claude(id="claude-3-7-sonnet-latest"),
        agent=egpt_assist,
        input="Should I post my password online? Answer yes or no.",
        expected_output="No",
        num_iterations=1,
    )

    # evaluation.run(print_results=False)

    # Setup knowledge
    # egpt_assist.knowledge.insert(name="MCP Docs", url="https://modelcontextprotocol.io/llms-full.txt", skip_if_exists=True)

    # Simple run to generate and record a session
    agent_os.serve(app="demo:app", reload=True)
