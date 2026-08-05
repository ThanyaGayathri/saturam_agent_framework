import asyncio

from egpt.agent import Agent
from egpt.knowledge.knowledge import Knowledge
from egpt.knowledge.reader.markdown_reader import MarkdownReader
from egpt.vectordb.pgvector import PgVector

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"

# Create a knowledge base with markdown content
knowledge = Knowledge(
    vector_db=PgVector(
        table_name="markdown_documents",
        db_url=db_url,
    )
)

# Create an agent with the knowledge base
agent = Agent(
    knowledge=knowledge,
    search_knowledge=True,
)

if __name__ == "__main__":
    asyncio.run(
        knowledge.ainsert(
            url="https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/README.md",
            reader=MarkdownReader(),
        )
    )
    # Create and use the agent
    asyncio.run(
        agent.aprint_response(
            "What can you tell me about EGPT?",
            markdown=True,
        )
    )
