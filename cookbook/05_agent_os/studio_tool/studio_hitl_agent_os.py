"""StudioTools + human-in-the-loop served through EgptOS.

Same HITL studio agent as studio_hitl_agent.py, but running behind EgptOS:
the run pauses surface through the EgptOS API and chat UI instead of the
console. When the studio agent is missing details it pauses with a structured
multi-select question (ask_user) or a free-text input request
(get_user_input), and create_agent always pauses for explicit confirmation
before anything is persisted. The EgptOS UI renders each pause and continues
the run with the user's response.

Try in the chat UI:
    - "Create an agent called 'research-buddy'."  (the agent must ask for
      tools and instructions, then ask you to confirm the create call)
    - "What models and tools do we have available?"

Usage:
    # Start the EgptOS server
    .venvs/demo/bin/python cookbook/05_agent_os/studio_tool/studio_hitl_agent_os.py
"""

from pathlib import Path

from egpt.agent import Agent
from egpt.db.sqlite import SqliteDb
from egpt.models.anthropic import Claude
from egpt.models.openai import OpenAIResponses
from egpt.os import EgptOS
from egpt.registry import Registry
from egpt.tools.calculator import CalculatorTools
from egpt.tools.duckduckgo import DuckDuckGoTools
from egpt.tools.hackernews import HackerNewsTools
from egpt.tools.studio import StudioTools
from egpt.tools.user_control_flow import UserControlFlowTools
from egpt.tools.user_feedback import UserFeedbackTools

DB_DIR = Path(__file__).parent / "tmp"
DB_DIR.mkdir(exist_ok=True)

db = SqliteDb(id="studio-hitl-os-db", db_file=str(DB_DIR / "studio_hitl_os.db"))

registry = Registry(
    name="Studio HITL Registry",
    tools=[DuckDuckGoTools(), HackerNewsTools(), CalculatorTools()],
    models=[
        OpenAIResponses(id="gpt-5.5"),
        Claude(id="claude-sonnet-4-6"),
    ],
    dbs=[db],
)

studio_agent = Agent(
    id="studio-hitl-agent",
    name="Studio HITL",
    model=OpenAIResponses(id="gpt-5.5"),
    tools=[
        StudioTools(
            registry=registry,
            db=db,
            default_model_id="gpt-5.5",
            # Pause the run for explicit user approval before persisting.
            requires_confirmation_tools=["create_agent"],
        ),
        # Lets the agent ask structured multiple-choice questions.
        UserFeedbackTools(),
        # Lets the agent pause the run and ask for free-text details.
        UserControlFlowTools(),
    ],
    instructions=[
        "You help the user compose new agents from registry primitives.",
        "Call list_tools and list_models first so you know what is available.",
        "NEVER guess or invent missing details.",
        "If the user did not say which tools the new agent needs, use ask_user with one "
        "multi_select question whose options are ONLY the exact tool names returned by "
        "list_tools -- never include your own toolkits as options.",
        "If the user did not provide instructions for the new agent, use get_user_input "
        "to ask for them as free text.",
        "Only call create_agent once the user has supplied tools and instructions, and "
        "pass the exact tool names the user chose.",
        "After creating a component, report its id, name, model and tools.",
    ],
    db=db,
    markdown=True,
)

agent_os = EgptOS(
    id="studio-hitl-agent-os",
    description="Studio agent with human-in-the-loop composition",
    agents=[studio_agent],
    registry=registry,
    db=db,
)
app = agent_os.get_app()


if __name__ == "__main__":
    agent_os.serve(app="studio_hitl_agent_os:app", port=7777, reload=True)
