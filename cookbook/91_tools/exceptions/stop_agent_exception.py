"""
Stop Agent Exception
=============================

Demonstrates stop agent exception.
"""

from egpt.agent import Agent
from egpt.db.sqlite import SqliteDb
from egpt.exceptions import StopAgentRun
from egpt.models.openai import OpenAIChat
from egpt.run import RunContext
from egpt.utils.log import logger

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


def add_item(run_context: RunContext, item: str) -> str:
    """Add an item to the shopping list."""
    if run_context.session_state is None:
        run_context.session_state = {}

    if "shopping_list" not in run_context.session_state:
        run_context.session_state["shopping_list"] = []

    run_context.session_state["shopping_list"].append(item)
    len_shopping_list = len(run_context.session_state["shopping_list"])

    if len_shopping_list < 3:
        raise StopAgentRun(
            f"Shopping list is: {run_context.session_state['shopping_list']}. We must stop the agent."  # type: ignore
        )

    logger.info(
        f"The shopping list is now: {run_context.session_state.get('shopping_list')}"
    )  # type: ignore
    return f"The shopping list is now: {run_context.session_state.get('shopping_list')}"  # type: ignore


agent = Agent(
    model=OpenAIChat(id="gpt-5.2"),
    session_id="stop_agent_exception_session",
    db=SqliteDb(
        session_table="stop_agent_exception_session",
        db_file="tmp/stop_agent_exception.db",
    ),
    # Initialize the session state with empty shopping list
    session_state={"shopping_list": []},
    tools=[add_item],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response("Add milk", stream=True)
    print(
        f"Final session state: {agent.get_session_state(session_id='stop_agent_exception_session')}"
    )
