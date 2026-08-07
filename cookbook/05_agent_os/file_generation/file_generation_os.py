"""File generation with EgptOS.

This example serves an agent that generates files (JSON, CSV, PDF, DOCX, TXT, HTML)
through EgptOS using the FileGenerationTools toolkit.

Run:
    .venvs/demo/bin/python cookbook/05_agent_os/file_generation/file_generation_os.py

Then open http://localhost:7777/docs, connect to the local server, and chat with the agent, e.g.:
    "Generate a DOCX report on Q4 sales trends."
    "Generate a PDF about renewable energy."
    "Generate a CSV of 5 fictional employees."
    "Generate an HTML landing page for a coffee shop."

Generated files are returned as base64-encoded artifacts in the EgptOS response
and saved to tmp/file_gen_out/.
"""

from egpt.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.integrations.tools.file_generation import FileGenerationTools
from egpt.models.openai import OpenAIResponses
from egpt.os.app import EgptOS

agent_db = SqliteDb(db_file="tmp/file_gen_os.db")

file_agent = Agent(
    name="File Generator",
    model=OpenAIResponses(id="gpt-5.4"),
    db=agent_db,
    tools=[
        FileGenerationTools(
            all=True,
            output_directory="tmp/file_gen_out",
        )
    ],
    description="You generate files (JSON, CSV, PDF, DOCX, TXT, HTML) on request.",
    instructions=[
        "When asked to create a file, pick the right generator tool for the requested format.",
        "Always provide meaningful content and a descriptive filename.",
        "When generating HTML files, produce a complete HTML5 document with doctype, html, head, and body tags.",
        "Briefly explain what was generated.",
    ],
    markdown=True,
    debug_mode=True,
    add_history_to_context=True,
    num_history_runs=3,
)

agent_os = EgptOS(agents=[file_agent])
app = agent_os.get_app()

if __name__ == "__main__":
    agent_os.serve(app="file_generation_os:app", reload=True)
