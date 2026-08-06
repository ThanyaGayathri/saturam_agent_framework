"""Basic Team Factory -- per-tenant team with role-based members.

Demonstrates a TeamFactory that builds a team with different members
depending on the caller's context. The team mode and member composition
vary per request.

Run:
    .venvs/demo/bin/python cookbook/05_agent_os/factories/team/01_basic_team_factory.py

Test:
    curl -X POST http://localhost:7777/teams/support-team/runs \
        -F 'message=I need help with billing and a technical issue' \
        -F 'user_id=tenant_42' \
        -F 'stream=false'
"""

from egpt.agent import Agent
from egpt.factory import RequestContext
from egpt.integrations.db.postgres import PostgresDb
from egpt.models.openai import OpenAIResponses
from egpt.os import EgptOS
from egpt.team.factory import TeamFactory
from egpt.team.team import Team

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

db = PostgresDb(
    id="team-factory-db",
    db_url="postgresql+psycopg://ai:ai@localhost:5532/ai",
)

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_support_team(ctx: RequestContext) -> Team:
    """Build a support team tailored to the calling tenant."""
    user_id = ctx.user_id or "anonymous"

    billing_agent = Agent(
        name="Billing Agent",
        role="Handle billing inquiries",
        model=OpenAIResponses(id="gpt-5.4"),
        instructions=f"You handle billing questions for tenant {user_id}. Be concise.",
    )

    tech_agent = Agent(
        name="Tech Support Agent",
        role="Handle technical issues",
        model=OpenAIResponses(id="gpt-5.4"),
        instructions=f"You handle technical support for tenant {user_id}. Be concise.",
    )

    return Team(
        name="Support Team",
        model=OpenAIResponses(id="gpt-5.4"),
        members=[billing_agent, tech_agent],
        db=db,
        instructions=[
            f"You are the support team leader for tenant {user_id}.",
            "Route billing questions to the Billing Agent and technical issues to the Tech Support Agent.",
        ],
        markdown=True,
    )


support_team_factory = TeamFactory(
    db=db,
    id="support-team",
    name="Per-tenant Support Team",
    description="Builds a support team with billing and tech agents per tenant",
    factory=build_support_team,
)

# ---------------------------------------------------------------------------
# EgptOS
# ---------------------------------------------------------------------------

agent_os = EgptOS(
    id="team-factory-demo",
    description="Demo: basic team factory",
    teams=[support_team_factory],
)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="01_basic_team_factory:app", port=7777, reload=True)
