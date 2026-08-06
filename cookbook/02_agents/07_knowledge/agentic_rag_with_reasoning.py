"""
Agentic Rag With Reasoning
=============================

Demonstrates agentic RAG with reranking and explicit reasoning tools.
"""

import asyncio

from egpt.agent import Agent
from egpt.knowledge.embedder.cohere import CohereEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.knowledge.reranker.cohere import CohereReranker
from egpt.models.openai import OpenAIResponses
from egpt.tools.reasoning import ReasoningTools
from egpt.vectordb.lancedb import LanceDb, SearchType

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
knowledge = Knowledge(
    # Use LanceDB as the vector database, store embeddings in the `mcp_docs` table
    vector_db=LanceDb(
        uri="tmp/lancedb",
        table_name="mcp_docs",
        search_type=SearchType.hybrid,
        embedder=CohereEmbedder(id="embed-v4.0"),
        reranker=CohereReranker(model="rerank-v3.5"),
    ),
)

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    # Agentic RAG is enabled by default when `knowledge` is provided to the Agent.
    knowledge=knowledge,
    # search_knowledge=True gives the Agent the ability to search on demand
    # search_knowledge is True by default
    search_knowledge=True,
    tools=[ReasoningTools(add_instructions=True)],
    instructions=[
        "You are an expert on the Model Context Protocol (MCP).",
        "Include sources in your response.",
        "Always search your knowledge before answering the question.",
    ],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(
        knowledge.ainsert_many(
            urls=["https://modelcontextprotocol.io/docs/learn/architecture"]
        )
    )
    agent.print_response(
        "What is the Model Context Protocol?",
        stream=True,
        show_full_reasoning=True,
    )
