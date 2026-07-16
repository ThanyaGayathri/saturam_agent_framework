"""Basic Workflow Factory -- per-tenant content pipeline.

Demonstrates a WorkflowFactory that builds a multi-step workflow
with tenant-specific instructions. The workflow steps are constructed
fresh on each request.

Run:
    .venvs/demo/bin/python cookbook/05_agent_os/factories/workflow/01_basic_workflow_factory.py

Test:
    curl -X POST http://localhost:7777/workflows/content-pipeline/runs \
        -F 'message=Write a blog post about sustainable energy' \
        -F 'user_id=tenant_42' \
        -F 'stream=false'
"""

from egpt.agent import Agent
from egpt.db.postgres import PostgresDb
from egpt.factory import RequestContext
from egpt.models.openai import OpenAIResponses
from egpt.os import EgptOS
from egpt.workflow.factory import WorkflowFactory
from egpt.workflow.step import Step
from egpt.workflow.workflow import Workflow

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

db = PostgresDb(
    id="workflow-factory-db",
    db_url="postgresql+psycopg://ai:ai@localhost:5532/ai",
)

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_content_pipeline(ctx: RequestContext) -> Workflow:
    """Build a content pipeline workflow tailored to the calling tenant."""
    user_id = ctx.user_id or "anonymous"

    drafter = Agent(
        name="Drafter",
        model=OpenAIResponses(id="gpt-5.4"),
        instructions=(
            f"You are a content drafter for tenant {user_id}. "
            "Write a first draft based on the topic. Keep it focused and concise."
        ),
    )

    editor = Agent(
        name="Editor",
        model=OpenAIResponses(id="gpt-5.4"),
        instructions=(
            f"You are an editor for tenant {user_id}. "
            "Review the draft for clarity, grammar, and structure. Output the final version."
        ),
    )

    return Workflow(
        name="Content Pipeline",
        description="Draft then edit content",
        db=db,
        steps=[
            Step(name="draft", description="Write the first draft", agent=drafter),
            Step(name="edit", description="Edit and finalize", agent=editor),
        ],
    )


content_pipeline_factory = WorkflowFactory(
    db=db,
    id="content-pipeline",
    name="Content Pipeline",
    description="Builds a draft-then-edit content workflow per tenant",
    factory=build_content_pipeline,
)

# ---------------------------------------------------------------------------
# EgptOS
# ---------------------------------------------------------------------------

agent_os = EgptOS(
    id="workflow-factory-demo",
    description="Demo: basic workflow factory",
    workflows=[content_pipeline_factory],
)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="01_basic_workflow_factory:app", port=7777, reload=True)
