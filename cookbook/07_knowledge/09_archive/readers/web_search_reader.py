from egpt.agent import Agent
from egpt.integrations.db.postgres.postgres import PostgresDb
from egpt.knowledge.knowledge import Knowledge
from egpt.integrations.knowledge.reader.web_search_reader import WebSearchReader
from egpt.models.openai import OpenAIChat
from egpt.vectordb.pgvector import PgVector

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"


db = PostgresDb(id="web-search-db", db_url=db_url)

vector_db = PgVector(
    db_url=db_url,
    table_name="web_search_documents",
)
knowledge = Knowledge(
    name="Web Search Documents",
    contents_db=db,
    vector_db=vector_db,
)


# Load knowledge from web search
knowledge.insert(
    topics=["egpt"],
    reader=WebSearchReader(
        max_results=3,
        search_engine="duckduckgo",
        chunk=True,
    ),
)

# Create an agent with the knowledge
agent = Agent(
    model=OpenAIChat(id="gpt-5.2"),
    knowledge=knowledge,
    search_knowledge=True,
    debug_mode=True,
)

# Ask the agent about the knowledge
agent.print_response(
    "What are the latest AI trends according to the search results?", markdown=True
)
