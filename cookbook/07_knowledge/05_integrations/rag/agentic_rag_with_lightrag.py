"""
Agentic Rag With Lightrag
=============================

Demonstrates an agentic RAG flow backed by LightRAG (relocated integration example).
"""

import asyncio
from os import getenv

from egpt.agent import Agent
from egpt.knowledge.knowledge import Knowledge
from egpt.knowledge.reader.wikipedia_reader import WikipediaReader
from egpt.vectordb.lightrag import LightRag

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
vector_db = LightRag(api_key=getenv("LIGHTRAG_API_KEY"))

knowledge = Knowledge(
    name="My LightRag Knowledge Base",
    description="Knowledge base using a LightRag vector database",
    vector_db=vector_db,
)

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    knowledge=knowledge,
    search_knowledge=True,
    read_chat_history=False,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(
        knowledge.ainsert(
            name="Recipes",
            path="cookbook/07_knowledge/testing_resources/cv_1.pdf",
            metadata={"doc_type": "recipe_book"},
        )
    )

    asyncio.run(
        knowledge.ainsert(
            name="Recipes",
            topics=["Manchester United"],
            reader=WikipediaReader(),
        )
    )

    asyncio.run(
        knowledge.ainsert(
            name="Recipes",
            url="https://en.wikipedia.org/wiki/Manchester_United_F.C.",
        )
    )

    asyncio.run(
        agent.aprint_response("What skills does Jordan Mitchell have?", markdown=True)
    )

    asyncio.run(
        agent.aprint_response(
            "In what year did Manchester United change their name?", markdown=True
        )
    )
