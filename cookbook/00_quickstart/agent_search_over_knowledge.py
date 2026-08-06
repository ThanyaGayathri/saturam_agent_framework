"""
Agentic Search over Knowledge - Agent with a Knowledge Base
============================================================
This example shows how to give an agent a searchable knowledge base.
The agent can search through documents (PDFs, text, URLs) to answer questions.

Key concepts:
- Knowledge: A searchable collection of documents (PDFs, text, URLs)
- Agentic search: The agent decides when to search the knowledge base
- Hybrid search: Combines semantic similarity with keyword matching.

Example prompts to try:
- "What is the Model Context Protocol?"
- "What is an MCP server?"
"""

from egpt.agent import Agent
from egpt.integrations.db.sqlite import SqliteDb
from egpt.knowledge.embedder.google import GeminiEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.models.google import Gemini
from egpt.vectordb.chroma import ChromaDb
from egpt.vectordb.search import SearchType

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
agent_db = SqliteDb(db_file="tmp/agents.db")

knowledge = Knowledge(
    name="MCP Documentation",
    vector_db=ChromaDb(
        name="mcp_docs",
        collection="mcp_docs",
        path="tmp/chromadb",
        persistent_client=True,
        # Enable hybrid search - combines vector similarity with keyword matching using RRF
        search_type=SearchType.hybrid,
        # RRF (Reciprocal Rank Fusion) constant - controls ranking smoothness.
        # Higher values (e.g., 60) give more weight to lower-ranked results,
        # Lower values make top results more dominant. Default is 60 (per original RRF paper).
        hybrid_rrf_k=60,
        embedder=GeminiEmbedder(id="gemini-embedding-001"),
    ),
    # Return 5 results on query
    max_results=5,
    # Store metadata about the contents in the agent database, table_name="egpt_knowledge"
    contents_db=agent_db,
)

# ---------------------------------------------------------------------------
# Agent Instructions
# ---------------------------------------------------------------------------
instructions = """\
You are an expert on the Model Context Protocol (MCP) and building AI agents.

## Workflow

1. Search
   - For questions about MCP, always search your knowledge base first
   - Extract key concepts from the query to search effectively

2. Synthesize
   - Combine information from multiple search results
   - Prioritize official documentation over general knowledge

3. Present
   - Lead with a direct answer
   - Include code examples when helpful
   - Keep it practical and actionable

## Rules

- Always search knowledge before answering MCP questions
- If the answer isn't in the knowledge base, say so
- Include code snippets for implementation questions
- Be concise — developers want answers, not essays\
"""

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent_with_knowledge = Agent(
    name="Agent with Knowledge",
    model=Gemini(id="gemini-3.5-flash"),
    instructions=instructions,
    knowledge=knowledge,
    search_knowledge=True,
    db=agent_db,
    add_datetime_to_context=True,
    add_history_to_context=True,
    num_history_runs=5,
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Load the introduction from the Model Context Protocol documentation into the knowledge base
    # We're only loading 1 file to keep this example simple.
    knowledge.insert(
        name="MCP Introduction",
        url="https://modelcontextprotocol.io/docs/getting-started/intro",
    )

    agent_with_knowledge.print_response(
        "What is the Model Context Protocol?",
        stream=True,
    )

# ---------------------------------------------------------------------------
# More Examples
# ---------------------------------------------------------------------------
"""
Load your own knowledge:

1. From a URL
   knowledge.insert(url="https://example.com/docs.pdf")

2. From a local file
   knowledge.insert(path="path/to/document.pdf")

3. From text directly
   knowledge.insert(text_content="Your content here...")

Hybrid search combines:
- Semantic search: Finds conceptually similar content
- Keyword search: Finds exact term matches
- Results fused using Reciprocal Rank Fusion (RRF)

The agent automatically searches when relevant (agentic search).
"""
