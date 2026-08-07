"""Run `uv pip install ddgs sqlalchemy pgvector pypdf openai` to install dependencies."""

import asyncio

from egpt.agent import Agent
from egpt.integrations.knowledge.embedder.azure_openai import AzureOpenAIEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.models.azure import AzureOpenAI
from egpt.vectordb.pgvector import PgVector

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"

knowledge = Knowledge(
    vector_db=PgVector(
        table_name="recipes",
        db_url=db_url,
        embedder=AzureOpenAIEmbedder(),
    ),
)
# Add content to the knowledge
asyncio.run(
    knowledge.ainsert(
        url="https://agno-public.s3.amazonaws.com/recipes/ThaiRecipes.pdf"
    )
)

agent = Agent(
    model=AzureOpenAI(id="gpt-5.2"),
    knowledge=knowledge,
)
agent.print_response("How to make Thai curry?", markdown=True)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
