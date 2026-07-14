"""
Knowledge Tools: Think, Search, Analyze
=========================================
KnowledgeTools provides a richer set of tools for knowledge interaction
beyond basic search:

- think: Agent reasons about the query before searching
- search: Standard knowledge base search
- analyze: Deep analysis of search results

This gives agents more sophisticated reasoning over knowledge.
"""

import asyncio

from egpt.agent import Agent
from egpt.knowledge.embedder.openai import OpenAIEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.models.openai import OpenAIChat
from egpt.tools.knowledge import KnowledgeTools
from egpt.vectordb.qdrant import Qdrant
from egpt.vectordb.search import SearchType

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

qdrant_url = "http://localhost:6333"

knowledge = Knowledge(
    vector_db=Qdrant(
        collection="knowledge_tools_demo",
        url=qdrant_url,
        search_type=SearchType.hybrid,
        embedder=OpenAIEmbedder(id="text-embedding-3-small"),
    ),
)

knowledge_tools = KnowledgeTools(
    knowledge=knowledge,
    enable_think=True,
    enable_search=True,
    enable_analyze=True,
    add_few_shot=True,
)

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=[knowledge_tools],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    async def main():
        await knowledge.ainsert(url="https://docs.agno.com/llms-full.txt")

        print("\n" + "=" * 60)
        print("KnowledgeTools: think + search + analyze")
        print("=" * 60 + "\n")

        agent.print_response(
            "How do I build a team of agents in Agno?",
            stream=True,
        )

    asyncio.run(main())
