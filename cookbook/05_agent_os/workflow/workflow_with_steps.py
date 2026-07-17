"""
Workflow With Steps
===================

Demonstrates workflow with steps.
"""

from egpt.agent.agent import Agent
from egpt.db.sqlite import SqliteDb
from egpt.models.openai.chat import OpenAIChat

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------
# Import the workflows
from egpt.os import EgptOS
from egpt.tools.websearch import WebSearchTools
from egpt.workflow.step import Step
from egpt.workflow.steps import Steps
from egpt.workflow.workflow import Workflow

# Define agents for different tasks
researcher = Agent(
    name="Research Agent",
    model=OpenAIChat(id="gpt-4o-mini"),
    tools=[WebSearchTools()],
    instructions="Research the given topic and provide key facts and insights.",
)

writer = Agent(
    name="Writing Agent",
    model=OpenAIChat(id="gpt-4o"),
    instructions="Write a comprehensive article based on the research provided. Make it engaging and well-structured.",
)

editor = Agent(
    name="Editor Agent",
    model=OpenAIChat(id="gpt-4o"),
    instructions="Review and edit the article for clarity, grammar, and flow. Provide a polished final version.",
)

# Define individual steps
research_step = Step(
    name="research",
    agent=researcher,
    description="Research the topic and gather information",
)

writing_step = Step(
    name="writing",
    agent=writer,
    description="Write an article based on the research",
)

editing_step = Step(
    name="editing",
    agent=editor,
    description="Edit and polish the article",
)

# Create a Steps sequence that chains these above steps together
article_creation_sequence = Steps(
    name="article_creation",
    description="Complete article creation workflow from research to final edit",
    steps=[research_step, writing_step, editing_step],
)

article_workflow = Workflow(
    name="Article Creation Workflow",
    description="Automated article creation from research to publication",
    steps=[article_creation_sequence],
    db=SqliteDb(
        session_table="workflow_session",
        db_file="tmp/workflow.db",
    ),
)

# Initialize the EgptOS with the workflows
agent_os = EgptOS(
    description="Example OS setup",
    workflows=[article_workflow],
)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="workflow_with_steps:app", reload=True)
