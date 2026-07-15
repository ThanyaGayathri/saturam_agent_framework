"""
Groq Agent Team
===============

Cookbook example for `groq/agent_team.py`.
"""

from egpt.agent import Agent
from egpt.models.groq import Groq
from egpt.team import Team
from egpt.tools.websearch import WebSearchTools
from egpt.tools.yfinance import YFinanceTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

web_agent = Agent(
    name="Web Agent",
    role="Search the web for information",
    model=Groq(id="llama-3.3-70b-versatile"),
    tools=[WebSearchTools()],
    instructions="Always include sources",
    markdown=True,
)

finance_agent = Agent(
    name="Finance Agent",
    role="Get financial data",
    model=Groq(id="llama-3.3-70b-versatile"),
    tools=[YFinanceTools()],
    instructions="Use tables to display data",
    markdown=True,
)

agent_team = Team(
    members=[web_agent, finance_agent],
    model=Groq(
        id="llama-3.3-70b-versatile"
    ),  # You can use a different model for the team leader agent
    instructions=["Always include sources", "Use tables to display data"],
    markdown=True,
    show_members_responses=False,  # Comment to hide responses from team members
)

# Give the team a task
agent_team.print_response(
    input="Summarize the latest news about Nvidia and share its stock price?",
    stream=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
