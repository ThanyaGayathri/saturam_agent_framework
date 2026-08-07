"""
Knowledge Tools
===============

Demonstrates this reasoning cookbook example.
"""

from egpt.agent import Agent
from egpt.integrations.knowledge.embedder.openai import OpenAIEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.models.openai import OpenAIChat
from egpt.tools.knowledge import KnowledgeTools
from egpt.vectordb.lancedb import LanceDb, SearchType


# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------
def run_example() -> None:
    # Create a knowledge containing information from a URL
    mcp_docs = Knowledge(
        # Use LanceDB as the vector database and store embeddings in the `mcp_docs` table
        vector_db=LanceDb(
            uri="tmp/lancedb",
            table_name="mcp_docs",
            search_type=SearchType.hybrid,
            embedder=OpenAIEmbedder(id="text-embedding-3-small"),
        ),
    )
    # Add content to the knowledge
    mcp_docs.insert(url="https://modelcontextprotocol.io/llms-full.txt")

    knowledge_tools = KnowledgeTools(
        knowledge=mcp_docs,
        enable_think=True,
        enable_search=True,
        enable_analyze=True,
        add_few_shot=True,
    )

    agent = Agent(
        model=OpenAIChat(id="gpt-4o"),
        tools=[knowledge_tools],
        markdown=True,
    )

    if __name__ == "__main__":
        agent.print_response(
            "How does an MCP client connect to a server?",
            markdown=True,
            stream=True,
        )


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_example()
