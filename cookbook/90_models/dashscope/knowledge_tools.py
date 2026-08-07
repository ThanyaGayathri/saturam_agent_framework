"""
Here is a tool with reasoning capabilities to allow agents to search and analyze information from a knowledge base.

1. Run: `uv pip install openai lancedb sqlalchemy` to install the dependencies
2. Export your OPENAI_API_KEY
3. Run: `cookbook/92_models/dashscope/knowledge_tools.py` to run the agent
"""

from egpt.agent import Agent
from egpt.integrations.knowledge.embedder.openai import OpenAIEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.models.dashscope import DashScope
from egpt.tools.knowledge import KnowledgeTools
from egpt.vectordb.lancedb import LanceDb, SearchType

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

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
    model=DashScope(id="qwen-plus"),
    tools=[knowledge_tools],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent.print_response(
        "How does an MCP client connect to a server?",
        markdown=True,
        stream=True,
    )
