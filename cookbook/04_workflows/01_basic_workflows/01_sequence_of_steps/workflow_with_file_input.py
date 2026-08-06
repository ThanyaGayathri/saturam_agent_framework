"""
Workflow With File Input
========================

Demonstrates passing file inputs through workflow steps for reading and summarization.
"""

from egpt.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.media import File
from egpt.models.anthropic import Claude
from egpt.models.openai import OpenAIChat
from egpt.workflow.step import Step
from egpt.workflow.workflow import Workflow

# ---------------------------------------------------------------------------
# Create Agents
# ---------------------------------------------------------------------------
read_agent = Agent(
    name="Agent",
    model=Claude(id="claude-sonnet-4-20250514"),
    role="Read the contents of the attached file.",
)

summarize_agent = Agent(
    name="Summarize Agent",
    model=OpenAIChat(id="gpt-4o"),
    instructions=[
        "Summarize the contents of the attached file.",
    ],
)

# ---------------------------------------------------------------------------
# Define Steps
# ---------------------------------------------------------------------------
read_step = Step(
    name="Read Step",
    agent=read_agent,
)

summarize_step = Step(
    name="Summarize Step",
    agent=summarize_agent,
)

# ---------------------------------------------------------------------------
# Create Workflow
# ---------------------------------------------------------------------------
content_creation_workflow = Workflow(
    name="Content Creation Workflow",
    description="Automated content creation from blog posts to social media",
    db=SqliteDb(
        session_table="workflow",
        db_file="tmp/workflow.db",
    ),
    steps=[read_step, summarize_step],
)

# ---------------------------------------------------------------------------
# Run Workflow
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    content_creation_workflow.print_response(
        input="Summarize the contents of the attached file.",
        files=[
            File(url="https://agno-public.s3.amazonaws.com/recipes/ThaiRecipes.pdf")
        ],
        markdown=True,
    )
