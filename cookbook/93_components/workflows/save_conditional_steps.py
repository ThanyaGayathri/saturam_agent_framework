"""
Save Conditional Workflow Steps
===============================

Demonstrates creating a workflow with conditional steps, saving it to the
database, and loading it back with a Registry.
"""

from egpt.agent import Agent
from egpt.integrations.db.postgres import PostgresDb
from egpt.integrations.tools.hackernews import HackerNewsTools
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.registry import Registry
from egpt.workflow.condition import Condition
from egpt.workflow.step import Step
from egpt.workflow.types import StepInput
from egpt.workflow.workflow import Workflow, get_workflow_by_id

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
# Database
db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"
db = PostgresDb(db_url=db_url)

# ---------------------------------------------------------------------------
# Create Agents
# ---------------------------------------------------------------------------
# Agents
hackernews_agent = Agent(
    name="HackerNews Researcher",
    instructions="Research tech news and trends from Hacker News",
    tools=[HackerNewsTools()],
)

web_agent = Agent(
    name="Web Researcher",
    instructions="Research general information from the web",
    tools=[WebSearchTools()],
)

content_agent = Agent(
    name="Content Creator",
    instructions="Create well-structured content from research data",
)


# ---------------------------------------------------------------------------
# Create Registry Components
# ---------------------------------------------------------------------------
# Evaluator function (will be serialized by name and restored via registry)
def is_tech_topic(step_input: StepInput) -> bool:
    """Returns True to execute the conditional steps, False to skip."""
    topic = step_input.input or step_input.previous_step_content or ""
    tech_keywords = [
        "ai",
        "machine learning",
        "programming",
        "software",
        "tech",
        "startup",
        "coding",
    ]
    is_tech = any(keyword in topic.lower() for keyword in tech_keywords)
    print(f"Condition: Topic is {'tech' if is_tech else 'not tech'}")
    return is_tech


# Registry (required to restore the evaluator function when loading)
registry = Registry(
    name="Condition Workflow Registry",
    functions=[is_tech_topic],
)

# ---------------------------------------------------------------------------
# Create Workflow Steps
# ---------------------------------------------------------------------------
# Steps
research_hackernews_step = Step(
    name="ResearchHackerNews",
    description="Research tech news from Hacker News",
    agent=hackernews_agent,
)

research_web_step = Step(
    name="ResearchWeb",
    description="Research general information from web",
    agent=web_agent,
)

write_step = Step(
    name="WriteContent",
    description="Write the final content based on research",
    agent=content_agent,
)

# ---------------------------------------------------------------------------
# Create Workflow
# ---------------------------------------------------------------------------
# Workflow
workflow = Workflow(
    name="Conditional Research Workflow",
    description="Conditionally research from HackerNews for tech topics",
    steps=[
        Condition(
            name="TechTopicCondition",
            description="Check if topic is tech-related for HackerNews research",
            evaluator=is_tech_topic,
            steps=[research_hackernews_step],
        ),
        research_web_step,
        write_step,
    ],
    db=db,
)

# ---------------------------------------------------------------------------
# Run Workflow Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Save
    print("Saving workflow...")
    version = workflow.save(db=db)
    print(f"Saved workflow as version {version}")

    # Load
    print("\nLoading workflow...")
    loaded_workflow = get_workflow_by_id(
        db=db,
        id="conditional-research-workflow",
        registry=registry,
    )

    if loaded_workflow:
        print("Workflow loaded successfully!")
        print(f"  Name: {loaded_workflow.name}")
        print(f"  Steps: {len(loaded_workflow.steps) if loaded_workflow.steps else 0}")

        # Uncomment to run the loaded workflow
        # loaded_workflow.print_response(input="Latest AI developments in machine learning", stream=True)
    else:
        print("Workflow not found")
