from egpt.agent import Agent
from egpt.db.sqlite import SqliteDb
from egpt.os import EgptOS
from egpt.tools.workspace import Workspace

workbench = Agent(
    name="Workbench",
    model="openai:gpt-5.5",
    db=SqliteDb(db_file="workbench.db"),  # session storage
    tools=[Workspace(".")],  # operate in this directory
    enable_agentic_memory=True,  # remembers across sessions
    add_history_to_context=True,  # add past runs to context
    num_history_runs=3,  # last 3 runs
)

# Serve via EgptOS, get streaming, auth, session isolation, API endpoints
agent_os = EgptOS(agents=[workbench], tracing=True)
app = agent_os.get_app()
