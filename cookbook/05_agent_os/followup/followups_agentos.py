from egpt.agent import Agent
from egpt.db.postgres import PostgresDb
from egpt.models.openai import OpenAIResponses
from egpt.os import EgptOS
from egpt.team.team import Team

db = PostgresDb(db_url="postgresql://ai:ai@localhost:5532/ai")

# ---------------------------------------------------------------------------
# Create the Agent — just set followups=True
# ---------------------------------------------------------------------------
agent = Agent(
    name="Followups Agent",
    id="followup-suggestions-agent",
    model=OpenAIResponses(id="gpt-4o"),
    instructions="You are a knowledgeable assistant. Answer questions thoroughly.",
    # Enable built-in followups
    followups=True,
    num_followups=3,
    # Optionally use a cheaper model for followups
    # followup_model=OpenAIResponses(id="gpt-4o-mini"),
    markdown=True,
    db=db,
)
team = Team(
    id="followups-team",
    name="Followups Team",
    model=OpenAIResponses(id="gpt-4o"),
    members=[agent],
    instructions="You are a knowledgeable assistant. Answer questions thoroughly.",
    # Enable built-in followups
    followups=True,
    num_followups=3,
    # Optionally use a cheaper model for followups
    # followup_model=OpenAIResponses(id="gpt-4o-mini"),
    markdown=True,
    db=db,
)

agno_os = EgptOS(
    id="followups-agentos",
    name="Followups EgptOS",
    agents=[agent],
    teams=[team],
    db=db,
)
app = agno_os.get_app()
if __name__ == "__main__":
    agno_os.serve(app="followups_agentos:app", reload=True)
