"""
Web Readers: Website, YouTube, ArXiv, Firecrawl
=================================================
Readers for web-based content sources.

Supported web sources:
- WebsiteReader: Crawls web pages and extracts content
- YouTubeReader: Extracts transcripts from YouTube videos
- ArxivReader: Fetches academic papers from ArXiv
- FirecrawlReader: Advanced web scraping via Firecrawl API

See also: 01_documents.py for PDF/DOCX, 02_data.py for CSV/JSON.
"""

import asyncio

from egpt.agent import Agent
from egpt.integrations.knowledge.embedder.openai import OpenAIEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.integrations.knowledge.reader.website_reader import WebsiteReader
from egpt.models.openai import OpenAIResponses
from egpt.vectordb.qdrant import Qdrant
from egpt.vectordb.search import SearchType

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

qdrant_url = "http://localhost:6333"

knowledge = Knowledge(
    vector_db=Qdrant(
        collection="web_readers",
        url=qdrant_url,
        search_type=SearchType.hybrid,
        embedder=OpenAIEmbedder(id="text-embedding-3-small"),
    ),
)

agent = Agent(
    model=OpenAIResponses(id="gpt-5.2"),
    knowledge=knowledge,
    search_knowledge=True,
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    async def main():
        # --- Website: crawl and extract content ---
        print("\n" + "=" * 60)
        print("READER: Website (crawl and extract)")
        print("=" * 60 + "\n")

        # WebsiteReader crawls pages up to max_depth and max_links
        website_reader = WebsiteReader(max_depth=1, max_links=5)
        await knowledge.ainsert(
            name="MCP Docs",
            url="https://modelcontextprotocol.io/docs/getting-started/intro",
            reader=website_reader,
        )
        agent.print_response("What is the Model Context Protocol?", stream=True)

        # --- URL: direct URL loading (auto-detected) ---
        print("\n" + "=" * 60)
        print("READER: Direct URL (auto-detected)")
        print("=" * 60 + "\n")

        # URLs ending in .pdf, .md, .txt etc. are auto-detected
        await knowledge.ainsert(
            name="Recipes",
            url="https://agno-public.s3.amazonaws.com/recipes/ThaiRecipes.pdf",
        )
        agent.print_response("What Thai recipes are available?", stream=True)

    asyncio.run(main())
