"""
Redis With Cohere Reranker
==========================

Demonstrates Redis vector retrieval with Cohere reranking.
"""

from egpt.agent import Agent
from egpt.knowledge.embedder.openai import OpenAIEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.knowledge.reranker.cohere import CohereReranker
from egpt.models.openai import OpenAIChat
from egpt.vectordb.redis import RedisDB

# ---------------------------------------------------------------------------
# Create Knowledge Base
# ---------------------------------------------------------------------------
knowledge = Knowledge(
    vector_db=RedisDB(
        index_name="mcp_docs",
        redis_url="redis://localhost:6379",
        embedder=OpenAIEmbedder(id="text-embedding-3-small"),
        reranker=CohereReranker(model="rerank-multilingual-v3.0"),
    ),
)


# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    model=OpenAIChat(id="gpt-5.2"),
    knowledge=knowledge,
    markdown=True,
)


# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
def main() -> None:
    knowledge.insert(
        name="MCP Docs",
        url="https://modelcontextprotocol.io/docs/getting-started/intro",
    )
    agent.print_response("What are the key features of the Model Context Protocol?")


if __name__ == "__main__":
    main()
