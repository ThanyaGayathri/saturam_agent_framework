"""
EgptOS Knowledge (Sync And Async)
==================================

Demonstrates EgptOS knowledge integration with both sync and async database setups.
"""

import asyncio
from textwrap import dedent

from egpt.agent import Agent
from egpt.db.postgres import AsyncPostgresDb, PostgresDb
from egpt.knowledge.embedder.openai import OpenAIEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS
from egpt.vectordb.pgvector import PgVector, SearchType

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
USE_ASYNC = False

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"

sync_documents_db = PostgresDb(
    db_url=db_url,
    id="mcp_knowledge_db",
    knowledge_table="mcp_knowledge_contents",
)
sync_faq_db = PostgresDb(
    db_url=db_url,
    id="egpt_faq_db",
    knowledge_table="egpt_faq_contents",
)

async_documents_db = AsyncPostgresDb(
    db_url=db_url,
    id="mcp_knowledge_db",
    knowledge_table="mcp_knowledge_contents",
)
async_faq_db = AsyncPostgresDb(
    db_url=db_url,
    id="egpt_faq_db",
    knowledge_table="egpt_faq_contents",
)

sync_documents_knowledge = Knowledge(
    vector_db=PgVector(
        db_url=db_url,
        table_name="mcp_knowledge_vectors",
        search_type=SearchType.hybrid,
        embedder=OpenAIEmbedder(id="text-embedding-3-small"),
    ),
    contents_db=sync_documents_db,
)

sync_faq_knowledge = Knowledge(
    vector_db=PgVector(
        db_url=db_url,
        table_name="egpt_faq_vectors",
        search_type=SearchType.hybrid,
        embedder=OpenAIEmbedder(id="text-embedding-3-small"),
    ),
    contents_db=sync_faq_db,
)

async_documents_knowledge = Knowledge(
    vector_db=PgVector(
        db_url=db_url,
        table_name="mcp_knowledge_vectors",
        search_type=SearchType.hybrid,
        embedder=OpenAIEmbedder(id="text-embedding-3-small"),
    ),
    contents_db=async_documents_db,
)

async_faq_knowledge = Knowledge(
    vector_db=PgVector(
        db_url=db_url,
        table_name="egpt_faq_vectors",
        search_type=SearchType.hybrid,
        embedder=OpenAIEmbedder(id="text-embedding-3-small"),
    ),
    contents_db=async_faq_db,
)

# ---------------------------------------------------------------------------
# Create Agents
# ---------------------------------------------------------------------------
sync_knowledge_agent = Agent(
    name="Knowledge Agent",
    model=OpenAIChat(id="gpt-4o-mini"),
    knowledge=sync_documents_knowledge,
    search_knowledge=True,
    db=sync_documents_db,
    enable_user_memories=True,
    add_history_to_context=True,
    markdown=True,
    instructions=[
        "You are a helpful assistant with access to Model Context Protocol documentation.",
        "Search the knowledge base to answer questions about the Model Context Protocol.",
    ],
)

async_knowledge_agent = Agent(
    name="Knowledge Agent",
    model=OpenAIChat(id="gpt-4o-mini"),
    knowledge=async_documents_knowledge,
    search_knowledge=True,
    db=async_documents_db,
    enable_user_memories=True,
    add_history_to_context=True,
    markdown=True,
    instructions=[
        "You are a helpful assistant with access to Model Context Protocol documentation.",
        "Search the knowledge base to answer questions about the Model Context Protocol.",
    ],
)

# ---------------------------------------------------------------------------
# Create EgptOS
# ---------------------------------------------------------------------------
sync_agent_os = EgptOS(
    description="Example app with EgptOS Knowledge",
    agents=[sync_knowledge_agent],
    knowledge=[sync_faq_knowledge],
)

async_agent_os = EgptOS(
    description="Example app with EgptOS Knowledge (Async)",
    agents=[async_knowledge_agent],
    knowledge=[async_faq_knowledge],
)

agent_os = async_agent_os if USE_ASYNC else sync_agent_os
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if USE_ASYNC:
        asyncio.run(
            async_documents_knowledge.ainsert(
                name="MCP Docs",
                url="https://modelcontextprotocol.io/llms-full.txt",
                skip_if_exists=True,
            )
        )
        asyncio.run(
            async_faq_knowledge.ainsert(
                name="EGPT FAQ",
                text_content=dedent("""
                What is EGPT?
                EGPT is a framework for building agents.
                Use it to build multi-agent systems with memory, knowledge,
                human in the loop and MCP support.
            """),
                skip_if_exists=True,
            )
        )
    else:
        sync_documents_knowledge.insert(
            name="MCP Docs",
            url="https://modelcontextprotocol.io/llms-full.txt",
            skip_if_exists=True,
        )
        sync_faq_knowledge.insert(
            name="EGPT FAQ",
            text_content=dedent("""
            What is EGPT?
            EGPT is a framework for building agents.
            Use it to build multi-agent systems with memory, knowledge,
            human in the loop and MCP support.
        """),
            skip_if_exists=True,
        )

    agent_os.serve(app="egptos_knowledge:app", reload=True)
