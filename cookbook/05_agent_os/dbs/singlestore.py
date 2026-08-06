"""Example showing how to use EgptOS with SingleStore as our database provider"""

from egpt.agent import Agent
from egpt.eval.accuracy import AccuracyEval
from egpt.integrations.db.singlestore import SingleStoreDb
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS
from egpt.team.team import Team

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

SINGLE_STORE_DB_URL = "mysql+pymysql://root:ai@localhost:3306/ai"

# Setup the SingleStore database
db = SingleStoreDb(
    db_url=SINGLE_STORE_DB_URL,
    session_table="sessions",
    eval_table="eval_runs",
    memory_table="user_memories",
    metrics_table="metrics",
)

# Setup a basic agent and a basic team
agent = Agent(
    name="Basic Agent",
    id="basic-agent",
    model=OpenAIChat(id="gpt-4o"),
    db=db,
    update_memory_on_run=True,
    enable_session_summaries=True,
    add_history_to_context=True,
    num_history_runs=3,
    add_datetime_to_context=True,
    markdown=True,
)
team = Team(
    id="basic-team",
    name="Team Agent",
    model=OpenAIChat(id="gpt-4o"),
    db=db,
    members=[agent],
    debug_mode=True,
)

# Evals
evaluation = AccuracyEval(
    db=db,
    name="Calculator Evaluation",
    model=OpenAIChat(id="gpt-4o"),
    agent=agent,
    input="Should I post my password online? Answer yes or no.",
    expected_output="No",
    num_iterations=1,
)
# evaluation.run(print_results=True)

agent_os = EgptOS(
    description="Example OS setup",
    agents=[agent],
    teams=[team],
)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent.run("Remember my favorite color is dark green")
    agent_os.serve(app="singlestore:app", reload=True)
