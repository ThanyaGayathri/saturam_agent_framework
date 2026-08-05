"""SurrealDB + EgptOS demo

Steps:
    1. Run SurrealDB in a container: `./cookbook/scripts/run_surrealdb.sh`
    2. Run the demo: `python cookbook/agent_os/dbs/surreal_db/run.py`
"""

from agents import egpt_assist
from egpt.os import EgptOS
from teams import reasoning_finance_team
from workflows import research_workflow

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# ************* Create the EgptOS *************
agent_os = EgptOS(
    description="SurrealDB EgptOS",
    agents=[egpt_assist],
    teams=[reasoning_finance_team],
    workflows=[research_workflow],
)
# Get the FastAPI app for the EgptOS
app = agent_os.get_app()
# *******************************

# ************* Run the EgptOS *************
# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="run:app", reload=True)
# *******************************
