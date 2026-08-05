from egpt.agent import Agent
from egpt.knowledge.chunking.code import CodeChunking
from egpt.knowledge.knowledge import Knowledge
from egpt.knowledge.reader.text_reader import TextReader
from egpt.vectordb.pgvector import PgVector

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"

knowledge = Knowledge(
    vector_db=PgVector(table_name="python_code_chunking", db_url=db_url),
)

# Add code with CodeChunking
knowledge.insert(
    url="https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/libs/agno/egpt/session/workflow.py",
    reader=TextReader(
        chunking_strategy=CodeChunking(
            tokenizer="gpt2", chunk_size=500, language="python", include_nodes=False
        ),
    ),
)

# Query with agent
agent = Agent(knowledge=knowledge, search_knowledge=True)
agent.print_response("How does the Workflow class work?", markdown=True)
