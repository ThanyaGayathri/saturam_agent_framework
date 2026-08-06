"""
Multiple Knowledge Bases
========================

Demonstrates multiple knowledge bases.
"""

from egpt.agent import Agent
from egpt.integrations.db.json import JsonDb
from egpt.knowledge.knowledge import Knowledge
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS
from egpt.vectordb.pgvector import PgVector

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"


vector_db = PgVector(table_name="vectors", db_url=db_url)
secondary_vector_db = PgVector(table_name="more_vectors", db_url=db_url)
contents_db = JsonDb(db_path="./egpt_json_data", knowledge_table="main_knowledge")
secondary_contents_db = JsonDb(
    db_path="./egpt_json_data_2", knowledge_table="secondary_knowledge"
)

# Create knowledge bases
knowledge_base = Knowledge(
    name="Main Knowledge Base",
    description="A simple knowledge base",
    contents_db=contents_db,
    vector_db=vector_db,
)

main_agent = Agent(
    name="Main Agent",
    model=OpenAIChat(id="gpt-4o"),
    knowledge=knowledge_base,
    add_datetime_to_context=True,
    markdown=True,
    db=contents_db,
)

agent_os = EgptOS(
    description="Example app for basic agent with knowledge capabilities",
    id="knowledge-demo",
    agents=[main_agent],
)
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """ Run your EgptOS:
    Now you can interact with your knowledge base using the API. Examples:
    - http://localhost:8001/knowledge/{id}/documents
    - http://localhost:8001/knowledge/{id}/documents/123
    - http://localhost:8001/knowledge/{id}/documents?agent_id=123
    - http://localhost:8001/knowledge/{id}/documents?limit=10&page=0&sort_by=created_at&sort_order=desc
    """
    agent_os.serve(app="multiple_knowledge_bases:app", reload=True)
