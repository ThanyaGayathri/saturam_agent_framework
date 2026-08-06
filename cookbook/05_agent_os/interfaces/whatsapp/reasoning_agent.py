"""
Reasoning Agent
===============

Demonstrates reasoning agent.
"""

from egpt.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.models.anthropic.claude import Claude
from egpt.os.app import EgptOS
from egpt.os.interfaces.whatsapp import Whatsapp
from egpt.tools.reasoning import ReasoningTools
from egpt.tools.yfinance import YFinanceTools

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

agent_db = SqliteDb(db_file="tmp/persistent_memory.db")

reasoning_finance_agent = Agent(
    name="Reasoning Finance Agent",
    model=Claude(id="claude-sonnet-4-6"),
    db=agent_db,
    tools=[
        ReasoningTools(add_instructions=True),
        YFinanceTools(),
    ],
    instructions="Use tables to display data. When you use thinking tools, keep the thinking brief.",
    add_datetime_to_context=True,
    markdown=True,
)


# Setup our EgptOS app
agent_os = EgptOS(
    agents=[reasoning_finance_agent],
    interfaces=[Whatsapp(agent=reasoning_finance_agent, show_reasoning=True)],
)
app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="reasoning_agent:app", reload=True)
