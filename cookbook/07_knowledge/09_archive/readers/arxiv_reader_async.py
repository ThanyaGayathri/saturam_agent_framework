import asyncio

from egpt.agent import Agent
from egpt.knowledge.knowledge import Knowledge
from egpt.knowledge.reader.arxiv_reader import ArxivReader
from egpt.vectordb.pgvector import PgVector

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"

knowledge = Knowledge(
    # Table name: ai.arxiv_documents
    vector_db=PgVector(
        table_name="arxiv_documents",
        db_url=db_url,
    ),
)

# Create an agent with the knowledge
agent = Agent(
    knowledge=knowledge,
    search_knowledge=True,
)


def main():
    # Load the knowledge
    asyncio.run(
        knowledge.ainsert(
            topics=["Generative AI", "Machine Learning"],
            reader=ArxivReader(),
        )
    )

    # Create and use the agent
    asyncio.run(
        agent.aprint_response(
            "What can you tell me about Generative AI?", markdown=True
        )
    )


if __name__ == "__main__":
    main()
