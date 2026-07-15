"""
 Teams
======

Demonstrates  teams.
"""

from egpt.agent import Agent
from egpt.db.postgres import PostgresDb
from egpt.models.anthropic import Claude
from egpt.team.team import Team
from egpt.tools.reasoning import ReasoningTools
from egpt.tools.websearch import WebSearchTools
from egpt.tools.yfinance import YFinanceTools

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"

web_agent = Agent(
    name="Web Search Agent",
    role="Handle web search requests",
    model=Claude(id="claude-3-7-sonnet-latest"),
    db=PostgresDb(db_url=db_url, session_table="web_agent_sessions"),
    tools=[WebSearchTools()],
    instructions=["Always include sources"],
)

finance_agent = Agent(
    name="Finance Agent",
    role="Handle financial data requests",
    model=Claude(id="claude-3-7-sonnet-latest"),
    db=PostgresDb(db_url=db_url, session_table="finance_agent_sessions"),
    tools=[YFinanceTools()],
    instructions=["Use tables to display data"],
)

finance_reasoning_team = Team(
    name="Reasoning Team Leader",
    model=Claude(id="claude-3-7-sonnet-latest"),
    db=PostgresDb(db_url=db_url, session_table="finance_reasoning_team_sessions"),
    members=[
        web_agent,
        finance_agent,
    ],
    tools=[ReasoningTools(add_instructions=True)],
    markdown=True,
    show_members_responses=True,
)

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit("This module is intended to be imported.")
