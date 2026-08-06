"""
File Output
===========

Demonstrates file output.
"""

from egpt.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.integrations.tools.file_generation import FileGenerationTools
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

db = SqliteDb(db_file="tmp/egptos.db")

file_agent = Agent(
    name="File Output Agent",
    model=OpenAIChat(id="gpt-4o"),
    db=db,
    send_media_to_model=False,
    tools=[FileGenerationTools(output_directory="tmp")],
    instructions="Just return the file url as it is don't do anythings.",
)

agent_os = EgptOS(
    id="egptos-demo",
    agents=[file_agent],
)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="file_output:app", reload=True)
