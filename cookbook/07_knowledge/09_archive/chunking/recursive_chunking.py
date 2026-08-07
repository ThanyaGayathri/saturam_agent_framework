from egpt.agent import Agent
from egpt.knowledge.chunking.recursive import RecursiveChunking
from egpt.knowledge.knowledge import Knowledge
from egpt.integrations.knowledge.reader.pdf_reader import PDFReader
from egpt.vectordb.pgvector import PgVector

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"

knowledge = Knowledge(
    vector_db=PgVector(table_name="recipes_recursive_chunking", db_url=db_url),
)

knowledge.insert(
    url="https://agno-public.s3.amazonaws.com/recipes/ThaiRecipes.pdf",
    reader=PDFReader(
        name="Recursive Chunking Reader",
        chunking_strategy=RecursiveChunking(),
    ),
)

agent = Agent(
    knowledge=knowledge,
    search_knowledge=True,
)

agent.print_response("How to make Thai curry?", markdown=True)
