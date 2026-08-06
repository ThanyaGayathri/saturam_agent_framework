"""
Support Team
============

A multi-agent team that routes support questions to the right specialist.
Technical Support handles code and API questions; Documentation Specialist
searches Slack history and the web for existing answers.

Key concepts:
  - ``Team`` with a coordinator model routes questions to the best member.
  - One member uses ``SlackTools`` to find past answers in Slack threads.
  - Both members use ``WebSearchTools`` for external documentation.

Slack scopes: app_mentions:read, assistant:write, chat:write, im:history,
             search:read, channels:history

Environment variables:
    SLACK_TOKEN         Bot token (xoxb-) for standard Slack APIs
    SLACK_USER_TOKEN    User token (xoxp-) required for search_messages
"""

from egpt.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.models.openai import OpenAIChat
from egpt.os.app import EgptOS
from egpt.os.interfaces.slack import Slack
from egpt.team import Team
from egpt.tools.slack import SlackTools
from egpt.tools.websearch import WebSearchTools

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

team_db = SqliteDb(session_table="team_sessions", db_file="tmp/support_team.db")

# Technical Support Agent
tech_support = Agent(
    name="Technical Support",
    role="Code and technical troubleshooting",
    model=OpenAIChat(id="gpt-4o"),
    tools=[WebSearchTools()],
    instructions=[
        "You handle technical questions about code, APIs, and implementation.",
        "Provide code examples when helpful.",
        "Search for current documentation and best practices.",
    ],
    markdown=True,
)

# Documentation Agent
docs_agent = Agent(
    name="Documentation Specialist",
    role="Finding and explaining documentation",
    model=OpenAIChat(id="gpt-4o"),
    tools=[
        SlackTools(
            enable_search_messages=True,
            enable_get_thread=True,
        ),
        WebSearchTools(),
    ],
    instructions=[
        "You find relevant documentation and past discussions.",
        "Search Slack for previous answers to similar questions.",
        "Search the web for official documentation.",
        "Explain documentation in simple terms.",
    ],
    markdown=True,
)

# The Team with a coordinator
support_team = Team(
    name="Support Team",
    model=OpenAIChat(id="gpt-4o"),
    members=[tech_support, docs_agent],
    description="A support team that routes questions to the right specialist.",
    instructions=[
        "You coordinate support requests.",
        "Route technical/code questions to Technical Support.",
        "Route 'how do I' or 'where is' questions to Documentation Specialist.",
        "For complex questions, consult both agents.",
    ],
    db=team_db,
    add_history_to_context=True,
    num_history_runs=3,
    markdown=True,
)

agent_os = EgptOS(
    teams=[support_team],
    interfaces=[
        Slack(
            team=support_team,
            reply_to_mentions_only=True,
        )
    ],
)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="support_team:app", reload=True)
