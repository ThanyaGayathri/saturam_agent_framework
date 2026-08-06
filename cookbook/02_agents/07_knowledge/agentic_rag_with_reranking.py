"""
Agentic Rag With Reranking
=============================

1. Run: `uv pip install openai cohere lancedb sqlalchemy` to install the dependencies.
"""

from egpt.agent import Agent
from egpt.knowledge.embedder.openai import OpenAIEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.knowledge.reranker.cohere import CohereReranker
from egpt.models.openai import OpenAIResponses
from egpt.vectordb.lancedb import LanceDb, SearchType

knowledge = Knowledge(
    # Use LanceDB as the vector database and store embeddings in the `mcp_docs` table
    vector_db=LanceDb(
        uri="tmp/lancedb",
        table_name="mcp_docs",
        search_type=SearchType.hybrid,
        embedder=OpenAIEmbedder(
            id="text-embedding-3-small"
        ),  # Use OpenAI for embeddings
        reranker=CohereReranker(
            model="rerank-multilingual-v3.0"
        ),  # Use Cohere for reranking
    ),
)

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    # Agentic RAG is enabled by default when `knowledge` is provided to the Agent.
    knowledge=knowledge,
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    knowledge.insert(
        name="MCP Docs",
        url="https://modelcontextprotocol.io/docs/getting-started/intro",
    )
    agent.print_response("What are the key features of the Model Context Protocol?")
