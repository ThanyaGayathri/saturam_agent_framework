import asyncio

from egpt.agent import Agent
from egpt.integrations.db.postgres.postgres import PostgresDb
from egpt.knowledge.knowledge import Knowledge
from egpt.integrations.knowledge.reader.tavily_reader import TavilyReader
from egpt.models.openai import OpenAIChat
from egpt.vectordb.pgvector import PgVector

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"

# Initialize database and vector store
db = PostgresDb(id="tavily-reader-db", db_url=db_url)

vector_db = PgVector(
    db_url=db_url,
    table_name="tavily_documents",
)

knowledge = Knowledge(
    name="Tavily Extracted Documents",
    contents_db=db,
    vector_db=vector_db,
)


async def main():
    """
    Example demonstrating async TavilyReader usage with Knowledge base integration.

    This example shows:
    1. Adding content from URLs using TavilyReader asynchronously
    2. Integrating with Knowledge base for RAG
    3. Querying the agent with search_knowledge enabled
    """

    # URLs to extract content from
    urls_to_extract = [
        "https://github.com/modelcontextprotocol/modelcontextprotocol",
        "https://docs.tavily.com/documentation/api-reference/endpoint/extract",
    ]

    print("=" * 80)
    print("Adding content to Knowledge base using TavilyReader (async)")
    print("=" * 80)

    # Add content from URLs using TavilyReader
    # Note: Comment out after first run to avoid re-adding the same content
    for url in urls_to_extract:
        print(f"\nExtracting content from: {url}")
        await knowledge.ainsert(
            url,
            reader=TavilyReader(
                extract_format="markdown",
                extract_depth="basic",
                chunk=True,
                chunk_size=3000,
            ),
        )

    print("\n" + "=" * 80)
    print("Creating Agent with Knowledge base")
    print("=" * 80)

    # Create an agent with the knowledge
    agent = Agent(
        model=OpenAIChat(id="gpt-5.2"),
        knowledge=knowledge,
        search_knowledge=True,  # Enable knowledge search
        debug_mode=True,
    )

    print("\n" + "=" * 80)
    print("Querying Agent")
    print("=" * 80)

    # Ask questions about the extracted content
    await agent.aprint_response(
        "What is EGPT and what are its main features based on the documentation?",
        markdown=True,
    )

    print("\n" + "=" * 80)
    print("Second Query")
    print("=" * 80)

    await agent.aprint_response(
        "What is the Tavily Extract API and how does it work?",
        markdown=True,
    )


if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
