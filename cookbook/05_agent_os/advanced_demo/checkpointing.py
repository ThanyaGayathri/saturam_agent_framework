from egpt.agent import Agent
from egpt.integrations.db.postgres import PostgresDb
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS
from egpt.tools.websearch import WebSearchTools

db = PostgresDb(db_url="postgresql+psycopg://ai:ai@localhost:5532/ai")

research_agent = Agent(
    name="Research Agent",
    checkpoint="tool-batch",
    id="research_agent",
    model=OpenAIChat(id="gpt-5.2"),
    instructions=["You are a research agent"],
    tools=[WebSearchTools()],
    db=db,
)

agent_os = EgptOS(
    id="checkpointing-demo",
    name="Checkpointing Demo",
    description="A demo of checkpointing in EgptOS",
    agents=[research_agent],
)

app = agent_os.get_app()

if __name__ == "__main__":
    agent_os.serve(app="checkpointing:app", reload=True)
