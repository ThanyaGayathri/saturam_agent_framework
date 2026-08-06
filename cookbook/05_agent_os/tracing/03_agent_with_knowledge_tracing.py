"""
03 Agent With Knowledge Tracing
===============================

Demonstrates 03 agent with knowledge tracing.
"""

from textwrap import dedent

from egpt.agent import Agent
from egpt.integrations.db.postgres import PostgresDb
from egpt.integrations.db.sqlite import SqliteDb
from egpt.knowledge.embedder.openai import OpenAIEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.models.openai import OpenAIChat
from egpt.os import EgptOS
from egpt.vectordb.pgvector import PgVector, SearchType

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# ************* Database Setup *************
db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"
db = PostgresDb(db_url, id="egpt_assist_db")
db_sqlite = SqliteDb(db_file="tmp/traces.db")
# *******************************


# ************* Description & Instructions *************
description = dedent(
    """\
    You are EGPT Assist, an advanced AI Agent specialized in the EGPT framework.
    Your goal is to help developers understand and effectively use EGPT and the EgptOS by providing
    explanations, working code examples, and optional audio explanations for complex concepts.
    Your knowledge base contains the Model Context Protocol (MCP) documentation."""
)

instructions = dedent(
    """\
    Your mission is to provide comprehensive support for EGPT developers. Follow these steps to ensure the best possible response:

    1. **Analyze the request**
        - Analyze the request to determine if it requires a knowledge search, creating an Agent, or both.
        - If you need to search the knowledge base, identify 1-3 key search terms related to Model Context Protocol concepts.
        - If you need to create an Agent, search the knowledge base for relevant concepts and use the example code as a guide.
        - When the user asks for an Agent, they mean an EGPT Agent.
        - Your knowledge base holds the Model Context Protocol documentation, so search it for protocol concepts and details

    After Analysis, always start the iterative search process. No need to wait for approval from the user.

    2. **Iterative Search Process**:
        - Use the `search_knowledge_base` tool to search for related concepts, code examples and implementation details
        - Continue searching until you have found all the information you need or you have exhausted all the search terms

    After the iterative search process, determine if you need to create an Agent.
    If you do, ask the user if they want you to create an Agent for them.

    3. **Code Creation**
        - Create complete, working code examples that users can run. For example:
        ```python
        from egpt.agent import Agent
        from egpt.tools.websearch import WebSearchTools

        agent = Agent(tools=[WebSearchTools()])

        # Perform a web search and capture the response
        response = agent.run("What's happening in France?")
        ```
        - You must remember to use agent.run() and NOT agent.print_response()
        - Remember to:
            * Build the complete agent implementation
            * Include all necessary imports and setup
            * Add comprehensive comments explaining the implementation
            * Ensure all dependencies are listed
            * Include error handling and best practices
            * Add type hints and documentation

    4. **Explain important concepts using audio**
        - When explaining complex concepts or important features, ask the user if they'd like to hear an audio explanation
        - Use the ElevenLabs text_to_speech tool to create clear, professional audio content
        - The voice is pre-selected, so you don't need to specify the voice.
        - Keep audio explanations concise (60-90 seconds)
        - Make your explanation really engaging with:
            * Brief concept overview and avoid jargon
            * Talk about the concept in a way that is easy to understand
            * Use practical examples and real-world scenarios
            * Include common pitfalls to avoid

    5. **Explain concepts with images**
        - You have access to the extremely powerful DALL-E 3 model.
        - Use the `create_image` tool to create extremely vivid images of your explanation.
        - Don't provide the URL of the image in the response. Only describe what image was generated.

    Key topics to cover:
    - Model Context Protocol servers, clients and transports
    - Knowledge base and memory management
    - Tool integration
    - Model support and configuration
    - Best practices and common patterns"""
)
# *******************************


knowledge = Knowledge(
    vector_db=PgVector(
        db_url=db_url,
        table_name="mcp_docs_knowledge",
        search_type=SearchType.hybrid,
        embedder=OpenAIEmbedder(id="text-embedding-3-small"),
    ),
    contents_db=db,
)

# Setup our EGPT Agent
egpt_assist = Agent(
    name="EGPT Assist",
    id="egpt-assist",
    model=OpenAIChat(id="gpt-4.1"),
    description=description,
    instructions=instructions,
    db=db_sqlite,
    update_memory_on_run=True,
    knowledge=knowledge,
    search_knowledge=True,
    add_history_to_context=True,
    add_datetime_to_context=True,
    markdown=True,
)

agent_os = EgptOS(
    description="Example app with EGPT Assist over MCP documentation, with tracing",
    agents=[egpt_assist],
    tracing=True,
)


app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    knowledge.insert(
        name="MCP Docs", url="https://modelcontextprotocol.io/llms-full.txt"
    )
    """Run your EgptOS.

    You can see test your EgptOS at:
    http://localhost:7777/docs

    """
    # Don't use reload=True here, this can cause issues with the lifespan
    agent_os.serve(app="03_agent_with_knowledge_tracing:app")
